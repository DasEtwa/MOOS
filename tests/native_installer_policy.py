#!/usr/bin/env python3
"""Executable installer Host input guards and source/build configuration policy."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

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


if __name__=='__main__':unittest.main()
