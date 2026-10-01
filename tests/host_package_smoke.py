#!/usr/bin/env python3
"""Boot the QEMU/runtime payload extracted from an unsigned Host package.

Unprivileged acceptance only: this does not install or authenticate a package.
It deliberately exercises packaged firmware and libraries, not output/host.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
from moos_host_setup import load_payload
import qemu_release_smoke
import qemu_smoke


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error('run this test as an ordinary user, never root')
    with tempfile.TemporaryDirectory(prefix='moos-package-smoke-') as temporary:
        root = Path(temporary)
        subprocess.run(['/usr/bin/dpkg-deb', '--extract', str(args.package.resolve()),
                        str(root / 'extracted')], check=True)
        payload = root / 'extracted/usr/lib/moos/bootstrap'
        # Production demands root ownership; this fixture is intentionally
        # user-owned. All bounded reads/digests/inventory checks still run.
        manifest = load_payload(payload, check=lambda path, **kw: None)
        if 'images/bzImage' not in manifest['files']:
            parser.error('this acceptance requires a package containing Personal and QEMU')
        wrapper = root / 'run-package-qemu'
        wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(str(ROOT / 'scripts/run-qemu.sh'))
                           + ' --qemu ' + shlex.quote(str(payload / 'runtime/bin/qemu-system-x86_64'))
                           + ' "$@"\n')
        wrapper.chmod(0o755)
        qemu_smoke.RUNNER = wrapper
        qemu_smoke.TEST_IMAGE_DIR = str(payload / 'images')
        result = qemu_release_smoke.main()
        if result == 0:
            print('MOOS packaged QEMU, firmware, libraries and release image: PASS')
        return result


if __name__ == '__main__':
    raise SystemExit(main())
