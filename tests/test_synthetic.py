# Copyright 2026 XY (GitHub: zhao960728-crypto)
# SPDX-License-Identifier: Apache-2.0
"""Synthetic source/scale and input-overwrite regression; not model accuracy."""
from pathlib import Path
import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import uuid

import ezdxf
import numpy as np
from PIL import Image, ImageDraw
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
import yaml

BASE = Path(__file__).resolve().parents[1]
PACKAGE = BASE / 'skills/xy-drawing-restore'
SCRIPT = PACKAGE / 'scripts/restore_tools.py'
sys.path.insert(0, str(SCRIPT.parent))
from delivery import cad_frame, paper_width, source_is_dark
from page_exports import verify_page_dxf
from restoration_geometry import (add_cubic_chain, add_filled_paths, add_native_linear_dimension,
                                  restore_color_artwork, restore_raster_ornament)
from restore_tools import check_dimension_inventory, check_embedded_images, feet_inches_mm, render_dxf_png, source_image_ids
RUN = BASE / 'work' / ('verification-' + uuid.uuid4().hex[:8])
RUN.mkdir(parents=True, exist_ok=True)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def invoke(*args, expected=0):
    result = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                            capture_output=True, encoding='utf-8')
    assert result.returncode == expected, (args, result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)


pdf = RUN / 'source.pdf'
c = canvas.Canvas(str(pdf), pagesize=(210 * mm, 148 * mm))
for width in (120, 100):
    c.rect(25 * mm, 35 * mm, width * mm, 60 * mm)
    c.circle(45 * mm, 55 * mm, 5 * mm)
    c.drawString(25 * mm, 105 * mm, f'Original drawing: {width} x 60 mm; hole diameter 10 mm')
    c.showPage()
c.save()
image = Image.new('RGB', (800, 500), 'white')
draw = ImageDraw.Draw(image)
draw.rectangle((50, 100, 650, 400), outline='black', width=2)
draw.ellipse((125, 175, 175, 225), outline='black', width=2)
draw.text((50, 40), '120 x 60 mm; diameter 10 mm', fill='black')
image.save(RUN / 'source.png')
image.save(RUN / 'source.tiff', save_all=True, append_images=[image.copy()])

prepared = invoke('prepare', '--out', RUN / 'frozen', pdf, RUN / 'source.png', RUN / 'source.tiff')
assert prepared['pages'] == 5 and prepared['physical_scale_verified'] is False
manifest_path = Path(prepared['manifest'])
manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
assert [s['pages'] for s in manifest['sources']] == [2, 1, 2]
native = json.loads((manifest_path.parent / manifest['pages'][0]['native']).read_text(encoding='utf-8'))
assert len(native['chars']) > 20 and len(native['rects']) > 0 and native['curves']

doc = ezdxf.new('R2010')
doc.units = 4
msp = doc.modelspace()
pages = []
for i, page in enumerate(manifest['pages']):
    x, width = i * 160, (100 if page['id'] == 's1-p2' else 120)
    lines = [msp.add_line((x, 0), (x + width, 0)), msp.add_line((x + width, 0), (x + width, 60)),
             msp.add_line((x + width, 60), (x, 60)), msp.add_line((x, 60), (x, 0))]
    circle = msp.add_circle((x + 20, 20), 5)
    text = msp.add_text(f'{width} x 60 mm; D10', dxfattribs={'insert': (x, 70), 'height': 3})
    region = {'id': 'main', 'handles': [e.dxf.handle for e in [*lines, circle, text]],
              'scale_status': 'verified', 'anchors': []}
    for entity, kind, expected in [(lines[0], 'line_length', width), (circle, 'circle_diameter', 10)]:
        region['anchors'].append({'kind': kind, 'handles': [entity.dxf.handle],
                                  'expected_mm': expected, 'source_basis': 'dimension',
                                  'source_text': f'Synthetic source {page["id"]}: width {width} mm; diameter 10 mm',
                                  'derivation': 'Explicit synthetic fixture dimension; copied to corresponding native geometry.'})
    pages.append({'id': page['id'], 'regions': [region], 'unresolved': [], 'excluded': []})
