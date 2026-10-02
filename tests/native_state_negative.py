#!/usr/bin/env python3
"""Real degraded boot boundaries; damaged DATA is never formatted or rewritten."""
import argparse
from pathlib import Path
import subprocess

import native_boot
from native_test_disk import DisposableDisk
import qemu_smoke


def rename_entry(entries, index, name):
    entries[index * 128 + 56:(index + 1) * 128] = name.encode('utf-16-le').ljust(72, b'\0')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, default=native_boot.ROOT / 'output/native/images/moos-native-x86_64.img')
    parser.add_argument('--debugfs', type=Path, default=native_boot.ROOT / 'output/native/host/sbin/debugfs')
    parser.add_argument('--qemu', type=Path)
    args = parser.parse_args()
    canonical = native_boot.digest(args.image)
    try:
        native_boot.inspect_disk(args.image, args.debugfs)
        for fault in ['missing-data', 'ambiguous-data', 'corrupt-data', 'wrong-data-uuid', 'newer-schema', 'invalid-schema-bytes', 'incomplete-identity', 'invalid-identity-links', 'invalid-identity-text', 'failure-after-rw']:
            with DisposableDisk(args.image, args.debugfs) as disk:
                disk.install_probe()
                if fault == 'missing-data':
                    disk.mutate_entries(lambda entries: rename_entry(entries, 4, 'LOST_DATA'))
                elif fault == 'ambiguous-data':
                    disk.mutate_entries(lambda entries: rename_entry(entries, 3, 'DATA'))
                elif fault == 'corrupt-data':
                    filesystem = disk.extract('DATA')
                    with filesystem.open('r+b') as stream:
                        stream.write(bytes(4096))
                    disk.replace('DATA', filesystem)
                elif fault in ['newer-schema', 'invalid-schema-bytes']:
                    version = disk.workspace / 'state-version.txt'
                    version.write_bytes(b'2\n' if fault == 'newer-schema' else b'1\0')
                    disk.edit_fs('DATA', ['rm /state-version', f'write {version} /state-version',
                                          'set_inode_field /state-version mode 0100600'])
                elif fault == 'wrong-data-uuid':
                    disk.edit_fs('DATA', ['set_super_value uuid 01010101-0101-4101-8101-010101010101'])
                elif fault == 'incomplete-identity':
                    disk.edit_fs('DATA', ['mkdir /identity', 'set_inode_field /identity mode 040700'])
                else:
                    console = disk.boot(qemu=args.qemu)
                    qemu_smoke.require(r'MOOS_N2_TEST_STATUS=READY', console, 'initial identity')
                    if fault == 'invalid-identity-links':
                        disk.edit_fs('DATA', ['set_inode_field /identity/installation-id links_count 2'])
                    elif fault == 'invalid-identity-text':
                        identity = disk.read_state('/identity/installation-id')
                        invalid = disk.workspace / 'invalid-identity.txt'
                        invalid.write_bytes(b'\n' + identity.rstrip(b'\n'))
                        disk.edit_fs('DATA', ['rm /identity/installation-id',
                                              f'write {invalid} /identity/installation-id',
                                              'set_inode_field /identity/installation-id mode 0100600'])
                    else:
                        # Fixed offline failure injection in the disposable system
                        # proves the production EXIT guard after rw, without adding
                        # a runtime test flag or command surface to the release.
                        import native_disk
                        system = disk.extract('SYSTEM_A')
                        hook = native_disk.fs_read(args.debugfs, system, '/etc/init.d/S20moos-state')
                        line = b'mount -o remount,rw "$STATE" || blocked \'DATA cannot become writable\'\n'
                        assert hook.count(line) == 1
                        fault_hook = disk.workspace / 'fault-hook.sh'
                        fault_hook.write_bytes(hook.replace(line, line + b'false # test-only failure after rw\n'))
                        disk.edit_fs('SYSTEM_A', ['rm /etc/init.d/S20moos-state',
                                                  f'write {fault_hook} /etc/init.d/S20moos-state',
                                                  'set_inode_field /etc/init.d/S20moos-state mode 0100755'])
                before = disk.data_digest()
                identity = disk.read_state('/identity/installation-id') if fault in ['invalid-identity-links', 'invalid-identity-text', 'failure-after-rw'] else None
                console = disk.boot(qemu=args.qemu)
                qemu_smoke.require(r'MOOS_N2_TEST_STATUS=BLOCKED', console, fault + ' blocks state')
                qemu_smoke.require(r'MOOS_N2_TEST_MOUNT=(ext4|tmpfs):.*\bro\b', console, fault + ' readonly state path')
                if fault != 'failure-after-rw' and disk.data_digest() != before:
                    raise AssertionError(fault + ' modified/reformatted DATA')
                if identity is not None and disk.read_state('/identity/installation-id') != identity:
                    raise AssertionError(fault + ' replaced installation identity')
                print('Native degraded boot: PASS: ' + fault, flush=True)
        if native_boot.digest(args.image) != canonical:
            raise AssertionError('negative test modified canonical image')
        return 0
    except (OSError, ValueError, AssertionError, RuntimeError, TimeoutError, subprocess.SubprocessError) as error:
        print('Native state negative: FAIL: ' + str(error))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
