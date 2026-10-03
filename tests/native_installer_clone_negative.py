#!/usr/bin/env python3
"""Cloned installed disks must fail before an ambiguous SYSTEM_A is mounted."""
import argparse
import importlib.util
import os
from pathlib import Path
import pty
import shutil
import tempfile
import uuid

import native_boot
import qemu_smoke

ROOT = native_boot.ROOT
spec = importlib.util.spec_from_file_location('n3_runner', ROOT / 'scripts/run-native-installer-qemu.py')
runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--boot', choices=['bios', 'uefi'], default='bios')
    parser.add_argument('--uefi-code', type=Path)
    parser.add_argument('--uefi-vars', type=Path)
    parser.add_argument('--qemu', type=Path, default=ROOT/'output/host/bin/qemu-system-x86_64')
    args = parser.parse_args()
    installed = ROOT / 'output/native-installer/acceptance' / args.boot / 'a.img'
    if not installed.is_file():
        raise SystemExit('run native_installer.py for this boot mode first')
    original = native_boot.digest(installed)
    with tempfile.TemporaryDirectory(prefix='moos-native-installer-', dir='/tmp') as name:
        workspace = Path(name)
        for disk in ['a', 'b']:
            shutil.copyfile(installed, workspace / f'{disk}.img')
            (workspace / f'{disk}.img').chmod(0o600)
            (workspace / f'{disk}.serial').write_text('MOOS-N3-TARGET-' + uuid.uuid4().hex[:5] + '\n')
        args.workspace = workspace
        args.image = ROOT / 'output/native-installer/images/moos-native-installer-x86_64.img'
        argv = runner.command(args)
        for argument in [
            'file=/moos/installer.img,if=none,id=source,format=raw,readonly=on',
            'virtio-blk-pci,drive=source,serial=MOOS-N3-INSTALLER,bootindex=1',
        ]:
            index = argv.index(argument); del argv[index - 1:index + 1]
        for disk in ['a', 'b']:
            index = argv.index(f'file=/moos/{disk}.img,if=none,id={disk},format=raw,snapshot=off')
            argv[index] = argv[index].replace('snapshot=off', 'snapshot=on')
        b_serial = (workspace / 'b.serial').read_text().strip()
        index = argv.index(f'virtio-blk-pci,drive=b,serial={b_serial}')
        argv[index] += ',bootindex=1'
        before = {path: native_boot.digest(path) for path in [workspace/'a.img', workspace/'b.img']}
        session = qemu_smoke.QemuSession.__new__(qemu_smoke.QemuSession)
        session.output = ''; session.exit_status = None
        session.pid, session.fd = pty.fork()
        if session.pid == 0:
            argv, fds = runner.pin_bindings(argv, args.canonical_inodes)
            os.execvp(argv[0], argv)
        try:
            session.read_until(r'duplicate PARTUUID=.*refusing ambiguous root', 120)
            assert 'MOOS Native state: DATA READY' not in session.output
            assert 'moos-native login:' not in session.output
            session.send('\x01x'); assert session.wait_for_exit(10) == 0
        finally:
            session.close()
        assert all(native_boot.digest(path) == digest for path, digest in before.items())
    assert native_boot.digest(installed) == original
    print(f'N3 {args.boot}: PASS duplicate installed identities failed before root/DATA acceptance')


if __name__ == '__main__':
    main()
