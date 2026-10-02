#!/usr/bin/env python3
"""Experimental installer VM: only private disposable regular target files."""
import argparse
import os
from pathlib import Path
import re
import stat

ROOT = Path(__file__).resolve().parents[1]


def regular(path, writable=False):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('single-link regular file required, never symlinks/devices')
    if writable and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600):
        raise ValueError('private test-owned target required')
    return info


def targets(workspace, source):
    if workspace.parent != Path('/tmp') or not workspace.name.startswith('moos-native-installer-') or workspace.resolve() != workspace:
        raise ValueError('only private /tmp installer test workspaces allowed')
    info = workspace.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('unsafe test workspace')
    source_info = regular(source)
    result, objects, serials = [], {(source_info.st_dev, source_info.st_ino)}, set()
    for name in ['a','b']:
        path = workspace / (name + '.img')
        info = regular(path, True)
        key = (info.st_dev, info.st_ino)
        if key in objects:
            raise ValueError('source or duplicate target inode')
        objects.add(key)
        serial_path = workspace / (name + '.serial')
        if regular(serial_path).st_size != 21:
            raise ValueError('bounded target serial required')
        serial = serial_path.read_text().strip()
        if not re.fullmatch('MOOS-N3-TARGET-[0-9a-f]{5}', serial) or serial in serials:
            raise ValueError('invalid/ambiguous virtual target serial')
        serials.add(serial)
        result.append((path,serial))
    return result


def canonical_inodes():
    result=set()
    for name in ['output/native/images/moos-native-x86_64.img','output/native-installer/images/moos-native-installer-x86_64.img']:
        path=ROOT/name
        if path.exists():
            info=regular(path);result.add((info.st_dev,info.st_ino))
    return result


def pin_bindings(argv,canonical=None):
    """Pin validated file inodes through sandbox setup; never reopen target paths."""
    argv=list(argv);fds=[];seen={};canonical=canonical_inodes() if canonical is None else canonical
    try:
        for index,argument in enumerate(argv):
            if argument in ('--bind','--ro-bind') and argv[index+2].startswith('/moos/'):
                path=Path(argv[index+1]);before=regular(path,argument=='--bind')
                # O_PATH inspects without opening a device, and NOFOLLOW rejects links.
                fd=os.open(path,os.O_PATH|os.O_NOFOLLOW);fds.append(fd)
                after=os.fstat(fd)
                if not stat.S_ISREG(after.st_mode) or after.st_nlink!=1 or (before.st_dev,before.st_ino)!=(after.st_dev,after.st_ino):
                    raise ValueError('backing changed before sandbox bind')
                key=(after.st_dev,after.st_ino);writable=argument=='--bind'
                if writable and (key in canonical or after.st_uid!=os.getuid() or stat.S_IMODE(after.st_mode)!=0o600):
                    raise ValueError('canonical or unsafe writable backing')
                if key in seen and (writable or seen[key]):
                    raise ValueError('source/target or duplicate opened inode')
                seen[key]=writable
                os.set_inheritable(fd,True)
                argv[index]=argument+'-fd';argv[index+1]=str(fd)
        return argv,fds
    except BaseException:
        for fd in fds:os.close(fd)
        raise


def command(args):
    if os.getuid() == 0:
        raise ValueError('installer QEMU requires an unprivileged builder')
    disks = targets(args.workspace,args.image)
    args.canonical_inodes=canonical_inodes()
    qemu = args.qemu.resolve()
    library, firmware = qemu.parent.parent / 'lib', qemu.parent.parent / 'share/qemu'
    if not qemu.is_file() or not library.is_dir() or not firmware.is_dir():
        raise ValueError('QEMU build/runtime missing')
    argv = ['bwrap','--unshare-all','--die-with-parent','--new-session','--clearenv','--uid','65534','--gid','65534',
            '--dev','/dev','--proc','/proc','--size','268435456','--tmpfs','/tmp','--chmod','1777','/tmp',
            '--dir','/var','--size','268435456','--tmpfs','/var/tmp','--chmod','1777','/var/tmp',
            '--dir','/etc','--dir','/lib','--dir','/lib64','--dir','/usr','--dir','/usr/lib',
            '--dir','/opt','--dir','/opt/qemu','--dir','/moos',
            '--ro-bind',str(qemu),'/opt/qemu/qemu','--ro-bind',str(library),'/opt/qemu/lib',
            '--ro-bind',str(firmware),'/opt/qemu/firmware','--ro-bind','/lib','/lib',
            '--ro-bind','/lib64','/lib64','--ro-bind','/usr/lib','/usr/lib','--ro-bind-try','/usr/lib64','/usr/lib64',
            '--ro-bind',str(args.image),'/moos/installer.img']
    for name,(path,_) in zip(['a','b'],disks):
        argv += ['--bind',str(path),f'/moos/{name}.img']
    if args.boot == 'uefi':
        for name,path in [('code',args.uefi_code),('vars',args.uefi_vars)]:
            if path is None:
                raise ValueError('UEFI needs explicit firmware templates')
            regular(path)
            argv += ['--ro-bind',str(path),f'/moos/{name}.fd']
    elif args.uefi_code or args.uefi_vars:
        raise ValueError('firmware options require UEFI')
    argv += ['--setenv','PATH','/usr/bin:/bin','--setenv','HOME','/nonexistent','--setenv','TMPDIR','/tmp',
             '--setenv','LC_ALL','C','--setenv','LD_LIBRARY_PATH','/opt/qemu/lib',
             '--setenv','QEMU_AUDIO_DRV','none','/opt/qemu/qemu',
             '-M','pc','-accel','tcg','-m','512M','-smp','1','-nodefaults','-no-reboot',
             '-drive','file=/moos/installer.img,if=none,id=source,format=raw,readonly=on',
             '-device','virtio-blk-pci,drive=source,serial=MOOS-N3-INSTALLER,bootindex=1',
             '-nographic','-chardev','stdio,id=installer-console,mux=on,signal=off',
             '-serial','chardev:installer-console','-monitor','none','-L','/opt/qemu/firmware']
    for name,(_,serial) in zip(['a','b'],disks):
        argv += ['-drive',f'file=/moos/{name}.img,if=none,id={name},format=raw,snapshot=off',
                 '-device',f'virtio-blk-pci,drive={name},serial={serial}']
    if args.boot == 'uefi':
        argv += ['-drive','if=pflash,format=raw,readonly=on,file=/moos/code.fd',
                 '-drive','if=pflash,format=raw,snapshot=on,file=/moos/vars.fd']
    return argv


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--image',type=Path,default=ROOT/'output/native-installer/images/moos-native-installer-x86_64.img')
    parser.add_argument('--qemu',type=Path,default=ROOT/'output/host/bin/qemu-system-x86_64')
    parser.add_argument('--boot',choices=['bios','uefi'],default='bios')
    parser.add_argument('--uefi-code',type=Path)
    parser.add_argument('--uefi-vars',type=Path)
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    try:
        argv=command(args)
        if args.dry_run:
            print('QEMU installer: rootless; offline; source readonly; two private regular target copies; no host devices')
        else:
            argv,fds=pin_bindings(argv,args.canonical_inodes)
            os.execvp(argv[0],argv)
    except (ValueError,OSError) as error:
        parser.exit(2,'error: '+str(error)+'\n')


if __name__ == '__main__':
    main()
