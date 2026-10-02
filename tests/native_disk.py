"""Bounded regular-file GPT/filesystem inspection for Native acceptance only."""
from dataclasses import dataclass
import os
from pathlib import Path
import stat
import struct
import subprocess
import uuid
import zlib

MIB = 1024 * 1024
DISK_SIZE = 171 * MIB
DISK_UUID = uuid.UUID('4d4f4f53-0000-4000-8000-000000000000')
BIOS_TYPE = uuid.UUID('21686148-6449-6e6f-744e-656564454649')
EFI_TYPE = uuid.UUID('c12a7328-f81f-11d2-ba4b-00a0c93ec93b')
LINUX_TYPE = uuid.UUID('0fc63daf-8483-4772-8e79-3d69d8477de4')
ROLES = [('BIOS_GRUB', BIOS_TYPE, 1, 1), ('BOOT', EFI_TYPE, 2, 16),
         ('SYSTEM_A', LINUX_TYPE, 18, 60), ('SYSTEM_B', LINUX_TYPE, 78, 60),
         ('DATA', LINUX_TYPE, 138, 32)]
HEADER = struct.Struct('<8sIIIIQQQQ16sQIII')


@dataclass(frozen=True)
class Partition:
    name: str
    kind: uuid.UUID
    identity: uuid.UUID
    first: int
    last: int
    flags: int

    @property
    def offset(self):
        return self.first * 512

    @property
    def size(self):
        return (self.last - self.first + 1) * 512


def regular_file(path):
    info = Path(path).lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('Native disk must be a regular file, not a symlink/device')
    return info


def read_gpt(path, *, strict_roles=True, installed=False, media=False):
    info = regular_file(path)
    expected_size = 131 * MIB if media else DISK_SIZE
    expected_id = uuid.UUID('4d4f4f53-0000-4000-9000-000000000000') if media else DISK_UUID
    if media and installed or (not installed and info.st_size != expected_size) or (installed and not 192 * MIB <= info.st_size <= 64 * 1024**3):
        raise ValueError('truncated or unexpected Native GPT disk size')
    with Path(path).open('rb') as stream:
        mbr = stream.read(512)
        if mbr[510:] != b'\x55\xaa' or mbr[450] != 0xEE:
            raise ValueError('missing protective MBR boot signature/type')
        if struct.unpack_from('<II', mbr, 454) != (1, info.st_size // 512 - 1) or any(mbr[462:510]):
            raise ValueError('invalid protective MBR extent')

        def header_at(lba):
            stream.seek(lba * 512)
            data = bytearray(stream.read(512))
            fields = HEADER.unpack_from(data)
            signature, revision, size, crc, reserved, current, backup, first, last, disk, table, count, entry_size, table_crc = fields
            if signature != b'EFI PART' or revision != 0x10000 or size != HEADER.size or reserved:
                raise ValueError('invalid GPT header')
            struct.pack_into('<I', data, 16, 0)
            if zlib.crc32(data[:size]) != crc:
                raise ValueError('GPT header checksum mismatch')
            disk_id = uuid.UUID(bytes_le=disk)
            if current != lba or count != 128 or entry_size != 128 or (not installed and disk_id != expected_id) or (installed and (disk_id.version != 4 or str(disk_id).startswith('4d4f4f53-'))):
                raise ValueError('unexpected GPT metadata')
            if (first not in (34, 2048) if installed else first != 34) or last != info.st_size // 512 - 34:
                raise ValueError('invalid GPT usable bounds')
            if table != (2 if lba == 1 else lba - 32):
                raise ValueError('invalid GPT entry-array location')
            stream.seek(table * 512)
            entries = stream.read(count * entry_size)
            if len(entries) != count * entry_size or zlib.crc32(entries) != table_crc:
                raise ValueError('GPT entry-array checksum mismatch')
            return fields, entries

        primary, entries = header_at(1)
        secondary, backup_entries = header_at(info.st_size // 512 - 1)
        if primary[6] != secondary[5] or secondary[6] != 1 or primary[7:10] != secondary[7:10] or entries != backup_entries:
            raise ValueError('GPT primary/backup mismatch')
        partitions = []
        for index in range(128):
            entry = entries[index * 128:(index + 1) * 128]
            if entry[:16] == bytes(16):
                if any(entry):
                    raise ValueError('nonzero unused GPT entry')
                continue
            first, last, flags = struct.unpack_from('<QQQ', entry, 32)
            name = entry[56:128].decode('utf-16-le').rstrip('\0')
            if not name or '\0' in name or first < primary[7] or first > last or last > primary[8]:
                raise ValueError('invalid GPT partition range/name')
            partitions.append(Partition(name, uuid.UUID(bytes_le=entry[:16]),
                                        uuid.UUID(bytes_le=entry[16:32]), first, last, flags))
        if len({p.name for p in partitions}) != len(partitions) or len({p.identity for p in partitions}) != len(partitions):
            raise ValueError('duplicate/ambiguous GPT role or identity')
        ordered = sorted(partitions, key=lambda p: p.first)
        if any(left.last >= right.first for left, right in zip(ordered, ordered[1:])):
            raise ValueError('overlapping GPT partitions')
        if strict_roles:
            roles = [('BIOS_GRUB',BIOS_TYPE,1,1),('INSTALLER',EFI_TYPE,2,128)] if media else ROLES
            if len(partitions) != len(roles):
                raise ValueError('missing or unexpected Native GPT roles')
            for number, (partition, expected) in enumerate(zip(partitions, roles), 1):
                name, kind, offset, size = expected
                identity = partition.identity if installed else uuid.UUID(f'4d4f4f53-0000-4000-{9000 if media else 8000}-{number:012d}')
                if installed and (identity.version != 4 or str(identity).startswith('4d4f4f53-')):
                    raise ValueError('prototype/invalid installed identity')
                if installed and name == 'DATA':
                    size = info.st_size // MIB - 139
                if (partition.name, partition.kind, partition.identity, partition.offset, partition.size) != (name, kind, identity, offset * MIB, size * MIB):
                    raise ValueError('incorrect Native GPT partition role: ' + name)
                if partition.flags != ((1 << 63) if name == 'SYSTEM_B' else 0):
                    raise ValueError('incorrect Native GPT role flags')
        return partitions


def extract_partition(disk, partition, destination):
    regular_file(disk)
    with Path(disk).open('rb') as source, Path(destination).open('wb') as target:
        source.seek(partition.offset)
        remaining = partition.size
        while remaining:
            chunk = source.read(min(remaining, MIB))
            if not chunk:
                raise ValueError('truncated Native partition')
            target.write(chunk)
            remaining -= len(chunk)


def fs_read(debugfs, image, path, *, missing_ok=False):
    result = subprocess.run([str(debugfs), '-R', 'cat ' + path, str(image)],
                            capture_output=True, timeout=10, check=True)
    if b'File not found' in result.stderr:
        if missing_ok:
            return None
        raise ValueError('missing filesystem member: ' + path)
    if b'Filesystem not open' in result.stderr or b'Bad magic' in result.stderr:
        raise ValueError('invalid Native filesystem')
    return result.stdout
