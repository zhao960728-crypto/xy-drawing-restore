#!/usr/bin/env python3
# Copyright 2026 XY (GitHub: zhao960728-crypto)
# SPDX-License-Identifier: Apache-2.0
"""Local source extraction, DXF measurements and previews. Not an OCR/model engine."""
from __future__ import annotations
import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
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
                                       'lines': page.lines, 'rects': page.rects, 'curves': page.curves}
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
        return float(entity.get_measurement())
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
                row['measured_mm'] = measurement([entity], 'dimension_measurement')
            except ValueError as error:
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


def check(args):
    protect_output(args.out, [args.manifest, args.dxf, args.record])
    from ezdxf.disassemble import recursive_decompose
    manifest_path = Path(args.manifest).resolve()
    manifest, record = read_json(manifest_path), read_json(args.record)
    root = manifest_path.parent
    frozen_inputs = [asset_path(root, source['path']) for source in manifest['sources']]
    frozen_inputs += [asset_path(root, page[key]) for page in manifest['pages'] for key in ('preview', 'native') if key in page]
    protect_output(args.out, frozen_inputs)
    doc = load_dxf(args.dxf)
    findings, anchors = [], []
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
            if entity.dxftype() in RASTER_TYPES:
                findings.append('Raster/underlay/proxy entity is not editable restoration: ' + entity.dxftype())
    if any(len(layout) for layout in doc.layouts if layout.name != 'Model'):
        findings.append('This workflow assigns all restored content to modelspace; nonempty paperspace is unaccounted for.')
    tolerance = finite_number(record.get('tolerance_mm'), positive=True)
    pages = record.get('pages', [])
    expected_ids = {page['id'] for page in manifest['pages']}
    if len(pages) != len(expected_ids) or {page.get('id') for page in pages} != expected_ids:
        findings.append('The record does not cover each original page exactly once.')
    claimed = set()
    for page in pages:
        label = str(page.get('id'))
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
    if claimed != entities.keys():
        findings.append('Actual modelspace entities are not completely assigned to source pages/regions.')
    result = {'automated_checks': 'failed' if findings else 'passed',
              'restoration_acceptance': 'requires_visual_review',
              'manifest_sha256': sha(manifest_path), 'record_sha256': sha(args.record),
              'dxf_sha256': sha(args.dxf), 'findings': findings, 'anchors': anchors,
              'limitations': ['Reference text/derivations are declarations that require source-image review.',
                              'This does not verify all source geometry, wording, layout or fabrication.']}
    write_json(args.out, result)
    return result


def preview(args):
    import matplotlib
    matplotlib.use('Agg')
    from PIL import Image
    from ezdxf.addons.drawing.config import Configuration
    from ezdxf.addons.drawing.matplotlib import qsave
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
        qsave(doc.modelspace(), str(path), bg='#ffffff', fg='#000000', dpi=240,
              config=Configuration(min_lineweight=1.5),
              size_inches=(16, 12),
              filter_func=None if selected is None else lambda entity: entity.dxf.handle in selected)
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
