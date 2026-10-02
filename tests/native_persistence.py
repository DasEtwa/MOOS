#!/usr/bin/env python3
"""Real reboot/system-replacement acceptance on private regular disk copies."""
import argparse
from pathlib import Path
import re
import subprocess

import native_boot
import native_disk
from native_test_disk import DisposableDisk
import qemu_smoke


def ready(console):
    qemu_smoke.require(r'MOOS_N2_TEST_STATUS=READY', console, 'ready persistent state')
    qemu_smoke.require(r'MOOS_N2_TEST_MOUNT=ext4:.*\brw\b', console, 'writable mounted DATA')
    identity = re.search(r'MOOS_N2_TEST_ID=([0-9a-f-]{36})', console).group(1)
    return identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, default=native_boot.ROOT / 'output/native/images/moos-native-x86_64.img')
    parser.add_argument('--debugfs', type=Path, default=native_boot.ROOT / 'output/native/host/sbin/debugfs')
    parser.add_argument('--boot', choices=['bios', 'uefi'], default='bios')
    parser.add_argument('--qemu', type=Path)
    parser.add_argument('--uefi-code', type=Path)
    parser.add_argument('--uefi-vars', type=Path)
    args = parser.parse_args()
    before = native_boot.digest(args.image)
    try:
        native_boot.inspect_disk(args.image, args.debugfs)
        options = dict(boot=args.boot, qemu=args.qemu, code=args.uefi_code, variables=args.uefi_vars)
        with DisposableDisk(args.image, args.debugfs) as disk:
            disk.install_probe()
            first = ready(disk.boot(**options))
            identity = disk.read_state('/identity/installation-id')
            assert identity == (first + '\n').encode()
            marker = disk.workspace / 'marker.txt'
            marker.write_text('native-n2-persistent-marker\n')
            disk.edit_fs('DATA', [f'write {marker} /config/persistence-marker',
                                  'set_inode_field /config/persistence-marker mode 0100600'])
            second_console = disk.boot(**options)
            assert ready(second_console) == first, 'identity changed after reboot'
            qemu_smoke.require(r'MOOS_N2_TEST_MARKER=native-n2-persistent-marker', second_console, 'marker after reboot')
            # Refresh only the exact SYSTEM_A range. DATA and its UUID/bytes are untouched.
            data_before = disk.data_digest()
            canonical_system = disk.workspace / 'canonical-system.ext4'
            native_disk.extract_partition(args.image, disk.partitions['SYSTEM_A'], canonical_system)
            disk.replace('SYSTEM_A', canonical_system)
            assert disk.data_digest() == data_before, 'system replacement changed DATA'
            disk.install_probe()
            third_console = disk.boot(**options)
            assert ready(third_console) == first, 'identity changed after system replacement'
            qemu_smoke.require(r'MOOS_N2_TEST_MARKER=native-n2-persistent-marker', third_console, 'marker after system replacement')
            assert disk.read_state('/identity/installation-id') == identity
            assert disk.read_state('/config/persistence-marker') == marker.read_bytes()
        # An independently booted fresh clone must receive a different identity.
        with DisposableDisk(args.image, args.debugfs) as clone:
            clone.install_probe()
            assert ready(clone.boot(**options)) != first, 'fresh clones share installation identity'
        assert native_boot.digest(args.image) == before, 'canonical image changed'
        print(f'Native {args.boot} persistence: PASS; reboot, SYSTEM_A replacement, distinct fresh-clone identity, clean shutdown')
        return 0
    except (OSError, ValueError, AssertionError, RuntimeError, TimeoutError, subprocess.SubprocessError) as error:
        print('Native persistence: FAIL: ' + str(error))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
