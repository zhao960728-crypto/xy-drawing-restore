#!/usr/bin/env python3
# Copyright 2026 XY (GitHub: zhao960728-crypto)
# SPDX-License-Identifier: Apache-2.0
"""Build a small, explicit source-only skill distribution."""
from pathlib import Path
import hashlib
import json
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills/xy-drawing-restore'
FILES = ['SKILL.md', 'references/workflow.md', 'scripts/bootstrap.py',
         'scripts/restore_tools.py', 'scripts/restoration_geometry.py', 'scripts/delivery.py',
         'scripts/page_exports.py', 'scripts/requirements.txt']
NOTICES = ['LICENSE', 'NOTICE', 'COMMERCIAL_USE.md', 'THIRD_PARTY_NOTICES.md']


def main():
    for name in NOTICES:
        (SKILL / name).write_bytes((ROOT / name).read_bytes())
    output = ROOT / 'dist'
    output.mkdir(exist_ok=True)
    metadata = (SKILL / 'SKILL.md').read_text('utf-8').split('---', 2)[1]
    version = re.search(r'^  version: (\d+\.\d+\.\d+)$', metadata, re.M).group(1)
    archive = output / f'xy-drawing-restore-{version}.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name in FILES + NOTICES:
            path = SKILL / name
            assert path.is_file() and not path.is_symlink()
            z.write(path, 'xy-drawing-restore/' + name)
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        assert len(z.namelist()) == len(FILES) + len(NOTICES)
    assert archive.stat().st_size < 3_000_000
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / 'SHA256SUMS').write_text(digest + '  ' + archive.name + '\n', encoding='utf-8')
    print(json.dumps({'archive': archive.name, 'bytes': archive.stat().st_size,
                      'sha256': digest}))


if __name__ == '__main__':
    main()
