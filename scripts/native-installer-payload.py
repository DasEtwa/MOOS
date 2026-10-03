#!/usr/bin/env python3
"""Create a bounded offline Core payload; SHA256 is integrity, not authenticity."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import re


def regular(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('payload must be a single-link regular file: ' + str(path))
    return info


def digest_stream(stream):
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() and (args.output.is_symlink() or not args.output.is_dir()):
        raise ValueError('unsafe payload directory')
    args.output.mkdir(exist_ok=True)
    allowed = {'native-boot.vfat','rootfs.ext2','boot.img','grub.img','bzImage','bootx64.efi','manifest.json'}
    for path in args.output.iterdir():
        if path.name not in allowed:
            raise ValueError('unexpected generated payload member')
        regular(path)
    entries = []
    for role, name, size in [('BOOT','native-boot.vfat',16*1024**2), ('SYSTEM_A','rootfs.ext2',60*1024**2), ('BIOS_BOOT','boot.img',512), ('BIOS_CORE','grub.img',None), ('KERNEL','bzImage',None), ('UEFI','bootx64.efi',None)]:
        source, target = args.source / name, args.output / name
        if name == 'bootx64.efi':
            source = args.source / 'efi-part/EFI/BOOT/bootx64.efi'
        info = regular(source)
        if size is not None and info.st_size != size or not 0 < info.st_size <= 60*1024**2:
            raise ValueError('incorrect payload size')
        if target.exists() or target.is_symlink():
            regular(target)
        shutil.copyfile(source, target)
        with target.open('rb') as stream:
            digest = digest_stream(stream)
        entries.append(dict(role=role,file=name,size=info.st_size,sha256=digest))
    manifest = args.output / 'manifest.json'
    if manifest.exists() or manifest.is_symlink():
        regular(manifest)
    release = Path(__file__).resolve().parents[1] / 'system/overlay/etc/moos-release'
    match = re.search(r"^MOOS_VERSION='([A-Za-z0-9.+-]{1,64})'$", release.read_text(), re.MULTILINE)
    if not match:
        raise ValueError('missing bounded MOOS version')
    manifest.write_text(json.dumps(dict(schema=1,version=match.group(1),entries=entries),sort_keys=True,indent=2)+'\n')
    for path in args.output.iterdir():
        os.utime(path, (int(os.environ.get('SOURCE_DATE_EPOCH','1790812800')),)*2, follow_symlinks=False)


if __name__ == '__main__':
    main()
