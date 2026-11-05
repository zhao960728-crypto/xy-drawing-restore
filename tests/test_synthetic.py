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
from PIL import Image, ImageDraw
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
import yaml

BASE = Path(__file__).resolve().parents[1]
PACKAGE = BASE / 'skills/xy-drawing-restore'
SCRIPT = PACKAGE / 'scripts/restore_tools.py'
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
meta = yaml.safe_load((PACKAGE / 'SKILL.md').read_text(encoding='utf-8').split('---', 2)[1])
assert meta['name'] == 'xy-drawing-restore' and meta['metadata']['version'] == '1.0.2'
assert meta['metadata']['author'] == 'XY (zhao960728-crypto)'
summary = {'source_pages': 5, 'scale_anchors': 10, 'dxf_entities': 30, 'negative_cases': len(negatives), 'overwrite_cases': 7, 'status': 'passed', 'model_accuracy_benchmarked': False}
(RUN / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
print(json.dumps(summary))
