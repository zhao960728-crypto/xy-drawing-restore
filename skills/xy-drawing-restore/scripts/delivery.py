# Copyright 2026 XY (GitHub: zhao960728-crypto)
# SPDX-License-Identifier: Apache-2.0
"""Build source/DXF comparisons and publish only a fully reviewed restoration."""
from contextlib import contextmanager
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

from page_exports import verify_page_dxf, write_page_dxf
from restore_tools import asset_path, check_inputs, load_dxf, read_json, render_dxf_png, sha, write_json


REVIEW_CHECKS = ('geometry_and_connections', 'layout_and_scale', 'native_dimensions_and_duplicates',
                 'text_and_symbols', 'fills_and_openings', 'details_and_occlusion')
PACK_SCHEMA = 'xy-restoration-review-pack-v1'


@contextmanager
def new_directory(destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError('Use a new output directory. Existing files are kept.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='.xy-restore-', dir=destination.parent) as temp:
        stage = Path(temp) / 'files'
        stage.mkdir()
        yield stage
        if destination.exists():
            raise ValueError('The output directory was created during this run.')
        stage.rename(destination)


def load_scope(args):
    manifest_path = Path(args.manifest).resolve()
    manifest, record = read_json(manifest_path), read_json(args.record)
    bindings = {'manifest_sha256': sha(manifest_path), 'dxf_sha256': sha(args.dxf),
                'record_sha256': sha(args.record)}
    if manifest.get('schema') != 'xy-local-restoration-source-v1' or record.get('manifest_sha256') != bindings['manifest_sha256']:
        raise ValueError('The restoration record must bind the current source manifest.')
    pages = {page['id']: page for page in record['pages']}
    source_ids = [page['id'] for page in manifest['pages']]
    if len(pages) != len(record['pages']) or set(pages) != set(source_ids):
        raise ValueError('The restoration record must cover each source page once.')
    doc = load_dxf(args.dxf)
    model = doc.modelspace()
    actual = {entity.dxf.handle for entity in model}
    page_by_handle = {}
    for page_id, page in pages.items():
        regions = page.get('regions') or []
        ids = [region['id'] for region in regions]
        if not ids or len(ids) != len(set(ids)) or any(not str(region_id).strip() for region_id in ids):
            raise ValueError('Each source page needs distinct review regions.')
        for region in regions:
            handles = region['handles']
            if not isinstance(handles, list) or any(not isinstance(h, str) for h in handles):
                raise ValueError('Region handles must be a list of actual CAD handles.')
            for handle in handles:
                if handle not in actual or handle in page_by_handle:
                    raise ValueError('An entity is missing or assigned to multiple regions.')
                page_by_handle[handle] = page_id
    if page_by_handle.keys() != actual:
        raise ValueError('Some saved DXF entities are not assigned to a source page.')
    ordered = {page_id: [] for page_id in pages}
    redraw = {page_id: [] for page_id in pages}
    for entity in model:
        ordered[page_by_handle[entity.dxf.handle]].append(entity.dxf.handle)
    for entity in model.entities_in_redraw_order():
        redraw[page_by_handle[entity.dxf.handle]].append(entity)
    return manifest_path, manifest, pages, bindings, doc, ordered, redraw


def item(path):
    return {'path': path.name, 'sha256': sha(path)}


def comparison(source, rendered, output, source_box=None):
    from PIL import Image, ImageOps

    with Image.open(source) as image:
        if source_box is not None:
            if (not isinstance(source_box, list) or len(source_box) != 4 or any(type(v) is not int for v in source_box)
                    or not (0 <= source_box[0] < source_box[2] <= image.width
                            and 0 <= source_box[1] < source_box[3] <= image.height)):
                raise ValueError('Review region is outside the source preview pixels.')
            image = image.crop(source_box)
        left = ImageOps.contain(image.convert('RGB'), (1800, 1800))
    if rendered:
        with Image.open(rendered) as image:
            right = ImageOps.contain(image.convert('RGB'), (1800, 1800))
    else:
        right = Image.new('RGB', left.size, 'white')
    canvas = Image.new('RGB', (left.width+right.width+24, max(left.height, right.height)), '#d9d9d9')
    canvas.paste(left, (0, 0))
    canvas.paste(right, (left.width+24, 0))
    canvas.save(output)


def review_pack(args):
    manifest_path, manifest, pages, bindings, doc, ordered, redraw = load_scope(args)
    with new_directory(args.out) as stage:
        rows = []
        for index, page in enumerate(manifest['pages'], 1):
            page_id = page['id']
            source = asset_path(manifest_path.parent, page['preview'])
            if sha(source) != page['preview_sha256']:
                raise ValueError('Source preview changed: ' + page_id)
            stem = f'page-{index:03}'
            dxf, png = stage/(stem+'.dxf'), stage/(stem+'.png')
            has_entities = bool(ordered[page_id])
            if has_entities:
                saved = write_page_dxf(doc, ordered[page_id], redraw[page_id], dxf)
                render_dxf_png(saved, png)
            overall = stage/(stem+'.compare.png')
            comparison(source, png if has_entities else None, overall)
            regions = []
            for region_index, region in enumerate(pages[page_id]['regions'], 1):
                region_stem = f'{stem}.region-{region_index:03}'
                detail = stage/(region_stem+'.png')
                handles = set(region['handles'])
                if handles:
                    render_dxf_png(doc, detail, handles)
                panel = stage/(region_stem+'.compare.png')
                comparison(source, detail if handles else None, panel, region.get('source_box_px'))
                regions.append({'id': region['id'], 'comparison': item(panel),
                                'render': item(detail) if handles else None})
            rows.append({'id': page_id, 'source_preview_sha256': sha(source), 'comparison': item(overall),
                         'dxf': item(dxf) if has_entities else None, 'preview': item(png) if has_entities else None,
                         'regions': regions})
        checks = check_inputs(args.manifest, args.dxf, args.record)
        if any(checks[key] != value for key, value in bindings.items()):
            raise ValueError('An input changed during review preparation.')
        pack = {'schema': PACK_SCHEMA, **bindings, 'pages': rows, 'automated_checks': checks}
        write_json(stage/'review-pack.json', pack)
        template = {'pack_sha256': sha(stage/'review-pack.json'), 'review_type': '', 'reviewer': '', 'pages': [
            {'id': page['id'], 'status': 'pending', 'comparison_sha256': page['comparison']['sha256'],
             'findings': [], 'regions': [
                {'id': region['id'], 'status': 'pending', 'comparison_sha256': region['comparison']['sha256'],
                 'checks': dict.fromkeys(REVIEW_CHECKS, ''), 'findings': []} for region in page['regions']]}
            for page in rows]}
        write_json(stage/'review-template.json', template)
    return {'review_pack': str(Path(args.out).resolve()/'review-pack.json'),
            'status': 'awaiting_visual_review', 'pages': len(rows)}


def checked_pack_file(folder, entry):
    path = asset_path(folder, entry['path'])
    if not path.is_file() or sha(path) != entry['sha256']:
        raise ValueError('Review evidence is missing or changed: ' + entry['path'])
    return path


def indexed(rows, expected):
    result = {row['id']: row for row in rows}
    if len(result) != len(rows) or set(result) != set(expected):
        raise ValueError('The visual review must cover every page and region exactly once.')
    return result


def verify_visual_row(row, evidence):
    if (row.get('status') != 'passed' or row.get('findings') != []
            or row.get('comparison_sha256') != evidence['comparison']['sha256']):
        raise ValueError('Unresolved or unbound visual review: ' + evidence['id'])


def deliver(args):
    _, manifest, pages, bindings, doc, ordered, redraw = load_scope(args)
    checks = check_inputs(args.manifest, args.dxf, args.record)
    if checks['automated_checks'] != 'passed':
        raise ValueError('Restoration still has unresolved checks: ' + '; '.join(checks['findings'][:6]))
    pack_path = Path(args.pack).resolve()
    review_path = Path(args.review).resolve()
    pack_sha = sha(pack_path)
    review_sha = sha(review_path)
    pack, review = read_json(pack_path), read_json(review_path)
    if (pack.get('schema') != PACK_SCHEMA or review.get('pack_sha256') != pack_sha
            or any(pack.get(key) != value or checks[key] != value for key, value in bindings.items())):
        raise ValueError('Review must bind the current original, DXF and restoration record.')
    if review.get('review_type') not in {'self_review', 'independent_review'} or not str(review.get('reviewer', '')).strip():
        raise ValueError('Record who performed the visual comparison and the review type.')
    evidence = indexed(pack['pages'], pages)
    decisions = indexed(review['pages'], pages)
    sources = {source['id']: source for source in manifest['sources']}
    with new_directory(args.out) as stage:
        delivered = []
        for index, page in enumerate(manifest['pages'], 1):
            page_id = page['id']
            record = pages[page_id]
            dims = record.get('dimensions', [])
            count = record.get('source_dimension_count')
            if type(count) is not int or count != len(dims) or (count == 0 and not str(record.get('no_dimensions_reason', '')).strip()):
                raise ValueError('Source dimensions must be counted from the original page: ' + page_id)
            row, decision = evidence[page_id], decisions[page_id]
            if row['source_preview_sha256'] != page['preview_sha256']:
                raise ValueError('Review references a different source image.')
            checked_pack_file(pack_path.parent, row['comparison'])
            verify_visual_row(decision, row)
            source_regions = {region['id']: region for region in record['regions']}
            regions = indexed(row['regions'], source_regions)
            region_decisions = indexed(decision['regions'], regions)
            for region_id, region in regions.items():
                source_region = source_regions[region_id]
                if source_region.get('source_box_px') is None:
                    raise ValueError('Locate the review region on the actual source page: ' + region_id)
                if source_region.get('scale_status') == 'verified' and not any(
                        anchor.get('kind') != 'dimension_measurement' for anchor in source_region.get('anchors', [])):
                    raise ValueError('A verified scale requires a measurable source feature: ' + region_id)
                checked_pack_file(pack_path.parent, region['comparison'])
                if region['render']:
                    checked_pack_file(pack_path.parent, region['render'])
                detail = region_decisions[region_id]
                verify_visual_row(detail, region)
                notes = detail.get('checks') or {}
                if set(notes) != set(REVIEW_CHECKS) or any(not isinstance(note, str) or not note.strip() for note in notes.values()):
                    raise ValueError('Record evidence for each visual check in region: ' + region_id)
            stem = f'page-{index:03}'
            source = sources[page['source_id']]
            page_binding = {'id': page_id, 'source_name': source['name'],
                            'source_sha256': source['sha256'], 'source_page': page['page']}
            if not ordered[page_id]:
                if row['dxf'] is not None or row['preview'] is not None:
                    raise ValueError('A blank source page cannot have a result file.')
                delivered.append({**page_binding, 'status': 'empty_page_reviewed'})
                continue
            dxf, png = stage/(stem+'.dxf'), stage/(stem+'.png')
            actual_dxf = checked_pack_file(pack_path.parent, row['dxf'])
            actual_png = checked_pack_file(pack_path.parent, row['preview'])
            shutil.copyfile(actual_dxf, dxf)
            shutil.copyfile(actual_png, png)
            if sha(dxf) != row['dxf']['sha256'] or sha(png) != row['preview']['sha256']:
                raise ValueError('Reviewed output changed during delivery.')
            verify_page_dxf(doc.units, redraw[page_id], dxf)
            delivered.append({**page_binding, 'dxf': item(dxf), 'preview': item(png)})
        current = check_inputs(args.manifest, args.dxf, args.record)
        if current != checks or sha(pack_path) != pack_sha or sha(review_path) != review_sha:
            raise ValueError('Inputs or review evidence changed during delivery.')
        receipt = {'status': 'delivered', **bindings, 'review_pack_sha256': pack_sha,
                   'visual_review_sha256': review_sha,
                   'review_type_declared': review['review_type'], 'reviewer_declared': review['reviewer'],
                   'pages': delivered}
        write_json(stage/'delivery.json', receipt)
    return receipt
