#!/usr/bin/env python3
"""Real installer failures on private regular copies, with unchanged DATA/source."""
import argparse
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid

import native_boot
import native_disk
from native_installer import InstallerSession
from native_test_disk import DisposableDisk

ROOT=native_boot.ROOT


def prepare(directory,source=None):
    directory=Path(directory)
    for name in ['a','b']:
        path=directory/(name+'.img')
        if name=='a' and source:shutil.copyfile(source,path)
        else:
            with path.open('wb') as stream:stream.truncate(512*native_disk.MIB)
        path.chmod(0o600)
        (directory/(name+'.serial')).write_text('MOOS-N3-TARGET-'+uuid.uuid4().hex[:5]+'\n')
    return directory/'a.img',(directory/'a.serial').read_text().strip()


def preserve_fault(source,fault,image):
    with DisposableDisk(source,ROOT/'output/native/host/sbin/debugfs',installed=True) as probe:
        if fault=='schema':
            value=probe.workspace/'unsupported';value.write_text('9\n')
            probe.edit_fs('DATA',['rm /state-version',f'write {value} /state-version','set_inode_field /state-version mode 0100600'])
        elif fault=='identity':
            value=probe.workspace/'invalid-id';value.write_text('not-an-installation-uuid\n')
            probe.edit_fs('DATA',['rm /identity/installation-id',f'write {value} /identity/installation-id','set_inode_field /identity/installation-id mode 0100600'])
        elif fault=='corrupt-data':
            with probe.image.open('r+b') as stream:stream.seek(probe.partitions['DATA'].offset+1024);stream.write(bytes(1024))
        elif fault=='duplicate-role':
            def duplicate(entries):entries[4*128+56:5*128]=entries[3*128+56:4*128]
            probe.mutate_entries(duplicate)
        elif fault=='damaged-gpt':
            with probe.image.open('r+b') as stream:stream.seek(probe.image.stat().st_size-512+16);stream.write(bytes(4))
        else:raise ValueError(fault)
        shutil.copyfile(probe.image,image)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=ROOT/'output/native-installer/acceptance/bios/a.img')
    parser.add_argument('--qemu',type=Path)
    args=parser.parse_args()
    media=ROOT/'output/native-installer/images/moos-native-installer-x86_64.img'
    before=native_boot.digest(media)
    logs=ROOT/'output/native-installer/acceptance/negative';logs.mkdir(parents=True,exist_ok=True)
    # A corrupted co-located payload must block startup, without touching targets.
    with tempfile.TemporaryDirectory(prefix='moos-native-installer-',dir='/tmp') as directory:
        workspace=Path(directory);a,serial=prepare(workspace)
        damaged=workspace/'corrupted-installer.img';shutil.copyfile(media,damaged)
        boot=workspace/'corrupt-boot.img';boot.write_bytes(bytes(512))
        parts=native_disk.read_gpt(damaged,media=True)
        subprocess.run([str(ROOT/'output/native-installer/host/bin/mcopy'),'-o','-i',str(damaged)+'@@'+str(parts[1].offset),str(boot),'::/payload/boot.img'],check=True,timeout=15)
        backing={p:native_boot.digest(p) for p in [damaged,a,workspace/'b.img']}
        runner_args=['--workspace',str(workspace),'--image',str(damaged)]
        if args.qemu:runner_args += ['--qemu',str(args.qemu)]
        session=InstallerSession(runner_args)
        try:
            output=session.read_until('payload integrity mismatch',120)
            assert 'INSTALL_STATE=BLOCKED' in output and 'INSTALL_STATE=COMPLETE' not in output
            session.send('\x01x');assert session.wait_for_exit(10)==0
        finally:(logs/'corrupt-payload.log').write_text(session.output);session.close()
        assert all(native_boot.digest(p)==d for p,d in backing.items())
    # Each rejected preserve plan leaves the complete target byte-identical.
    for fault in ['schema','identity','corrupt-data','duplicate-role','damaged-gpt']:
        with tempfile.TemporaryDirectory(prefix='moos-native-installer-',dir='/tmp') as directory:
            workspace=Path(directory);a,serial=prepare(workspace,args.source)
            preserve_fault(args.source,fault,a)
            backing={p:native_boot.digest(p) for p in [a,workspace/'b.img']}
            runner_args=['--workspace',str(workspace),'--image',str(media)]
            if args.qemu:runner_args += ['--qemu',str(args.qemu)]
            session=InstallerSession(runner_args)
            try:
                session.read_until('installer> ',120)
                output=session.command('plan '+serial+' preserve')
                assert 'INSTALL_STATE=FAILED' in output and 'PLAN=' not in output and 'COMPLETE' not in output,output
                session.send('poweroff\n');session.read_until('reboot: Power down',20);assert session.wait_for_exit(10)==0
            finally:(logs/(fault+'.log')).write_text(session.output);session.close()
            assert all(native_boot.digest(p)==d for p,d in backing.items())
        print('N3 preserve rejected without mutation:',fault,flush=True)
    # Interruption after destructive commit cannot report COMPLETE. No recovery claim.
    with tempfile.TemporaryDirectory(prefix='moos-native-installer-',dir='/tmp') as directory:
        workspace=Path(directory);a,serial=prepare(workspace)
        b_before=native_boot.digest(workspace/'b.img')
        runner_args=['--workspace',str(workspace),'--image',str(media)]
        if args.qemu:runner_args += ['--qemu',str(args.qemu)]
        session=InstallerSession(runner_args)
        try:
            session.read_until('installer> ',120)
            output=session.command('plan '+serial+' fresh')
            token=re.search(r'Type exactly: confirm (ERASE [0-9A-F]+)',output).group(1)
            session.send('confirm '+token+'\n')
            session.read_until('INSTALL_STATE=WRITING_SYSTEM',180)
            session.send('\x01x');assert session.wait_for_exit(10)==0
            assert 'INSTALL_STATE=COMPLETE' not in session.output
        finally:(logs/'interrupted.log').write_text(session.output);session.close()
        assert native_boot.digest(workspace/'b.img')==b_before
    assert native_boot.digest(media)==before
    print('N3 negative acceptance: PASS corrupt payload blocked; invalid preserve states unchanged; interrupted write never COMPLETE; source/unrelated intact')


if __name__=='__main__':main()