dxf = RUN / '还原图纸.dxf'
doc.saveas(dxf)
record = {'manifest_sha256': sha(manifest_path), 'scope': 'entire_source',
          'scope_description': 'Entire synthetic source, all pages and frames.', 'tolerance_mm': 0.05, 'pages': pages}
record_path = RUN / 'record.json'
write(record_path, record)
result = invoke('check', '--manifest', manifest_path, '--dxf', dxf,
                '--record', record_path, '--out', RUN / 'checks.json')
assert result['automated_checks'] == 'passed' and result['restoration_acceptance'] == 'requires_visual_review'
assert len(result['anchors']) == 10
inspection = invoke('inspect', '--dxf', dxf, '--out', RUN / 'entities.json')
assert inspection['entities'] == 30
previews = invoke('preview', '--dxf', dxf, '--out', RUN / '还原图纸.png', '--record', record_path)
assert len(previews['previews']) >= 6
assert previews['dxf_sha256'] == sha(dxf)
for preview in previews['previews']:
    p = RUN / preview['path']
    assert sha(p) == preview['sha256']
    with Image.open(p) as actual:
        assert actual.width > 0 and actual.height > 0

negatives = {}
def bad(name, mutate, test_dxf=dxf):
    broken = copy.deepcopy(record)
    mutate(broken)
    file = RUN / (name + '.json')
    write(file, broken)
    result = invoke('check', '--manifest', manifest_path, '--dxf', test_dxf,
                    '--record', file, '--out', RUN / (name + '.result.json'), expected=2)
    assert result['automated_checks'] == 'failed' and result['findings']
    negatives[name] = result['findings']

bad('wrong-scale', lambda r: r['pages'][0]['regions'][0]['anchors'][0].update(expected_mm=121))
bad('missing-page', lambda r: r['pages'].pop())
bad('unverified-scale', lambda r: r['pages'][0]['regions'][0].update(scale_status='unverified'))
bad('false-empty', lambda r: r['pages'][0]['regions'][0].update(scale_status='no_geometric_content', reason='False text-only claim'))
bad('reused-entities', lambda r: r['pages'][1]['regions'][0].update(handles=r['pages'][0]['regions'][0]['handles']))
bad('wrong-manifest', lambda r: r.update(manifest_sha256='0' * 64))
bad('unresolved-source', lambda r: r['pages'][0].update(unresolved=['Illegible source text']))
bad('unapproved-exclusion', lambda r: r['pages'][0].update(excluded=['Omitted difficult view']))
bad('cross-region-anchor', lambda r: r['pages'][0]['regions'][0]['anchors'][0].update(handles=[pages[1]['regions'][0]['handles'][0]]))
bad('relaxed-tolerance', lambda r: r['pages'][0]['regions'][0]['anchors'][0].update(tolerance_mm=1))
bad('unassigned-text', lambda r: r['pages'][0]['regions'][0]['handles'].pop())
wrong_units = RUN / 'wrong-units.dxf'
doc.units = 0
doc.saveas(wrong_units)
doc.units = 4
bad('wrong-units', lambda r: None, wrong_units)

native_doc = ezdxf.readfile(dxf)
native_space = native_doc.modelspace()
native_block = native_doc.blocks.new('NATIVE_DIM')
dimension = add_native_linear_dimension(native_block, (0, 0), (120, 0), (0, -8), 120)
native_ref = native_space.add_blockref(native_block.name, (0, 0))
native_dxf = RUN / 'native-dimension.dxf'
native_doc.saveas(native_dxf)
native_record = copy.deepcopy(record)
native_record['pages'][0]['regions'][0]['handles'].append(native_ref.dxf.handle)
native_record['pages'][0]['dimensions'] = [dict(handle=dimension.dxf.handle, unit='mm',
                                               source_reading=120, source_text='Source length 120')]
