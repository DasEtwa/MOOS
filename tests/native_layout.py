#!/usr/bin/env python3
"""GPT boundary regressions using synthetic regular files, without a VM."""
from pathlib import Path
import struct
import tempfile
import unittest
import uuid
import zlib

import native_disk


def make_disk(path, change=None):
    entries = bytearray(128 * 128)
    for index, (name, kind, offset, size) in enumerate(native_disk.ROLES):
        start = index * 128
        entries[start:start + 16] = kind.bytes_le
        entries[start + 16:start + 32] = uuid.UUID(f'4d4f4f53-0000-4000-8000-{index + 1:012d}').bytes_le
        struct.pack_into('<QQQ', entries, start + 32, offset * 2048, (offset + size) * 2048 - 1,
                         (1 << 63) if name == 'SYSTEM_B' else 0)
        encoded = name.encode('utf-16-le')
        entries[start + 56:start + 56 + len(encoded)] = encoded
    if change:
        change(entries)
    sectors = native_disk.DISK_SIZE // 512

    def header(current, backup, table):
        data = bytearray(512)
        native_disk.HEADER.pack_into(data, 0, b'EFI PART', 0x10000, 92, 0, 0,
                                    current, backup, 34, sectors - 34,
                                    native_disk.DISK_UUID.bytes_le, table, 128, 128, zlib.crc32(entries))
        struct.pack_into('<I', data, 16, zlib.crc32(data[:92]))
        return data

    with path.open('wb') as stream:
        stream.truncate(native_disk.DISK_SIZE)
        mbr = bytearray(512)
        mbr[510:] = b'\x55\xaa'
        mbr[450] = 0xEE
        struct.pack_into('<II', mbr, 454, 1, sectors - 1)
        stream.write(mbr)
        stream.write(header(1, sectors - 1, 2))
        stream.write(entries)
        stream.seek((sectors - 33) * 512)
        stream.write(entries)
        stream.write(header(sectors - 1, 1, sectors - 33))


class GptBoundaries(unittest.TestCase):
    def test_valid_gpt_and_reserved_slot(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'disk.img'
            make_disk(path)
            partitions = native_disk.read_gpt(path)
            self.assertEqual([p.name for p in partitions], [p[0] for p in native_disk.ROLES])
            self.assertEqual(partitions[3].flags, 1 << 63)

    def test_checksums_backup_and_truncation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'disk.img'
            for offset in [512 + 56, 1024 + 72, native_disk.DISK_SIZE - 512 + 56]:
                make_disk(path)
                with path.open('r+b') as stream:
                    stream.seek(offset)
                    stream.write(b'\xff')
                with self.assertRaisesRegex(ValueError, 'checksum'):
                    native_disk.read_gpt(path)
            with path.open('r+b') as stream:
                stream.truncate(native_disk.DISK_SIZE - 512)
            with self.assertRaisesRegex(ValueError, 'truncated'):
                native_disk.read_gpt(path)

    def test_role_type_missing_and_ambiguity(self):
        changes = [
            lambda entries: entries.__setitem__(slice(4 * 128, 5 * 128), bytes(128)),
            lambda entries: entries.__setitem__(slice(4 * 128, 4 * 128 + 16), native_disk.EFI_TYPE.bytes_le),
            lambda entries: entries.__setitem__(slice(4 * 128 + 56, 5 * 128), entries[3 * 128 + 56:4 * 128]),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'disk.img'
            for change in changes:
                make_disk(path, change)
                with self.assertRaisesRegex(ValueError, 'role|ambiguous'):
                    native_disk.read_gpt(path)

    def test_symlink_and_special_file_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'disk.img'
            make_disk(path)
            link = Path(temporary) / 'link.img'
            link.symlink_to(path)
            for unsafe in [link, Path('/dev/null')]:
                with self.assertRaisesRegex(ValueError, 'regular file'):
                    native_disk.read_gpt(unsafe)


if __name__ == '__main__':
    unittest.main()
