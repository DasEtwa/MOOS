#!/usr/bin/env python3
"""Offline installer and installed-system acceptance on private QEMU files only."""
import argparse
import json
import os
from pathlib import Path
import pty
import re
import shutil
import sys
import tempfile
import uuid

import native_boot
import native_disk
from native_test_disk import DisposableDisk
import native_persistence
import qemu_smoke
from native_safe_output import ensure_directory, safe_copy_file, safe_write_text

ROOT=native_boot.ROOT
RUNNER=ROOT/'scripts/run-native-installer-qemu.py'


class InstallerSession(qemu_smoke.QemuSession):
    def __init__(self, arguments):
        self.pid,self.fd=pty.fork()
        self.output='';self.exit_status=None
        if self.pid == 0:
            os.execv(sys.executable,[sys.executable,str(RUNNER),*arguments])

    def _read_available(self):
        super()._read_available()
        if len(self.output)>1024*1024:
            raise RuntimeError('installer console exceeded 1 MiB')

    def command(self, line):
        self.send(line+'\n')
        return self.read_until(r'installer> ',300)


def boot_installed(image, firmware):
    parts=native_disk.read_gpt(image,installed=True)
    debugfs=ROOT/'output/native/host/sbin/debugfs'
    with tempfile.TemporaryDirectory(prefix='moos-n3-inspect-') as directory:
        system=Path(directory)/'system.ext4'
        native_disk.extract_partition(image,parts[2],system)
        native_boot.validator.validate_shadow(native_boot.validator.read_image_shadow(debugfs,system))
        for forbidden in ['/etc/init.d/S99moos-test-state','/usr/bin/moos-native-installer','/usr/bin/python3']:
            assert native_disk.fs_read(debugfs,system,forbidden,missing_ok=True) is None
    before=native_boot.digest(image)
    session=native_boot.NativeSession(['--image',str(image),*firmware])
    try:
        output=session.read_until(r'moos-native login:\s*',100)
        for pattern in [r'MOOS Native disk boot \(GRUB\)',r'MOOS Native state: DATA READY',r'MOOS Native 0.1.0-dev',r'Release root: locked']:
            qemu_smoke.require(pattern,output,'installed release boot')
        session.send('root\n');session.read_until(r'Password:\s*',10)
        session.send('\n');denial=session.read_until(r'moos-native login:\s*',15)
        assert not re.search(r'\n#\s*$',denial,re.MULTILINE)
        session.send('\x01x');assert session.wait_for_exit(10)==0
    finally:
        session.close()
    assert native_boot.digest(image)==before


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--boot',choices=['bios','uefi'],default='bios')
    parser.add_argument('--uefi-code',type=Path)
    parser.add_argument('--uefi-vars',type=Path)
    parser.add_argument('--image',type=Path,default=ROOT/'output/native-installer/images/moos-native-installer-x86_64.img')
    parser.add_argument('--qemu',type=Path)
    args=parser.parse_args()
    firmware=['--boot',args.boot]
    if args.qemu: firmware += ['--qemu',str(args.qemu)]
    if args.boot=='uefi':
        if not args.uefi_code or not args.uefi_vars: parser.error('UEFI firmware required')
        firmware += ['--uefi-code',str(args.uefi_code),'--uefi-vars',str(args.uefi_vars)]
    backing={path:native_boot.digest(path) for path in [args.image,args.uefi_code,args.uefi_vars] if path}
    try:
        with tempfile.TemporaryDirectory(prefix='moos-native-installer-',dir='/tmp') as name:
            workspace=Path(name)
            for disk in ['a','b']:
                path=workspace/(disk+'.img')
                with path.open('wb') as stream: stream.truncate(512*native_disk.MIB)
                path.chmod(0o600)
                (workspace/(disk+'.serial')).write_text('MOOS-N3-TARGET-'+uuid.uuid4().hex[:5]+'\n')
            a,b=workspace/'a.img',workspace/'b.img'
            with b.open('r+b') as stream: stream.write(b'unrelated disk: do not change\n')
            b_before=native_boot.digest(b)
            serial=(workspace/'a.serial').read_text().strip()
            command=['--workspace',str(workspace),'--image',str(args.image),*firmware]
            session=InstallerSession(command)
            try:
                boot=session.read_until(r'installer> ',120)
                qemu_smoke.require(r'MOOS Native Installer disk boot \(GRUB\)',boot,'installer GRUB')
                assert 'login:' not in boot
                inspection=session.command('inspect')
                disks=json.loads(re.search(r'^DISKS=(.*)$',inspection,re.MULTILINE).group(1))
                assert len(disks)==3
                assert sum(d['source'] for d in disks)==1
                assert all(not d['eligible'] and d['read_only'] for d in disks if d['source'])
                assert len([d for d in disks if d['eligible']])==2
                source=next(d for d in disks if d['source'])
                assert 'FAILED' in session.command('plan '+source['serial']+' fresh')
                assert 'FAILED' in session.command('plan '+serial+' preserve')
                output=session.command('plan '+serial+' fresh')
                plan=json.loads(re.search(r'^PLAN=(.*)$',output,re.MULTILINE).group(1))
                assert plan['target']['serial']==serial and plan['mode']=='Fresh'
                assert (workspace/'b.serial').read_text().strip() not in json.dumps(plan)
                assert 'ALL DATA ON THIS TARGET WILL BE ERASED' in output
                token=re.search(r'Type exactly: confirm (ERASE [0-9A-F]+)',output).group(1)
                assert 'FAILED' in session.command('confirm ERASE 0000000000000000')
                # Changing observed target layout invalidates the immutable plan.
                output=session.command('plan '+serial+' fresh')
                token=re.search(r'Type exactly: confirm (ERASE [0-9A-F]+)',output).group(1)
                with a.open('r+b') as stream: stream.write(b'changed since plan')
                assert 'FAILED' in session.command('confirm '+token)
                with a.open('rb') as stream: assert b'EFI PART' not in stream.read(4096)
                output=session.command('plan '+serial+' fresh')
                token=re.search(r'Type exactly: confirm (ERASE [0-9A-F]+)',output).group(1)
                installed=session.command('confirm '+token)
                assert 'INSTALL_STATE=COMPLETE' in installed, installed
                assert 'INSTALL_STATE=FAILED' not in installed
                session.send('poweroff\n');session.read_until('reboot: Power down',20)
                assert session.wait_for_exit(10)==0
            finally:
                (workspace/'install-console.log').write_text(session.output)
                session.close()
            assert native_boot.digest(b)==b_before
            assert all(native_boot.digest(p)==d for p,d in backing.items())
            boot_installed(a,firmware)
            parts=native_disk.read_gpt(a,installed=True)
            assert parts[4].size==373*native_disk.MIB
            # Existing offline bounded probe/marker mechanism on private copies.
            with DisposableDisk(a,ROOT/'output/native/host/sbin/debugfs',installed=True) as probe:
                probe.install_probe()
                options=dict(boot=args.boot,code=args.uefi_code,variables=args.uefi_vars)
                identity=native_persistence.ready(probe.boot(**options))
                marker=probe.workspace/'marker'
                marker.write_text('native-n3-preserve-marker\n')
                probe.edit_fs('DATA',[f'write {marker} /config/persistence-marker','set_inode_field /config/persistence-marker mode 0100600'])
                data=probe.extract('DATA')
                # Only DATA of our installed target is refreshed with test marker.
                with a.open('r+b') as stream,data.open('rb') as source:
                    stream.seek(parts[4].offset);shutil.copyfileobj(source,stream)
            data_before=range_digest(a,parts[4])
            session=InstallerSession(command)
            try:
                session.read_until('installer> ',120)
                output=session.command('plan '+serial+' preserve')
                plan=json.loads(re.search(r'^PLAN=(.*)$',output,re.MULTILINE).group(1))
                assert plan['installation_id']==identity and plan['mode']=='PreserveData'
                token=re.search(r'Type exactly: confirm (PRESERVE [0-9A-F]+)',output).group(1)
                installed=session.command('confirm '+token)
                assert 'INSTALL_STATE=COMPLETE' in installed,installed
                session.send('poweroff\n');session.read_until('reboot: Power down',20);assert session.wait_for_exit(10)==0
            finally:
                (workspace/'preserve-console.log').write_text(session.output);session.close()
            assert range_digest(a,parts[4])==data_before
            assert native_disk.read_gpt(a,installed=True)==parts
            assert native_boot.digest(b)==b_before
            boot_installed(a,firmware)
            with DisposableDisk(a,ROOT/'output/native/host/sbin/debugfs',installed=True) as probe:
                probe.install_probe();output=probe.boot(**options)
                assert native_persistence.ready(output)==identity
                qemu_smoke.require('MOOS_N2_TEST_MARKER=native-n3-preserve-marker',output,'preserved marker')
            # Retain acceptance logs/images as generated artifacts only.
            result=ROOT/'output/native-installer/acceptance'/args.boot
            ensure_directory(result)
            safe_copy_file(a,result/'a.img',max_bytes=512*native_disk.MIB,
                           expected_size=512*native_disk.MIB)
            for path in [workspace/'install-console.log',workspace/'preserve-console.log']:
                safe_copy_file(path,result/path.name,max_bytes=4*1024*1024)
            with a.open('rb') as stream:
                stream.seek(512+56);disk_guid=str(uuid.UUID(bytes_le=stream.read(16)))
            safe_write_text(result/'identities.json',json.dumps(dict(disk_guid=disk_guid,installation_id=identity,partitions=[str(p.identity) for p in parts]),indent=2)+'\n',max_bytes=8192)
        assert all(native_boot.digest(p)==d for p,d in backing.items())
        print(f'N3 {args.boot}: PASS installer -> fresh installed boot; source/unrelated unchanged; stale plan rejected; DATA reinstall preserved')
        return 0
    except Exception as error:
        if 'session' in locals(): print(session.output[-6000:])
        print('N3 installer: FAIL:',repr(error));return 1


def range_digest(image,partition):
    import hashlib
    with image.open('rb') as stream:
        stream.seek(partition.offset)
        digest=hashlib.sha256();remaining=partition.size
        while remaining:
            data=stream.read(min(remaining,native_disk.MIB));assert data
            digest.update(data);remaining-=len(data)
        return digest.hexdigest()


if __name__=='__main__':
    raise SystemExit(main())
