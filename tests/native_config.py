#!/usr/bin/env python3
"""Native profile and executable launcher boundary regressions (no VM needed)."""
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

import native_boot

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/run-native-qemu.sh"


class NativePolicy(unittest.TestCase):
    def test_release_only_build(self):
        result = subprocess.run(["sh", str(ROOT / "scripts/build.sh"), "--target", "native", "--profile", "development"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("locked release profile", result.stderr)
        config = (ROOT / "configs/moos_native_x86_64_defconfig").read_text()
        self.assertIn("# BR2_TARGET_ENABLE_ROOT_LOGIN is not set", config)
        self.assertNotIn('BR2_TARGET_GENERIC_ROOT_PASSWD=', config)
        self.assertIn('BR2_REPRODUCIBLE=y', config)
        self.assertIn('BR2_DOWNLOAD_FORCE_CHECK_HASHES=y', config)
        self.assertIn('BR2_TARGET_GRUB2_I386_PC=y', config)
        self.assertIn('BR2_TARGET_GRUB2_X86_64_EFI=y', config)
        self.assertNotIn('BR2_PACKAGE_PYTHON', config)

    def test_launcher_arguments_and_rejections(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bin").mkdir()
            (root / "lib").mkdir()
            (root / "share/qemu").mkdir(parents=True)
            qemu = root / "bin/qemu-system-x86_64"
            qemu.write_text("#!/bin/sh\nexit 99\n")
            qemu.chmod(0o755)
            image = root / "disk with spaces.img"
            image.write_bytes(b"test")
            code, variables = root / "code.fd", root / "vars.fd"
            code.touch(); variables.touch()
            bwrap = root / "bin/bwrap"
            bwrap.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$NATIVE_CAPTURE"\n')
            bwrap.chmod(0o755)
            capture = root / "capture"
            environment = dict(os.environ, PATH=str(root / "bin") + ":" + os.environ["PATH"], NATIVE_CAPTURE=str(capture))
            base = ["sh", str(RUNNER), "--qemu", str(qemu), "--image", str(image)]
            for boot in ["bios", "uefi"]:
                options = ["--boot", boot]
                if boot == "uefi":
                    options += ["--uefi-code", str(code), "--uefi-vars", str(variables)]
                subprocess.run(base + options, env=environment, check=True)
                arguments = capture.read_text().splitlines()
                for denied in ["-kernel", "-append", "--share-net", "--bind", "-enable-kvm"]:
                    self.assertNotIn(denied, arguments)
                self.assertIn("--unshare-all", arguments)
                self.assertIn("stdio,id=native-console,mux=on,signal=off", arguments)
                self.assertIn("chardev:native-console", arguments)
                self.assertEqual(arguments[arguments.index("-monitor") + 1], "none")
                self.assertIn("file=/moos/native.img,if=virtio,snapshot=on,format=raw", arguments)
                self.assertIn(str(image), arguments)
                self.assertNotIn(str(ROOT), arguments)
                if boot == "uefi":
                    self.assertIn("if=pflash,format=raw,snapshot=on,file=/moos/vars.fd", arguments)
            subprocess.run(base + ["--network", "user"], env=environment, check=True)
            arguments = capture.read_text()
            self.assertIn("--share-net", arguments)
            self.assertNotIn("hostfwd", arguments)
            for options in [["--direct"], ["-kernel", "bad"], ["--network", "bridge"], ["--boot", "uefi"], ["--boot", "invalid"]]:
                self.assertEqual(subprocess.run(base + options, env=environment, capture_output=True).returncode, 2)
            fake_id = root / "bin/id"
            fake_id.write_text("#!/bin/sh\nprintf '0\\n'\n")
            fake_id.chmod(0o755)
            self.assertEqual(subprocess.run(base, env=environment, capture_output=True).returncode, 2)
            build = subprocess.run(["sh", str(ROOT / "scripts/build.sh"), "--target", "native"], env=environment, capture_output=True, text=True)
            self.assertEqual(build.returncode, 2)
            self.assertIn("unprivileged user", build.stderr)

    def test_non_bootable_and_truncated_disks_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / "bad.img"
            image.write_bytes(bytes(512))
            with self.assertRaisesRegex(ValueError, "boot signature"):
                native_boot.inspect_disk(image, Path("missing-debugfs"))
            mbr = bytearray(512)
            mbr[510:] = b"\x55\xaa"
            struct.pack_into("<I", mbr, 440, 0x4D4F4F53)
            mbr[450] = 0xEF
            mbr[466] = 0x83
            struct.pack_into("<II", mbr, 470, 34816, 122880)
            image.write_bytes(mbr)
            with self.assertRaisesRegex(ValueError, "truncated"):
                native_boot.inspect_disk(image, Path("missing-debugfs"))


if __name__ == "__main__":
    unittest.main()
