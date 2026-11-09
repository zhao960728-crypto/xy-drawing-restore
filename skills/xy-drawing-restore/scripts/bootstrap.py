#!/usr/bin/env python3
# Copyright 2026 XY (GitHub: zhao960728-crypto)
# SPDX-License-Identifier: Apache-2.0
"""Create a task-owned Python environment, without touching system packages."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import venv


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if sys.version_info < (3, 10):
        raise RuntimeError('This skill requires Python 3.10 or newer.')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', required=True)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    root = Path(args.env).resolve()
    interpreter = root / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    if not args.check_only:
        if root.exists() and not (root / 'pyvenv.cfg').is_file():
            raise ValueError('The target exists and is not a virtual environment. Choose a new task directory.')
        if not interpreter.is_file():
            venv.EnvBuilder(with_pip=True).create(root)
        subprocess.run([str(interpreter), '-m', 'pip', 'install', '-r',
                        str(Path(__file__).with_name('requirements.txt'))], check=True)
    if not interpreter.is_file():
        raise ValueError('Task Python environment does not exist.')
    code = ('import json, importlib.metadata as m; '
            'import ezdxf, PIL, matplotlib, contourpy, shapely, pdfplumber, pypdfium2; '
            'print(json.dumps({n:m.version(n) for n in '
            '["ezdxf","Pillow","matplotlib","contourpy","shapely","pdfplumber","pypdfium2"]}))')
    result = subprocess.run([str(interpreter), '-c', code], check=True, capture_output=True, text=True)
    print(json.dumps({'python': str(interpreter), 'versions': json.loads(result.stdout)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