write(RUN/'native-record.json', native_record)
native_result = invoke('check', '--manifest', manifest_path, '--dxf', native_dxf,
                       '--record', RUN/'native-record.json', '--out', RUN/'native-check.json')
assert native_result['dimensions'][0]['actual'] == 120

def use_native(r):
    r['pages'][0] = copy.deepcopy(native_record['pages'][0])

def wrong_native_reading(r):
    use_native(r)
    r['pages'][0]['dimensions'][0]['source_reading'] = 100

bad('native-dimension-wrong-reading', wrong_native_reading, native_dxf)
dimension.dxf.text = '120'
literal_dxf = RUN/'literal-dimension.dxf'
native_doc.saveas(literal_dxf)
bad('dimension-literal-override', use_native, literal_dxf)
dimension.dxf.text = '<>'
dim_label = next(entity for entity in dimension.get_geometry_block()
                 if entity.dxftype() in {'TEXT', 'MTEXT'})
old_label = dim_label.dxf.text
dim_label.dxf.text = '119'
wrong_display = RUN/'wrong-dimension-display.dxf'
native_doc.saveas(wrong_display)
bad('dimension-visible-text-mismatch', use_native, wrong_display)
dim_label.dxf.text = old_label

for page in native_record['pages']:
    page['source_dimension_count'] = len(page.get('dimensions', []))
    if page['source_dimension_count'] == 0:
        page['no_dimensions_reason'] = 'The synthetic source has a descriptive label and no dimension annotation.'
    source = next(row for row in manifest['pages'] if row['id'] == page['id'])
    with Image.open(manifest_path.parent/source['preview']) as source_preview:
        page['regions'][0]['source_box_px'] = [0, 0, *source_preview.size]
write(RUN/'native-record.json', native_record)
review_dir = RUN/'review-private'
packed = invoke('review-pack', '--manifest', manifest_path, '--dxf', native_dxf,
                '--record', RUN/'native-record.json', '--out', review_dir)
assert packed['status'] == 'awaiting_visual_review'
pack_path = Path(packed['review_pack'])
review = json.loads((review_dir/'review-template.json').read_bytes())
review.update(review_type='self_review', reviewer='Synthetic fixture')
review_path = RUN/'review.json'
delivery_path = RUN/'delivery'
delivery_args = ['deliver', '--manifest', manifest_path, '--dxf', native_dxf, '--record', RUN/'native-record.json',
                 '--pack', pack_path, '--review', review_path, '--out', delivery_path]
write(review_path, review)
assert 'Unresolved' in invoke(*delivery_args, expected=2)['message']
assert not delivery_path.exists()
for page in review['pages']:
    page['status'] = 'passed'
    for region in page['regions']:
        region['status'] = 'passed'
        region['checks'] = {key: 'Synthetic fixture assertion' for key in region['checks']}
write(review_path, review)
pack = json.loads(pack_path.read_bytes())
last_evidence = review_dir/pack['pages'][-1]['regions'][0]['comparison']['path']
evidence_bytes = last_evidence.read_bytes()
last_evidence.write_bytes(evidence_bytes+b'changed')
assert 'changed' in invoke(*delivery_args, expected=2)['message']
assert not delivery_path.exists()
last_evidence.write_bytes(evidence_bytes)
missing = copy.deepcopy(review)
missing['pages'][-1]['regions'] = []
write(review_path, missing)
assert 'cover' in invoke(*delivery_args, expected=2)['message']
assert not delivery_path.exists()
write(review_path, review)
unresolved = copy.deepcopy(native_record)
unresolved['pages'][-1]['unresolved'] = ['A shape still differs from the original.']
write(RUN/'unresolved-record.json', unresolved)
with_unresolved = [RUN/'unresolved-record.json' if value == RUN/'native-record.json' else value
                   for value in delivery_args]
