#!/usr/bin/env python3
"""Executable installer Host input guards and source/build configuration policy."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

import native_safe_output

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('installer_runner',ROOT/'scripts/run-native-installer-qemu.py')
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)


class InstallerPolicy(unittest.TestCase):
    def test_writable_targets_reject_devices_links_source_and_canonical(self):
        with tempfile.TemporaryDirectory(prefix='moos-native-installer-',dir='/tmp') as directory:
            workspace=Path(directory)
            source=workspace/'source.img';source.write_bytes(b'readonly installer')
            for name in ['a','b']:
                p=workspace/(name+'.img')
                with p.open('wb') as stream:stream.truncate(512*1024*1024)
                p.chmod(0o600)
                (workspace/(name+'.serial')).write_text('MOOS-N3-TARGET-'+('abcde' if name=='a' else '12345')+'\n')
            self.assertEqual(len(runner.targets(workspace,source)),2)
            argv,fds=runner.pin_bindings(['bwrap','--ro-bind',str(source),'/moos/installer.img','--bind',str(workspace/'a.img'),'/moos/a.img'])
            try:
                self.assertIn('--bind-fd',argv);self.assertIn('--ro-bind-fd',argv)
                self.assertEqual(os.fstat(fds[1]).st_ino,(workspace/'a.img').stat().st_ino)
            finally:
                for fd in fds:os.close(fd)
            source.chmod(0o600)
            with self.assertRaises(ValueError):
                runner.pin_bindings(['bwrap','--ro-bind',str(source),'/moos/installer.img','--bind',str(source),'/moos/a.img'])
            info=(workspace/'a.img').stat()
            with self.assertRaises(ValueError):
                runner.pin_bindings(['bwrap','--bind',str(workspace/'a.img'),'/moos/a.img'],{(info.st_dev,info.st_ino)})
            a=workspace/'a.img'
            for target in [source,Path('/dev/null'),ROOT/'output/native/images/moos-native-x86_64.img']:
                a.unlink();a.symlink_to(target)
                with self.assertRaises((ValueError,OSError)):runner.targets(workspace,source)
            a.unlink();a.hardlink_to(source)
            with self.assertRaises(ValueError):runner.targets(workspace,source)
            a.unlink();a.write_bytes(b'target');a.chmod(0o600)
            with a.open('r+b') as stream:stream.truncate(512*1024*1024)
            second=workspace/'alias';second.hardlink_to(a)
            with self.assertRaises(ValueError):runner.targets(workspace,source)
            second.unlink()
            (workspace/'b.serial').write_text((workspace/'a.serial').read_text())
            with self.assertRaises(ValueError):runner.targets(workspace,source)
            workspace.chmod(0o755)
            with self.assertRaises(ValueError):runner.targets(workspace,source)
            workspace.chmod(0o700)
            with a.open('r+b') as stream:stream.truncate(1)
            with self.assertRaises(ValueError):runner.targets(workspace,source)
        with self.assertRaises(ValueError):runner.targets(ROOT,ROOT/'README.md')

    def test_build_and_console_boundaries(self):
        config=(ROOT/'configs/moos_native_installer_x86_64_defconfig').read_text()
        self.assertIn('# BR2_TARGET_ENABLE_ROOT_LOGIN is not set',config)
        self.assertIn('# BR2_TARGET_GENERIC_GETTY is not set',config)
        self.assertIn('BR2_TARGET_ROOTFS_CPIO_GZIP=y',config)
        self.assertIn('BR2_REPRODUCIBLE=y',config)
        self.assertNotIn('BR2_PACKAGE_PYTHON',config)
        init=(ROOT/'system/native-installer/overlay/etc/inittab').read_text()
        self.assertIn('ttyS0::respawn:/usr/bin/moos-native-installer',init)
        self.assertNotIn('getty',init);self.assertNotIn('askfirst',init)
        rust=(ROOT/'native/installer/src/lib.rs').read_text()
        self.assertNotIn('Command::new("/bin/sh")',rust)
        self.assertIn('disk_sequence',rust)
        self.assertIn('experimental QEMU-only gate',rust)
        self.assertIn('plan.mode == Mode::Fresh',rust)

    def test_acceptance_outputs_refuse_symlink_hardlink_and_directory_tricks(self):
        with tempfile.TemporaryDirectory(prefix='moos-safe-output-') as temporary:
            root=Path(temporary)
            repo=root/'repo'
            repo.mkdir(mode=0o700)
            output=repo/'output/native-installer/acceptance/bios'
            native_safe_output.ensure_directory(output)
            outside=root/'outside'
            outside.mkdir(mode=0o700)
            sentinel=outside/'sentinel.txt'
            sentinel.write_text('preserve this Host file\n')

            linked=output/'linked.log'
            linked.symlink_to(sentinel)
            with self.assertRaises((OSError,ValueError)):
                native_safe_output.safe_write_text(linked,'attacker data\n')
            self.assertTrue(linked.is_symlink())
            self.assertEqual(sentinel.read_text(),'preserve this Host file\n')

            linked_parent=repo/'output-link'
            linked_parent.symlink_to(outside,target_is_directory=True)
            with self.assertRaises((OSError,ValueError)):
                native_safe_output.safe_write_text(linked_parent/'redirected.log','attacker data\n')
            self.assertFalse((outside/'redirected.log').exists())

            linked_directory=repo/'acceptance-link'
            linked_directory.symlink_to(outside,target_is_directory=True)
            with self.assertRaises((OSError,ValueError)):
                native_safe_output.ensure_directory(linked_directory/'bios')
            self.assertFalse((outside/'bios').exists())

            hardlinked=output/'hardlinked.log'
            os.link(sentinel,hardlinked)
            with self.assertRaises(ValueError):
                native_safe_output.safe_write_text(hardlinked,'attacker data\n')
            self.assertEqual(sentinel.read_text(),'preserve this Host file\n')

            directory_at_file=output/'directory.log'
            directory_at_file.mkdir()
            with self.assertRaises((OSError,ValueError)):
                native_safe_output.safe_write_text(directory_at_file,'attacker data\n')

            replaceable=output/'replaceable.log'
            native_safe_output.safe_write_text(replaceable,'first\n')
            native_safe_output.safe_write_text(replaceable,'second\n')
            self.assertEqual(replaceable.read_text(),'second\n')
            self.assertEqual(replaceable.stat().st_nlink,1)
            self.assertEqual(replaceable.stat().st_mode & 0o777,0o600)

            source=repo/'source.img'
            source.write_bytes(b'disposable source bytes')
            copied=output/'copy.img'
            native_safe_output.safe_copy_file(
                source,copied,max_bytes=64,expected_size=23
            )
            self.assertEqual(copied.read_bytes(),b'disposable source bytes')
            self.assertEqual(
                native_safe_output.safe_read_text(replaceable,max_bytes=64),'second\n'
            )

    def test_acceptance_writers_use_the_safe_output_boundary(self):
        for name in [
            'native_boot.py',
            'native_installer.py',
            'native_installer_clone_negative.py',
            'native_installer_negative.py',
            'native_installer_multidisk.py',
        ]:
            source=(ROOT/'tests'/name).read_text()
            self.assertIn('native_safe_output',source,name)
        self.assertIn('safe_copy_file', (ROOT/'tests/native_installer.py').read_text())
        self.assertIn('safe_write_text', (ROOT/'tests/native_installer_negative.py').read_text())
        self.assertNotIn("(result/'identities.json').write_text",
                         (ROOT/'tests/native_installer.py').read_text())
        self.assertNotIn('write_text(session.output)',
                         (ROOT/'tests/native_installer_multidisk.py').read_text())


if __name__=='__main__':unittest.main()
