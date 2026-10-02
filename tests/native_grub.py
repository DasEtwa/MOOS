#!/usr/bin/env python3
"""Pinned host GRUB image embedding and rejection, using disposable files only."""
import argparse
from pathlib import Path
import shutil
import struct
import subprocess

import native_boot
from native_test_disk import DisposableDisk


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, default=native_boot.ROOT / 'output/native/images/moos-native-x86_64.img')
    parser.add_argument('--output', type=Path, default=native_boot.ROOT / 'output/native')
    args = parser.parse_args()
    before = native_boot.digest(args.image)
    try:
        for fault in ['none', 'missing-bios-grub', 'undersized-bios-grub']:
            with DisposableDisk(args.image, args.output / 'host/sbin/debugfs') as disk:
                if fault == 'missing-bios-grub':
                    disk.mutate_entries(lambda entries: entries.__setitem__(slice(0, 16), bytes(16)))
                elif fault == 'undersized-bios-grub':
                    disk.mutate_entries(lambda entries: struct.pack_into('<Q', entries, 40, 2048))
                mapping = disk.workspace / 'device.map'
                mapping.write_text(f'(hd0) {disk.image}\n')
                # setup modifies core.img's sector map; isolate its inputs too.
                for name in ['boot.img', 'grub.img']:
                    shutil.copyfile(args.output / 'images' / name, disk.workspace / name)
                command = [str(args.output / 'host/sbin/grub-bios-setup'),
                           '--verbose', '--directory=' + str(disk.workspace),
                           '--boot-image=boot.img', '--core-image=grub.img',
                           '--device-map=' + str(mapping), '(hd0)']
                result = subprocess.run(command, capture_output=True, text=True, timeout=20)
                assert 'using mapped regular image' in result.stderr, result.stderr
                if fault == 'none':
                    assert result.returncode == 0, result.stderr
                else:
                    assert result.returncode != 0, 'unsafe embedding accepted: ' + fault
                    assert 'will not proceed with blocklists' in result.stderr or 'can\'t determine filesystem' in result.stderr, result.stderr
                print('Native GRUB image embedding: PASS: ' + fault)
        assert native_boot.digest(args.image) == before, 'canonical disk changed'
        return 0
    except (OSError, ValueError, AssertionError, subprocess.SubprocessError) as error:
        print('Native GRUB embedding: FAIL: ' + str(error))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
