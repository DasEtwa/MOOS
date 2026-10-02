"""Disposable image mutation/boot helpers. Never shipped in a Native rootfs."""
from pathlib import Path
import os
import shutil
import struct
import subprocess
import tempfile
import zlib

import native_boot
import native_disk
import qemu_smoke

# Injected offline only into a disposable SYSTEM_A, never into a distribution.
# Fixed telemetry and poweroff; no guest shell, command API or login bypass.
HOOK = b'''#!/bin/sh
set -eu
case "$1" in
start)
    status=$(cat /run/moos-native-state)
    printf 'MOOS_N2_TEST_STATUS=%s\\n' "$status"
    awk '$2 == "/var/lib/moos" {printf "MOOS_N2_TEST_MOUNT=%s:%s\\n", $3, $4}' /proc/mounts
    if [ "$status" = READY ]; then
        printf 'MOOS_N2_TEST_ID=%s\\n' "$(cat /var/lib/moos/identity/installation-id)"
        if [ -f /var/lib/moos/config/persistence-marker ]; then
            printf 'MOOS_N2_TEST_MARKER=%s\\n' "$(head -c 64 /var/lib/moos/config/persistence-marker)"
        fi
    fi
    sync
    poweroff
    ;;
stop) ;;
*) exit 2 ;;
esac
'''


class DisposableDisk:
    def __init__(self, source, debugfs):
        native_disk.regular_file(source)
        self.temporary = tempfile.TemporaryDirectory(prefix='moos-native-persistence-', dir='/tmp')
        self.workspace = Path(self.temporary.name)
        self.image = self.workspace / 'disk.img'
        shutil.copyfile(source, self.image)
        self.image.chmod(0o600)
        (self.workspace / '.disposable-native-copy').write_text('disposable-native-copy-v1\n')
        self.partitions = {p.name: p for p in native_disk.read_gpt(self.image)}
        self.debugfs = debugfs

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.temporary.cleanup()

    def _check_owned(self):
        info = native_disk.regular_file(self.image)
        if info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o777 != 0o600:
            raise ValueError('unsafe disposable test image')

    def extract(self, role):
        self._check_owned()
        path = self.workspace / (role.lower() + '.ext4')
        native_disk.extract_partition(self.image, self.partitions[role], path)
        return path

    def replace(self, role, source):
        self._check_owned()
        info = native_disk.regular_file(source)
        partition = self.partitions[role]
        if info.st_size != partition.size:
            raise ValueError('replacement must match exact test partition size')
        with Path(source).open('rb') as stream, self.image.open('r+b') as destination:
            destination.seek(partition.offset)
            remaining = partition.size
            while remaining:
                chunk = stream.read(min(remaining, native_disk.MIB))
                if not chunk:
                    raise ValueError('truncated test replacement')
                destination.write(chunk)
                remaining -= len(chunk)

    def edit_fs(self, role, commands):
        filesystem = self.extract(role)
        for command in commands:
            result = subprocess.run([str(self.debugfs), '-w', '-R', command, str(filesystem)],
                                    capture_output=True, text=True, check=True, timeout=10)
            if any(error in result.stderr for error in ['Filesystem not open', 'File not found', 'Command not found']):
                raise ValueError(result.stderr)
        self.replace(role, filesystem)

    def read_state(self, path):
        return native_disk.fs_read(self.debugfs, self.extract('DATA'), path, missing_ok=True)

    def data_digest(self):
        return native_boot.digest(self.extract('DATA'))

    def install_probe(self):
        hook = self.workspace / 'probe.sh'
        hook.write_bytes(HOOK)
        self.edit_fs('SYSTEM_A', [f'write {hook} /etc/init.d/S99moos-test-state',
                                 'set_inode_field /etc/init.d/S99moos-test-state mode 0100755'])
        system = self.extract('SYSTEM_A')
        native_boot.validator.validate_shadow(native_boot.validator.read_image_shadow(self.debugfs, system))

    def mutate_entries(self, change):
        """Create CRC-valid role faults; all writes stay in this private copy."""
        self._check_owned()
        sectors = self.image.stat().st_size // 512
        with self.image.open('r+b') as stream:
            stream.seek(1024)
            entries = bytearray(stream.read(128 * 128))
            change(entries)
            for lba, table in [(1, 2), (sectors - 1, sectors - 33)]:
                stream.seek(table * 512)
                stream.write(entries)
                stream.seek(lba * 512)
                header = bytearray(stream.read(512))
                struct.pack_into('<I', header, 88, zlib.crc32(entries))
                struct.pack_into('<I', header, 16, 0)
                struct.pack_into('<I', header, 16, zlib.crc32(header[:92]))
                stream.seek(lba * 512)
                stream.write(header)

    def boot(self, *, boot='bios', qemu=None, code=None, variables=None):
        self._check_owned()
        arguments = ['--test-writable-copy', str(self.workspace), '--boot', boot]
        for flag, value in [('--qemu', qemu), ('--uefi-code', code), ('--uefi-vars', variables)]:
            if value:
                arguments.extend([flag, str(value)])
        firmware = {path: native_boot.digest(path) for path in [code, variables] if path}
        session = native_boot.NativeSession(arguments)
        try:
            console = session.read_until(r'reboot: Power down', 90)
            qemu_smoke.require(r'MOOS Native disk boot \(GRUB\)', console, 'GRUB disk boot')
            qemu_smoke.require(r'MOOS_N2_TEST_STATUS=(READY|BLOCKED)', console, 'test-only state probe')
            if session.wait_for_exit(10) != 0:
                raise AssertionError('disposable guest did not shut down cleanly')
            if any(native_boot.digest(path) != checksum for path, checksum in firmware.items()):
                raise AssertionError('persistence test modified firmware template')
            return console
        except Exception:
            print(session.output[-5000:])
            raise
        finally:
            session.close()
