#!/usr/bin/env python3
"""Read-only generated installer GPT, payload and ephemeral-runtime inspection."""
import gzip
import json
from pathlib import Path
import subprocess
import tempfile

import native_boot
import native_disk

ROOT = native_boot.ROOT


def cpio_members(path):
    with gzip.open(path,'rb') as stream:
        data=stream.read(64*native_disk.MIB+1)
    assert len(data)<=64*native_disk.MIB,'installer rootfs exceeds bounded test limit'
    members={};offset=0
    while True:
        header=data[offset:offset+110];assert header[:6]==b'070701'
        fields=[int(header[n:n+8],16) for n in range(6,110,8)]
        size,name_size=fields[6],fields[11]
        name=data[offset+110:offset+110+name_size-1].decode().removeprefix('./')
        offset=(offset+110+name_size+3)&~3
        body=data[offset:offset+size];assert len(body)==size
        offset=(offset+size+3)&~3
        if name=='TRAILER!!!':break
        assert name not in members
        members[name]=(fields[1],body)
    return members


def main():
    images=ROOT/'output/native-installer/images'
    image=images/'moos-native-installer-x86_64.img'
    before=native_boot.digest(image)
    parts=native_disk.read_gpt(image,media=True)
    with tempfile.TemporaryDirectory(prefix='moos-n3-media-') as directory:
        directory=Path(directory)
        manifest=directory/'manifest.json'
        subprocess.run([str(ROOT/'output/native-installer/host/bin/mcopy'),'-i',str(image)+'@@'+str(parts[1].offset),'::/payload/manifest.json',str(manifest)],check=True,timeout=15)
        payload=json.loads(manifest.read_text())
        assert payload['schema']==1 and len(payload['entries'])==6
        assert [p['role'] for p in payload['entries']]==['BOOT','SYSTEM_A','BIOS_BOOT','BIOS_CORE','KERNEL','UEFI']
        for entry in payload['entries']:
            member=directory/entry['file']
            subprocess.run([str(ROOT/'output/native-installer/host/bin/mcopy'),'-i',str(image)+'@@'+str(parts[1].offset),'::/payload/'+entry['file'],str(member)],check=True,timeout=15)
            assert member.stat().st_size==entry['size'] and native_boot.digest(member)==entry['sha256']
        native_boot.validator.validate_shadow(native_boot.validator.read_image_shadow(ROOT/'output/native/host/sbin/debugfs',directory/'rootfs.ext2'))
    members=cpio_members(images/'rootfs.cpio.gz')
    assert members['etc/shadow'][1].split(b':')[1].startswith((b'!',b'*'))
    init=members['etc/inittab'][1]
    assert b'moos-native-installer' in init and b'getty' not in init and b'askfirst' not in init
    assert not any('python' in name for name in members)
    binary=members['usr/bin/moos-native-installer'][1]
    assert binary.startswith(b'\x7fELF')
    assert native_boot.digest(image)==before
    print(f'Installer media: PASS GPT+offline manifest+locked ephemeral runtime; disk={image.stat().st_size}; cpio.gz={(images/"rootfs.cpio.gz").stat().st_size}; Rust ELF={len(binary)}; SHA256={before}')


if __name__=='__main__':main()
