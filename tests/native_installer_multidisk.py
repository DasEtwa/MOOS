#!/usr/bin/env python3
"""Boot the second of two installed disks; all backings are disposable copies."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import pty
import shutil
import sys
import tempfile
import uuid

import native_boot
import native_disk
import native_persistence
from native_test_disk import DisposableDisk
import qemu_smoke
from native_safe_output import ensure_directory, safe_digest, safe_read_text, safe_write_text

ROOT=native_boot.ROOT
spec=importlib.util.spec_from_file_location('n3_runner',ROOT/'scripts/run-native-installer-qemu.py')
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--boot',choices=['bios','uefi'],default='bios')
    parser.add_argument('--uefi-code',type=Path)
    parser.add_argument('--uefi-vars',type=Path)
    parser.add_argument('--qemu',type=Path,default=ROOT/'output/host/bin/qemu-system-x86_64')
    args=parser.parse_args()
    results=ROOT/'output/native-installer/acceptance'
    ensure_directory(results)
    images=[results/'bios/a.img',results/'uefi/a.img']
    identities=[json.loads(safe_read_text(results/mode/'identities.json',max_bytes=8192)) for mode in ['bios','uefi']]
    for key in ['disk_guid','installation_id']:
        assert identities[0][key]!=identities[1][key]
    assert all(a!=b for a,b in zip(identities[0]['partitions'],identities[1]['partitions']))
    originals={p:safe_digest(p,max_bytes=512*native_disk.MIB,expected_size=512*native_disk.MIB) for p in images}
    with tempfile.TemporaryDirectory(prefix='moos-native-installer-',dir='/tmp') as directory:
        workspace=Path(directory)
        for name,image in zip(['a','b'],images):
            with DisposableDisk(image,ROOT/'output/native/host/sbin/debugfs',installed=True) as probe:
                probe.install_probe()
                shutil.copyfile(probe.image,workspace/(name+'.img'))
            (workspace/(name+'.img')).chmod(0o600)
            (workspace/(name+'.serial')).write_text('MOOS-N3-TARGET-'+uuid.uuid4().hex[:5]+'\n')
        args.workspace=workspace;args.image=ROOT/'output/native-installer/images/moos-native-installer-x86_64.img'
        argv=runner.command(args)
        # Reuse strict private-file sandbox policy; omit source device and boot B.
        source_drive='file=/moos/installer.img,if=none,id=source,format=raw,readonly=on'
        source_device='virtio-blk-pci,drive=source,serial=MOOS-N3-INSTALLER,bootindex=1'
        for argument in [source_drive,source_device]:
            index=argv.index(argument);del argv[index-1:index+1]
        for name in ['a','b']:
            index=argv.index(f'file=/moos/{name}.img,if=none,id={name},format=raw,snapshot=off')
            argv[index]=argv[index].replace('snapshot=off','snapshot=on')
        serial=(workspace/'b.serial').read_text().strip()
        index=argv.index(f'virtio-blk-pci,drive=b,serial={serial}')
        argv[index]+=',bootindex=1'
        before={workspace/(n+'.img'):native_boot.digest(workspace/(n+'.img')) for n in ['a','b']}
        session=qemu_smoke.QemuSession.__new__(qemu_smoke.QemuSession)
        session.output='';session.exit_status=None;session.pid,session.fd=pty.fork()
        if session.pid==0:
            argv,fds=runner.pin_bindings(argv,args.canonical_inodes)
            os.execvp(argv[0],argv)
        try:
            output=session.read_until('reboot: Power down',120)
            assert native_persistence.ready(output)==identities[1]['installation_id'], 'firmware-selected second disk was not used'
            assert session.wait_for_exit(15)==0
            safe_write_text(results/(args.boot+'-multidisk.log'),session.output)
        finally:session.close()
        assert all(native_boot.digest(p)==d for p,d in before.items())
    assert all(safe_digest(p,max_bytes=512*native_disk.MIB,expected_size=512*native_disk.MIB)==d for p,d in originals.items())
    print(f'N3 {args.boot}: PASS two installs unique; firmware-selected second disk booted its own SYSTEM_A/DATA; readonly backings unchanged')


if __name__=='__main__':main()