assert 'unresolved checks' in invoke(*with_unresolved, expected=2)['message']
assert not delivery_path.exists()
delivered = invoke(*delivery_args)
assert delivered['status'] == 'delivered' and len(delivered['pages']) == 5
for page in delivered['pages']:
    assert Path(page['dxf']['path']).stem == Path(page['preview']['path']).stem
    assert sha(delivery_path/page['dxf']['path']) == page['dxf']['sha256']
    assert sha(delivery_path/page['preview']['path']) == page['preview']['sha256']
assert 'new output directory' in invoke(*delivery_args, expected=2)['message']

native_block.add_spline([(0, 0), (15, 20), (30, 0)])
spline_dxf = RUN/'nested-spline.dxf'
native_doc.saveas(spline_dxf)
bad('nested-spline', use_native, spline_dxf)
assert any('SPLINE' in f for f in negatives['nested-spline'])

geometry_doc = ezdxf.new('R2010')
geometry = geometry_doc.modelspace()
arc, bound = add_cubic_chain(geometry, [[(0, 0), (0, 5.5228475), (4.4771525, 10), (10, 10)]], .0125)
assert arc.dxftype() == 'ARC' and abs(arc.dxf.radius-10) < .0125 and bound <= .0125
path, _ = add_cubic_chain(geometry, [[(0, 0), (5, 2), (10, 15), (14, 5)],
                                     [(14, 5), (17, 0), (18, -6), (22, 0)]], .0125)
assert path.dxftype() == 'LWPOLYLINE' and len(path) > 5
gray = np.full((24, 32), 255, dtype=np.uint8)
gray[3:18, 3:16] = gray[6:16, 20:26] = 0
gray[6:15, 6:13] = 255
detail = restore_raster_ornament(geometry, gray, [0, 0, 32, 24], lambda p: p)
assert detail['holes'] == 1 and detail['connected_ink_regions'] == 2
assert not detail['touches_region_border'] and all(geometry_doc.entitydb[h].dxftype() == 'HATCH' for h in detail['handles'])

outer = [(0, 0), (10, 0), (10, 10), (0, 10)]
inner = [(2, 2), (8, 2), (8, 8), (2, 8)]
ring = add_filled_paths(geometry, [outer, inner], even_odd=True)
assert len(ring) == 1 and len(ring[0].paths) == 2
solid = add_filled_paths(geometry, [outer, inner])
assert len(solid) == 1 and len(solid[0].paths) == 1
art = Image.new('RGB', (40, 30), 'white')
art_draw = ImageDraw.Draw(art)
art_draw.rectangle((3, 3, 19, 26), fill='brown')
art_draw.rectangle((20, 3, 36, 26), fill='orange')
art_draw.rectangle((14, 10, 25, 18), fill='white')
fills = restore_color_artwork(geometry, art, lambda points: points)
assert len(fills) == 2 and len(fills[0].paths) == 2
assert len({entity.dxf.true_color for entity in fills}) == 2
manual_palette = [(255, 255, 255), (165, 42, 42), (255, 165, 0)]
with_white = restore_color_artwork(geometry, art, lambda points: points, matte=None,
                                    source_palette=manual_palette)
assert {entity.rgb for entity in with_white} == set(manual_palette)

block = doc.blocks.new('RASTER_TEST')
image_def = doc.add_image_def(filename='external-source.png', size_in_pixel=(800, 500))
block.add_image(image_def, insert=(0, 0), size_in_units=(120, 60))
raster_ref = msp.add_blockref('RASTER_TEST', (0, -100))
raster_dxf = RUN / 'hidden-raster.dxf'
doc.saveas(raster_dxf)
bad('raster-in-block', lambda r: r['pages'][0]['regions'][0]['handles'].append(raster_ref.dxf.handle), raster_dxf)

