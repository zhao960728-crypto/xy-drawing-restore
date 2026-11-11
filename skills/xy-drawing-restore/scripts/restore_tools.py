#!/usr/bin/env python3
# Copyright 2026 XY (GitHub: zhao960728-crypto)
# SPDX-License-Identifier: Apache-2.0
"""Local source extraction, DXF measurements and previews. Not an OCR/model engine."""
from __future__ import annotations
import argparse
from collections import Counter
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sys

EXTENSIONS = {'.pdf', '.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff', '.webp'}
RASTER_TYPES = {'IMAGE', 'PDFUNDERLAY', 'DWFUNDERLAY', 'DGNUNDERLAY', 'OLE2FRAME', 'ACAD_PROXY_ENTITY'}
TEXT_TYPES = {'TEXT', 'MTEXT', 'ATTRIB', 'ATTDEF'}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def json_default(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return {'bytes_hex': value.hex()}
    raise TypeError(f'Unsupported JSON value: {type(value).__name__}')


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2,
                               default=json_default, allow_nan=False) + '\n', encoding='utf-8')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def protect_output(path, inputs):
    destination = Path(path).resolve()
    for item in inputs:
        source = Path(item).resolve()
        if destination == source or (destination.exists() and source.exists() and destination.samefile(source)):
            raise ValueError('Output must not overwrite an input file: ' + str(destination))