original = manifest_path.parent / manifest['sources'][0]['path']
original_bytes = original.read_bytes()
original.write_bytes(original_bytes + b'\n% changed source bytes\n')
bad('changed-source', lambda r: None)
original.write_bytes(original_bytes)
invoke('prepare', '--out', RUN / 'frozen', pdf, expected=2)


protected = [manifest_path, record_path, dxf, manifest_path.parent / manifest['sources'][0]['path'], manifest_path.parent / manifest['pages'][0]['preview']]
for target in protected:
    before = sha(target)
    result = invoke('check', '--manifest', manifest_path, '--dxf', dxf, '--record', record_path, '--out', target, expected=2)
    assert 'overwrite' in result['message'] and sha(target) == before
before = sha(dxf)
invoke('inspect', '--dxf', dxf, '--out', dxf, expected=2)
assert sha(dxf) == before
preview_record = RUN / 'protected.preview.json'
preview_record.write_bytes(record_path.read_bytes()); before = sha(preview_record)
invoke('preview', '--dxf', dxf, '--record', preview_record, '--out', RUN / 'protected.png', expected=2)
assert sha(preview_record) == before

mixed_pdf = RUN / 'mixed-source.pdf'
canvas_with_image = canvas.Canvas(str(mixed_pdf), pagesize=(100, 50))
canvas_with_image.drawImage(ImageReader(Image.new('RGB', (8, 6), 'red')), 10, 12, 20, 15)
canvas_with_image.showPage()
canvas_with_image.save()
mixed_path = Path(invoke('prepare', '--out', RUN / 'mixed-frozen', mixed_pdf)['manifest'])
mixed_manifest = json.loads(mixed_path.read_bytes())
mixed_native = mixed_path.parent / mixed_manifest['pages'][0]['native']
mixed_data = json.loads(mixed_native.read_bytes())
assert mixed_data['embedded_images'] == [
    {'id': 'image-001', 'box_points': [10, 23, 30, 38], 'size_pixels': [8, 6], 'image_mask': False}]

for inventory in (mixed_data, {key: value for key, value in mixed_data.items() if key != 'embedded_images'}):
    write(mixed_native, inventory)
    assert source_image_ids(mixed_path.parent, mixed_manifest) == {'s1-p1': {'image-001'}}
    for status, handles, scope, passing in (
            (None, [], 'entire_source', False),
            ('restored', ['1'], 'entire_source', True),
            ('restored', ['2'], 'entire_source', False),
            ('unresolved', [], 'entire_source', False),
            ('excluded', [], 'entire_source', False),
            ('excluded', [], 'selected_region', True)):
        record_page = {'id': 's1-p1'}
        if status is not None:
            record_page['embedded_images'] = [
                {'id': 'image-001', 'status': status, 'handles': handles, 'evidence': 'Visible synthetic red region.'}]
        findings = []
        check_embedded_images(record_page, {'image-001'}, {'1'}, scope, findings)
        assert (not findings) == passing, findings

dark_page = Image.new('RGB', (200, 100), 'black')
ImageDraw.Draw(dark_page).rectangle((45, 45, 150, 55), fill='cyan')
assert source_is_dark(dark_page) and not source_is_dark(image)
frame_doc = ezdxf.new('R2010')
frame_doc.modelspace().add_line((0, 0), (30, 0))
frame_output = RUN / 'registered-dark.png'
frame = cad_frame({'cad_box_mm': [-50, -25, 50, 25]}, dark_page)
assert paper_width({'render_size_points': [720, 360]}, {'paper_lineweights': True}, frame, 200) == 254
assert paper_width({'render_size_points': [720, 360]}, {'paper_lineweights': True}, frame, 200, [50, 0, 150, 90]) == 127
render_dxf_png(frame_doc, frame_output, dark=True, frame=frame)
with Image.open(frame_output) as registered:
    pixels = np.asarray(registered.convert('RGB'))
    assert pixels.shape == (900, 1800, 3)
    assert pixels[445:455, 950:1400].max() > 210
    assert pixels[445:455, :880].max() == 0 and pixels[5, 5].max() == 0