def asset_path(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Manifest paths must stay inside the source workspace.')
    return path


def embedded_images(page):
    """List PDF image occurrences, without persisting image streams."""
    return [
        {'id': f'image-{index:03}', 'box_points': [image[key] for key in ('x0', 'top', 'x1', 'bottom')],
         'size_pixels': list(image['srcsize']), 'image_mask': bool(image.get('imagemask'))}
        for index, image in enumerate(page.images, 1)
    ]


def prepare(args):
    from PIL import Image, ImageOps
    root = Path(args.out).resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError('Source output directory must be new or empty; existing inputs are never overwritten.')
    paths = [Path(item).resolve() for item in args.inputs]
    for path in paths:
        if not path.is_file() or path.suffix.lower() not in EXTENSIONS or not path.stat().st_size:
            raise ValueError(f'Unsupported, missing or empty source: {path}')
        if path.is_relative_to(root):
            raise ValueError('Place input files outside the new output directory.')
    for folder in ('originals', 'pages', 'native'):
        (root / folder).mkdir(parents=True, exist_ok=True)
    manifest = {'schema': 'xy-local-restoration-source-v1', 'sources': [], 'pages': [],
                'physical_scale_verified': False}
    for index, path in enumerate(paths, 1):
        source_id = f's{index}'
        copied = root / 'originals' / (source_id + path.suffix.lower())
        shutil.copyfile(path, copied)
        source = {'id': source_id, 'name': path.name, 'sha256': sha(copied),
                  'path': copied.relative_to(root).as_posix(), 'pages': 0}
        if copied.suffix == '.pdf':
            import pdfplumber
            import pypdfium2 as pdfium
            rendered = pdfium.PdfDocument(str(copied))
            try:
                with pdfplumber.open(str(copied)) as native:
                    if len(rendered) != len(native.pages):
                        raise ValueError('PDF parsers disagree about page count; inspect the source.')
                    for page_index, page in enumerate(native.pages):
                        page_id = f'{source_id}-p{page_index + 1}'
                        png = root / 'pages' / (page_id + '.png')
                        extracted = root / 'native' / (page_id + '.json')
                        native_data = {'coordinate_system': 'pdfplumber; top/bottom from top; y0/y1 from bottom',
                                       'width_points': page.width, 'height_points': page.height,
                                       'rotation': page.rotation, 'cropbox': page.cropbox,
                                       'text': page.extract_text() or '', 'chars': page.chars,
                                       'lines': page.lines, 'rects': page.rects, 'curves': page.curves,
                                       'embedded_images': embedded_images(page)}
                        write_json(extracted, native_data)
                        rendered_page = rendered[page_index]
                        try:
                            width, height = rendered_page.get_size()
                            scale = min(2.0, math.sqrt(args.max_pixels / max(1.0, width * height)))
                            bitmap = rendered_page.render(scale=scale)
                            try:
                                bitmap.to_pil().save(png)
                            finally:
                                bitmap.close()
                        finally:
                            rendered_page.close()
                        manifest['pages'].append({'id': page_id, 'source_id': source_id,
                                                  'page': page_index + 1,
                                                  'preview': png.relative_to(root).as_posix(),
                                                  'preview_sha256': sha(png),
                                                  'native': extracted.relative_to(root).as_posix(),
                                                  'native_sha256': sha(extracted),
                                                  'render_size_points': [width, height]})
                        source['pages'] += 1
            finally:
                rendered.close()
        else:
            with Image.open(copied) as image:
                for frame in range(getattr(image, 'n_frames', 1)):
                    image.seek(frame)
                    upright = ImageOps.exif_transpose(image.copy()).convert('RGB')
                    page_id = f'{source_id}-p{frame + 1}'
                    png = root / 'pages' / (page_id + '.png')
                    # Preserve source pixels; the source manifest deliberately makes no scale claim.
                    upright.save(png)
                    manifest['pages'].append({'id': page_id, 'source_id': source_id, 'page': frame + 1,
                                              'preview': png.relative_to(root).as_posix(),
                                              'preview_sha256': sha(png), 'size_pixels': list(upright.size)})
                    source['pages'] += 1
        manifest['sources'].append(source)
    write_json(root / 'manifest.json', manifest)
    return {'manifest': str(root / 'manifest.json'), 'sha256': sha(root / 'manifest.json'),
            'pages': len(manifest['pages']), 'physical_scale_verified': False}


def load_dxf(path):
    import ezdxf
    return ezdxf.readfile(str(path))


def bounds(entities):
    from ezdxf import bbox
    result = bbox.extents(entities, fast=False)
    if not result.has_data:
        raise ValueError('Selected entities have no measurable bounds.')
    values = [float(result.extmin.x), float(result.extmin.y), float(result.extmax.x), float(result.extmax.y)]
    if not all(math.isfinite(value) for value in values):
        raise ValueError('Entity bounds contain non-finite values.')
    return values


def measurement(entities, kind):
    if not entities:
        raise ValueError('A scale anchor must select actual entities.')
    if kind in {'bbox_width', 'bbox_height'}:
        b = bounds(entities)
        return b[2] - b[0] if kind == 'bbox_width' else b[3] - b[1]
    if len(entities) != 1:
        raise ValueError(f'{kind} requires exactly one entity.')
    entity = entities[0]
    if kind == 'line_length' and entity.dxftype() == 'LINE':
        return float((entity.dxf.end - entity.dxf.start).magnitude)
    if kind == 'circle_diameter' and entity.dxftype() == 'CIRCLE':
        return float(entity.dxf.radius * 2)
    if kind == 'arc_radius' and entity.dxftype() == 'ARC':
        return float(entity.dxf.radius)
    if kind == 'dimension_measurement' and entity.dxftype() == 'DIMENSION':
        if (int(entity.dxf.dimtype) & 15) not in {0, 1, 3, 4}:
            raise ValueError('Only linear, radial and diameter dimensions can be millimeter anchors.')
        from restoration_geometry import dimension_measurement
        return dimension_measurement(entity)
    raise ValueError(f'Unsupported anchor/entity combination: {kind}/{entity.dxftype()}')


def inspect_dxf(args):
    protect_output(args.out, [args.dxf])
    doc = load_dxf(args.dxf)
    rows = []
    for entity in doc.modelspace():
        row = {'handle': entity.dxf.handle, 'type': entity.dxftype(), 'layer': entity.dxf.layer}
        try:
            row['bbox_xy'] = bounds([entity])
        except ValueError as error:
            row['bounds_note'] = str(error)
        kind = entity.dxftype()
        if kind == 'LINE':
            row.update(start=list(entity.dxf.start), end=list(entity.dxf.end),
                       length_mm=measurement([entity], 'line_length'))
        if kind in {'CIRCLE', 'ARC'}:
            row.update(center=list(entity.dxf.center), radius_mm=float(entity.dxf.radius))
        if kind == 'TEXT':
            row['text'] = entity.dxf.text
        if kind == 'MTEXT':
            row['text'] = entity.plain_text()
        if kind == 'DIMENSION':
            try:
                from restoration_geometry import dimension_measurement
                unit = 'degrees' if entity.dimtype in {2, 5} else 'mm'
                row['measured_' + unit] = dimension_measurement(entity)
            except (ValueError, NotImplementedError) as error:
                row['measurement_note'] = str(error)
        rows.append(row)
    output = {'dxf_sha256': sha(args.dxf), 'units': int(doc.units),
              'counts': dict(Counter(row['type'] for row in rows)), 'entities': rows}
    write_json(args.out, output)
    return {'inspection': str(Path(args.out).resolve()), 'entities': len(rows), 'units': int(doc.units)}


def finite_number(value, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Expected a finite numeric value.')
    if (positive and value <= 0) or (not positive and value < 0):
        raise ValueError('Expected a positive value or nonnegative tolerance.')
    return float(value)


def page_native_dimensions(entities, handles):
    """Follow real block references, retaining saved dimension handles."""
    todo = [entities[handle] for handle in handles]
    visited_blocks = set()
    dimensions = {}
    while todo:
        entity = todo.pop()
        if entity.dxftype() == 'DIMENSION':
            dimensions[entity.dxf.handle] = entity
        elif entity.dxftype() == 'INSERT':
            block = entity.block()
            if block is not None and block.name not in visited_blocks:
                visited_blocks.add(block.name)
                todo.extend(block)
    return dimensions


def feet_inches_mm(text):
    normalized = text.translate(str.maketrans({'′': "'", '’': "'", '″': '"', '”': '"', '–': '-'}))
    match = re.fullmatch(r"""\s*(?:(\d+)\s*'\s*-?\s*)?(?:(\d+(?:[.,]\d+)?)(?!\s*/)[\s-]*)?(?:(\d+\s*/\s*\d+)\s*)?(")?\s*""",
                         normalized)
    if not match or (not match[1] and not match[4]) or (not any(match[i] for i in (2, 3)) and match[4]):
        raise ValueError('Expected a visible feet-and-inches dimension.')
    try:
        inches = Fraction((match[2] or '0').replace(',', '.'))
        inches += Fraction(re.sub(r'\s+', '', match[3])) if match[3] else 0
        return float((int(match[1] or 0) * 12 + inches) * Fraction('25.4'))
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError('Invalid feet-and-inches dimension.') from error


def metric_label(text):
    match = re.fullmatch(
        r'(?:(\d+)\s*[-x×]\s*)?(?:(M|[RØΦ⌀]|%%c)\s*)?([+-]?\d+(?:[.,]\d+)?)\s*(mm|°)?',
        text.strip(), re.IGNORECASE)
    if match is None:
        return None
    quantity, prefix, value, suffix = match.groups()
    prefix = prefix.upper() if prefix else None
    qualifier = prefix if prefix in {'M', 'R'} else 'diameter' if prefix else None
    unit = 'degrees' if suffix == '°' else 'mm' if suffix else None
    return float(value.replace(',', '.')), qualifier, int(quantity) if quantity is not None else None, unit


def check_dimension_inventory(page, dimensions, task_tolerance, findings):
    from restoration_geometry import dimension_measurement

    label = str(page.get('id'))
    inventory = page.get('dimensions', [])
    if not isinstance(inventory, list):
        findings.append(label + ': invalid source dimension inventory.')
        return []
    seen, readings = set(), []
    for row in inventory:
        try:
            handle = row['handle']
            if handle in seen or handle not in dimensions:
                raise ValueError('Dimension handle is missing, repeated, or belongs to a different page.')
            seen.add(handle)
            entity = dimensions[handle]
            unit = 'degrees' if entity.dimtype in {2, 5} else 'mm'
            source_text = row.get('source_text')
            if row.get('unit') != unit or not isinstance(source_text, str) or not source_text.strip():
                raise ValueError('Record the source annotation text and correct measurement unit.')
            expected = finite_number(row['source_reading'], positive=True)
            limit = .05 if unit == 'degrees' else task_tolerance
            tolerance = finite_number(row.get('tolerance', limit))
            if tolerance > limit:
                raise ValueError('Dimension tolerance exceeds the allowed check tolerance.')
            style = entity.override()
            factor = 1 if unit == 'degrees' else finite_number(style.get('dimlfac', 1), positive=True)
            display_unit = row.get('display_unit')
            if display_unit == 'ft-in':
                if unit != 'mm' or style.get('dimlunit', 2) not in {3, 4}:
                    raise ValueError('Feet-and-inches labels need a native engineering or architectural dimension style.')
                if not math.isclose(feet_inches_mm(source_text), expected, abs_tol=1e-8, rel_tol=0):
                    raise ValueError('Source feet-and-inches text differs from the recorded millimeter reading.')
                factor *= 25.4
            elif display_unit is not None:
                raise ValueError('Unsupported dimension display unit.')
            if not math.isclose(factor, 1, abs_tol=1e-12, rel_tol=0) and not str(row.get('projection_basis', '')).strip():
                raise ValueError('An explicitly projected dimension needs a source projection basis.')
            actual = dimension_measurement(entity)
            error = abs(actual*factor-expected)
            display = entity.get_geometry_block()
            visible = [e.plain_text().strip() for e in display
                       if e.dxftype() in {'TEXT', 'MTEXT'} and e.plain_text().strip()] if display else []
            readings.append({'page': label, 'handle': handle, 'unit': unit, 'source_reading': expected,
                             'actual': actual, 'projection_factor': factor, 'absolute_error': error,
                             'visible_text': visible})
            if error > tolerance + 1e-9:
                raise ValueError('Actual native dimension measurement differs from the source reading.')
            if display_unit == 'ft-in':
                if len(visible) != 1 or not math.isclose(feet_inches_mm(visible[0]), expected, abs_tol=1e-8, rel_tol=0):
                    raise ValueError('Visible feet-and-inches text differs from the source reading.')
            else:
                scalar = metric_label(visible[0]) if len(visible) == 1 else None
                source_label = metric_label(source_text)
                if any(label and label[3] not in {None, unit} for label in (source_label, scalar)):
                    raise ValueError('Source or visible dimension text uses a different measurement unit.')
                if source_label and not math.isclose(source_label[0], expected, rel_tol=0, abs_tol=1e-9):
                    raise ValueError('Source dimension text differs from the recorded reading.')
                if scalar and not math.isclose(scalar[0], expected, rel_tol=0, abs_tol=1e-9):
                    raise ValueError('Visible dimension text differs from the source reading.')
                if source_label and scalar is None:
                    raise ValueError('Visible dimension text is missing or cannot be read as the source annotation.')
                if source_label and source_label[1:3] != scalar[1:3]:
                    raise ValueError('Visible dimension radius, diameter, thread or quantity qualifier differs from the source.')
        except (KeyError, ValueError, TypeError, NotImplementedError) as error:
            findings.append(label + ': ' + str(error))
    if seen != dimensions.keys():
        findings.append(label + ': source dimension inventory does not cover all native dimensions.')
    return readings


def source_assets(manifest_path, manifest):
    root = manifest_path.parent
    return [asset_path(root, source['path']) for source in manifest['sources']] + [
        asset_path(root, page[key]) for page in manifest['pages'] for key in ('preview', 'native') if key in page]


def source_image_ids(root, manifest):
    sources = {source['id']: source for source in manifest['sources']}
    legacy_pages = {}
    inventory = {}
    for page in manifest['pages']:
        if 'native' not in page:
            continue
        native = read_json(asset_path(root, page['native']))
        if 'embedded_images' in native:
            inventory[page['id']] = {image['id'] for image in native['embedded_images']}
        else:
            legacy_pages.setdefault(page['source_id'], []).append(page)
    if legacy_pages:
        import pdfplumber
        for source_id, pages in legacy_pages.items():
            with pdfplumber.open(asset_path(root, sources[source_id]['path'])) as pdf:
                for page in pages:
                    inventory[page['id']] = {
                        image['id'] for image in embedded_images(pdf.pages[page['page'] - 1])}
    return inventory


def check_embedded_images(page, expected, page_handles, scope, findings):
    seen = set()
    rows = page.get('embedded_images', [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        findings.append(page['id'] + ': invalid PDF image restoration inventory.')
        return
    for row in rows:
        image_id = row.get('id')
        status = row.get('status')
        handles = row.get('handles', [])
        tag = f"{page['id']}/{image_id}"
        if not isinstance(image_id, str) or image_id not in expected or image_id in seen:
            findings.append(tag + ': unknown or repeated PDF image occurrence.')
            continue
        seen.add(image_id)
        evidence = row.get('evidence')
        if not isinstance(evidence, str) or not evidence.strip():
            findings.append(tag + ': record visible content and evidence from the original page.')
        if status == 'restored':
            if (not isinstance(handles, list) or not handles or any(not isinstance(h, str) for h in handles)
                    or not set(handles) <= page_handles):
                findings.append(tag + ': link the editable entities on this page that restore the PDF image.')
        elif handles or not (status == 'not_visible' or (status == 'excluded' and scope == 'selected_region')):
            findings.append(tag + ': PDF image occurrence has unresolved visible content.')
    for image_id in sorted(expected - seen):
        findings.append(f"{page['id']}/{image_id}: PDF image occurrence has no restoration disposition.")


def check_inputs(manifest_path, dxf_path, record_path):
    from ezdxf.disassemble import recursive_decompose
    manifest_path = Path(manifest_path).resolve()
    manifest, record = read_json(manifest_path), read_json(record_path)
    root = manifest_path.parent
    doc = load_dxf(dxf_path)
    findings, anchors, dimension_readings = [], [], []
    entities = {entity.dxf.handle: entity for entity in doc.modelspace()}
    if record.get('manifest_sha256') != sha(manifest_path):
        findings.append('Source manifest fingerprint does not match the record.')
    if manifest.get('schema') != 'xy-local-restoration-source-v1':
        findings.append('Unsupported source manifest.')
    for source in manifest['sources']:
        if sha(asset_path(root, source['path'])) != source['sha256']:
            findings.append('Source bytes changed: ' + source['id'])
    for page in manifest['pages']:
        for key in ('preview', 'native'):
            if key in page and sha(asset_path(root, page[key])) != page[key + '_sha256']:
                findings.append('Source extraction changed: ' + page['id'] + '/' + key)
    if not record.get('scope_description', '').strip() or record.get('scope') not in {'entire_source', 'selected_region'}:
        findings.append('Record the user-authorized restoration scope.')
    if int(doc.units) != 4:
        findings.append('DXF units must be millimeters ($INSUNITS=4).')
    if not entities:
        findings.append('DXF modelspace is empty.')
    auditor = doc.audit()
    if auditor.has_errors or auditor.has_fixes:
        findings.append('DXF audit found errors or made in-memory fixes; repair, save and recheck the actual file.')
    for block in doc.blocks:
        for entity in block:
            kind = entity.dxftype()
            if kind in RASTER_TYPES:
                findings.append('Raster/underlay/proxy entity is not editable restoration: ' + kind)
            elif kind == 'SPLINE' or (kind in {'HATCH', 'MPOLYGON'} and any(
                    type(edge).__name__ == 'SplineEdge' for boundary in entity.paths
                    for edge in getattr(boundary, 'edges', ()))):
                findings.append('SPLINE or spline fill boundary found at ' + entity.dxf.handle)
            elif kind == 'DIMENSION':
                display = entity.get_geometry_block()
                label = entity.dxf.get('text', '<>')
                if (not display or (label and '<>' not in label)
                        or not any(item.dxftype() in {'TEXT', 'MTEXT'} and item.plain_text().strip() for item in display)
                        or any(item.dxftype() in {'HATCH', 'MPOLYGON'} | RASTER_TYPES for item in display)):
                    findings.append('DIMENSION must display a native editable reading at ' + entity.dxf.handle)
    if any(len(layout) for layout in doc.layouts if layout.name != 'Model'):
        findings.append('This workflow assigns all restored content to modelspace; nonempty paperspace is unaccounted for.')
    tolerance = finite_number(record.get('tolerance_mm'), positive=True)
    pages = record.get('pages', [])
    expected_ids = {page['id'] for page in manifest['pages']}
    image_ids = source_image_ids(root, manifest)
    if len(pages) != len(expected_ids) or {page.get('id') for page in pages} != expected_ids:
        findings.append('The record does not cover each original page exactly once.')
    claimed = set()
    for page in pages:
        label = str(page.get('id'))
        page_handles = set()
        if page.get('unresolved'):
            findings.append(label + ': unresolved source/scale/content questions remain.')
        if page.get('excluded') and record.get('scope') == 'entire_source':
            findings.append(label + ': content excluded from an entire-source restoration.')
        regions = page.get('regions') or []
        if not regions:
            findings.append(label + ': no region coverage recorded.')
        region_ids = set()
        for region in regions:
            region_id = region.get('id')
            tag = label + '/' + str(region_id)
            if not isinstance(region_id, str) or not region_id.strip() or region_id in region_ids:
                findings.append(tag + ': region ID is empty or repeated.')
            region_ids.add(region_id)
            handles = region.get('handles')
            if not isinstance(handles, list) or any(not isinstance(h, str) for h in handles):
                findings.append(tag + ': invalid entity handle list.')
                continue
            selected = set(handles)
            if len(handles) != len(selected) or selected & claimed or not selected <= entities.keys():
                findings.append(tag + ': missing, repeated or cross-region entity handles.')
                continue
            claimed.update(selected)
            page_handles.update(selected)
            flattened = list(recursive_decompose([entities[h] for h in handles]))
            geometry = any(entity.dxftype() not in TEXT_TYPES for entity in flattened)
            if region.get('scale_status') == 'no_geometric_content':
                if geometry or not str(region.get('reason', '')).strip():
                    findings.append(tag + ': no-geometric-content claim is invalid or lacks a reason.')
                continue
            if region.get('scale_status') != 'verified' or not region.get('anchors'):
                findings.append(tag + ': physical scale is not verified.')
                continue
            if not geometry:
                findings.append(tag + ': an empty/text-only region needs an explicit no-geometric-content reason.')
            for anchor in region['anchors']:
                try:
                    refs = anchor['handles']
                    if not isinstance(refs, list) or not refs or any(not isinstance(h, str) for h in refs) or len(set(refs)) != len(refs) or not set(refs) <= selected:
                        raise ValueError('Anchor handles must be distinct entities in this region.')
                    if anchor.get('source_basis') not in {'dimension', 'explicit_scale', 'user_reference'} or not str(anchor.get('source_text', '')).strip() or not str(anchor.get('derivation', '')).strip():
                        raise ValueError('Anchor requires source text, basis and scale derivation.')
                    expected = finite_number(anchor['expected_mm'], positive=True)
                    tol = finite_number(anchor.get('tolerance_mm', tolerance))
                    if tol > tolerance:
                        raise ValueError('Anchor tolerance exceeds task tolerance.')
                    actual = measurement([entities[h] for h in refs], anchor['kind'])
                    if not math.isfinite(actual):
                        raise ValueError('Measured value is non-finite.')
                    passed = abs(actual - expected) <= tol + 1e-9
                    anchors.append({'region': tag, 'kind': anchor['kind'], 'handles': refs,
                                    'actual_mm': actual, 'expected_mm': expected,
                                    'error_mm': abs(actual - expected), 'tolerance_mm': tol,
                                    'passed': passed, 'source_text': anchor['source_text']})
                    if not passed:
                        findings.append(tag + ': measured geometry does not match its source scale anchor.')
                except (KeyError, TypeError, ValueError) as error:
                    findings.append(tag + ': ' + str(error))
        dimensions = page_native_dimensions(entities, page_handles)
        dimension_readings.extend(check_dimension_inventory(page, dimensions, tolerance, findings))
        check_embedded_images(page, image_ids.get(label, set()), page_handles, record.get('scope'), findings)
    if claimed != entities.keys():
        findings.append('Actual modelspace entities are not completely assigned to source pages/regions.')
    return {'automated_checks': 'failed' if findings else 'passed',
              'restoration_acceptance': 'requires_visual_review',
              'manifest_sha256': sha(manifest_path), 'record_sha256': sha(record_path),
              'dxf_sha256': sha(dxf_path), 'findings': findings, 'anchors': anchors,
              'dimensions': dimension_readings,
              'limitations': ['Reference text/derivations are declarations that require source-image review.',
                              'This does not verify all source geometry, wording, layout or fabrication.']}


def check(args):
    manifest_path = Path(args.manifest).resolve()
    protect_output(args.out, [manifest_path, args.dxf, args.record])
    protect_output(args.out, source_assets(manifest_path, read_json(manifest_path)))
    result = check_inputs(manifest_path, args.dxf, args.record)
    write_json(args.out, result)
    return result


def render_dxf_png(doc, path, selected=None, *, dark=False, frame=None, paper_width_mm=None,
                   max_edge=1800):
    import matplotlib
    matplotlib.use('Agg')
    from ezdxf.addons.drawing import RenderContext
    from ezdxf.addons.drawing.config import Configuration
    from ezdxf.addons.drawing.frontend import UniversalFrontend
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from ezdxf.addons.drawing.pipeline import RenderPipeline2d
    from ezdxf.addons.drawing.properties import LayoutProperties
    from ezdxf.npshapes import to_matplotlib_path
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.patches import PathPatch
    from restoration_geometry import OUTLINE_WINDING_APPID

    class PreviewBackend(MatplotlibBackend):
        use_winding = False

        def draw_filled_paths(self, paths, properties):
            if not self.use_winding:
                return super().draw_filled_paths(paths, properties)
            # Source outlines already encode their winding. Guessing holes
            # by geometry can fill parts of a glyph or miss nested rings.
            paths = list(paths)
            if paths:
                glyph = to_matplotlib_path(paths)
                self.ax.add_patch(PathPatch(glyph, color=properties.color, linewidth=0, zorder=self._get_z()))

    class PreviewPipeline(RenderPipeline2d):
        def draw_text(self, *args, **kwargs):
            backend.use_winding = True
            try:
                super().draw_text(*args, **kwargs)
            finally:
                backend.use_winding = False

    class PreviewFrontend(UniversalFrontend):
        def draw_hatch_entity(self, entity, properties, **kwargs):
            backend.use_winding = entity.has_xdata(OUTLINE_WINDING_APPID)
            try:
                super().draw_hatch_entity(entity, properties, **kwargs)
            finally:
                backend.use_winding = False

    bg, fg = ('#000000', '#ffffff') if dark else ('#ffffff', '#000000')
    size = (16, 12)
    minimum_line = 1.5
    lineweight_scaling = 1.0
    if frame is not None:
        x0, y0, x1, y1 = frame
        width, height = x1 - x0, y1 - y0
        scale = max_edge / max(width, height)
        size = (max(1, round(width*scale))/240, max(1, round(height*scale))/240)
        minimum_line = .75 if dark else .50
    if paper_width_mm is not None:
        # The backend takes pen widths in points. For a registered PDF
        # canvas, scale the source's paper millimeters to the same pixels.
        lineweight_scaling = size[0] * 72 / paper_width_mm
        minimum_line = .10
    fig = Figure(figsize=size, dpi=240, facecolor=bg)
    FigureCanvasAgg(fig)
    ax = fig.add_axes((0, 0, 1, 1))
    backend = PreviewBackend(ax, adjust_figure=False)
    model = doc.modelspace()
    layout = LayoutProperties.from_layout(model)
    layout.set_colors(bg, fg)
    config = Configuration(min_lineweight=minimum_line, lineweight_scaling=lineweight_scaling)
    filter_func = None if selected is None else lambda entity: entity.dxf.handle in selected
    PreviewFrontend(RenderContext(doc), PreviewPipeline(backend), config).draw_layout(
        model, layout_properties=layout, filter_func=filter_func)
    if frame is not None:
        left, bottom, right, top = ax.dataLim.extents
        margin = 2 / scale
        if (all(math.isfinite(v) for v in (left, bottom, right, top))
                and (left < x0 - margin or bottom < y0 - margin or right > x1 + margin or top > y1 + margin)):
            raise ValueError(f'Review viewport {frame} would clip CAD bounds {[left, bottom, right, top]}.')
        ax.set_xlim(x0, x1)
        ax.set_ylim(y0, y1)
    fig.savefig(path, dpi=240, facecolor=bg)


def preview(args):
    from PIL import Image
    doc = load_dxf(args.dxf)
    target = Path(args.out).resolve()
    if target.suffix.lower() != '.png':
        raise ValueError('Preview output must end with .png.')
    inputs = [args.dxf] + ([args.record] if args.record else [])
    protect_output(target.with_suffix('.preview.json'), inputs)
    target.parent.mkdir(parents=True, exist_ok=True)
    outputs = []

    def render(path, selected=None):
        protect_output(path, inputs)
        render_dxf_png(doc, path, selected)
        outputs.append(path)
        # Non-overlapping readable tiles of the actual rendered DXF.
        with Image.open(path) as image:
            if max(image.size) > 1600:
                for y in range(0, image.height, 1400):
                    for x in range(0, image.width, 1400):
                        tile = path.with_name(path.stem + f'.detail-{y // 1400 + 1}-{x // 1400 + 1}.png')
                        protect_output(tile, inputs)
                        image.crop((x, y, min(x + 1400, image.width), min(y + 1400, image.height))).save(tile)
                        outputs.append(tile)

    if not len(doc.modelspace()):
        raise ValueError('Cannot render an empty DXF.')
    render(target)
    if args.record:
        record = read_json(args.record)
        allowed = {entity.dxf.handle for entity in doc.modelspace()}
        for i, page in enumerate(record['pages'], 1):
            handles = {h for region in page['regions'] for h in region['handles']}
            if not handles <= allowed:
                raise ValueError('Page preview selects nonexistent DXF handles.')
            if handles:
                render(target.with_name(target.stem + f'.page-{i:03}.png'), handles)
    result = {'dxf_sha256': sha(args.dxf), 'previews': [
        {'path': path.name, 'sha256': sha(path)} for path in outputs]}
    if args.record:
        result['record_sha256'] = sha(args.record)
    write_json(target.with_suffix('.preview.json'), result)
    return result


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare', help='Freeze PDF/image inputs and extract every page.')
    p.add_argument('inputs', nargs='+')
    p.add_argument('--out', required=True)
    p.add_argument('--max-pixels', type=int, default=36_000_000)
    p.set_defaults(func=prepare)
    for name, func in (('inspect', inspect_dxf), ('preview', preview), ('check', check)):
        p = sub.add_parser(name)
        p.add_argument('--dxf', required=True)
        p.add_argument('--out', required=True)
        if name == 'preview':
            p.add_argument('--record')
        if name == 'check':
            p.add_argument('--manifest', required=True)
            p.add_argument('--record', required=True)
        p.set_defaults(func=func)
    from delivery import review_pack, deliver
    for name, func in (('review-pack', review_pack), ('deliver', deliver)):
        p = sub.add_parser(name)
        for field in ('manifest', 'dxf', 'record', 'out'):
            p.add_argument('--'+field, required=True)
        if name == 'deliver':
            p.add_argument('--pack', required=True)
            p.add_argument('--review', required=True)
        p.set_defaults(func=func)
    args = parser.parse_args()
    if getattr(args, 'max_pixels', 1) <= 0:
        parser.error('--max-pixels must be positive')
    try:
        result = args.func(args)
        print(json.dumps(result, ensure_ascii=False, default=json_default, allow_nan=False))
        return 2 if result.get('automated_checks') == 'failed' else 0
    except Exception as error:
        print(json.dumps({'status': 'error', 'type': type(error).__name__, 'message': str(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