for invalid in (([-50, -25, 50, 15], 'aspect'), ([-20, -10, 20, 10], 'clip')):
    coordinates, message = invalid
    try:
        candidate = cad_frame({'cad_box_mm': coordinates}, dark_page)
        render_dxf_png(frame_doc, frame_output, dark=True, frame=candidate)
    except ValueError as error:
        assert message in str(error)
    else:
        raise AssertionError('Invalid review viewport was accepted.')

assert feet_inches_mm('11 1/2"') == 292.1
assert feet_inches_mm('6′ - 3 1/2″') == 1917.7
feet_doc = ezdxf.new('R2010', units=4)
feet_dim = feet_doc.modelspace().add_linear_dim((0, 120), (0, 0), (1752.6, 0),
    dimstyle='Standard', override={'dimlfac': 1/25.4, 'dimlunit': 4, 'dimtxt': 48})
feet_dim.render()
feet_entity = feet_dim.dimension
feet_mark = feet_entity.get_geometry_block().query('MTEXT').first
feet_mark.text = '5\'-9"'
foot_page = {'id': 'fixture', 'dimensions': [
    {'handle': feet_entity.dxf.handle, 'unit': 'mm', 'display_unit': 'ft-in',
     'source_text': feet_mark.text, 'source_reading': 1752.6}]}
findings = []
check_dimension_inventory(foot_page, {feet_entity.dxf.handle: feet_entity}, .05, findings)
assert not findings
feet_mark.text = '5\'-8"'
check_dimension_inventory(foot_page, {feet_entity.dxf.handle: feet_entity}, .05, findings)
assert any('Visible feet-and-inches' in finding for finding in findings)

thread_dim = feet_doc.modelspace().add_aligned_dim((0, 0), (12, 0), 3, text='2-M<>',
    dimstyle='Standard', override={'dimdec': 0})
thread_dim.render()
thread = thread_dim.dimension
thread_mark = thread.get_geometry_block().query('MTEXT').first
thread_page = {'id': 'thread-fixture', 'dimensions': [
    {'handle': thread.dxf.handle, 'unit': 'mm', 'source_text': '2-M12', 'source_reading': 12}]}
assert thread_mark.plain_text() == '2-M12'
findings = []
check_dimension_inventory(thread_page, {thread.dxf.handle: thread}, .05, findings)
assert not findings
thread_mark.text = '1-M12'
check_dimension_inventory(thread_page, {thread.dxf.handle: thread}, .05, findings)
assert any('quantity qualifier' in finding for finding in findings)

feet_mark.text = '5\'-9"'
different_style = RUN/'changed-dimension-style.dxf'
changed = feet_entity.override()
changed.update({'dimlfac':1, 'dimlunit':2})
changed.commit()
feet_doc.saveas(different_style)
feet_dim.commit()
try:
    verify_page_dxf(feet_doc.units, list(feet_doc.modelspace()), different_style)
except ValueError as error:
    assert 'styles' in str(error)
else:
    raise AssertionError('Readback accepted a change to the native dimension overrides.')

meta = yaml.safe_load((PACKAGE / 'SKILL.md').read_text(encoding='utf-8').split('---', 2)[1])
assert meta['name'] == 'xy-drawing-restore' and meta['metadata']['version'] == '1.0.3'
assert meta['metadata']['author'] == 'XY (zhao960728-crypto)'
summary = {'source_pages': 5, 'scale_anchors': 10, 'dxf_entities': 30, 'negative_cases': len(negatives), 'overwrite_cases': 7, 'status': 'passed', 'model_accuracy_benchmarked': False}
(RUN / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
print(json.dumps(summary))
