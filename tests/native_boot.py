#!/usr/bin/env python3
"""Acceptance of the actual Native disk, including its locked release account."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import qemu_smoke
import native_disk
from native_safe_output import safe_write_text

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_validator", ROOT / "scripts/validate-release-rootfs.py")
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def inspect_disk(image, debugfs):
    """Inspect SYSTEM_A from the disk being booted, not an adjacent rootfs."""
    partitions = native_disk.read_gpt(image)
    with tempfile.TemporaryDirectory(prefix="moos-native-check-") as temporary:
        system = Path(temporary) / "system.ext4"
        native_disk.extract_partition(image, partitions[2], system)
        validator.validate_shadow(validator.read_image_shadow(debugfs, system))
        if native_disk.fs_read(debugfs, system, '/etc/moos-platform') != b"MOOS_BACKEND='native'\nMOOS_ARCHITECTURE='x86_64'\n":
            raise ValueError("disk does not contain the Native platform identity")
        data = Path(temporary) / "data.ext4"
        native_disk.extract_partition(image, partitions[4], data)
        if native_disk.fs_read(debugfs, data, '/state-version') != b'1\n':
            raise ValueError('Native distribution DATA schema is invalid')
        if native_disk.fs_read(debugfs, data, '/identity/installation-id', missing_ok=True) is not None:
            raise ValueError('Native distribution image contains an installation identity')
    with image.open('rb') as stream:
        stream.seek(partitions[3].offset)
        for _ in range(partitions[3].size // native_disk.MIB):
            if any(stream.read(native_disk.MIB)):
                raise ValueError('SYSTEM_B is not the reserved empty slot')
    return partitions


class NativeSession(qemu_smoke.QemuSession):
    def __init__(self, arguments):
        self.output = ""
        self.exit_status = None
        self.pid, self.fd = qemu_smoke.pty.fork()
        if self.pid == 0:
            runner = str(ROOT / "scripts/run-native-qemu.sh")
            os.execv(runner, [runner, *arguments])

    def _read_available(self):
        super()._read_available()
        if len(self.output) > 1024 * 1024:
            raise RuntimeError("Native boot console exceeded 1 MiB")

    def wait_for_exit(self, timeout):
        try:
            return super().wait_for_exit(timeout)
        except TimeoutError as error:
            raise TimeoutError("QEMU did not exit after console quit") from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, default=ROOT / "output/native/images/moos-native-x86_64.img")
    parser.add_argument("--debugfs", type=Path, default=ROOT / "output/native/host/sbin/debugfs")
    parser.add_argument("--boot", choices=["bios", "uefi"], default="bios")
    parser.add_argument("--network", choices=["none", "user"], default="none")
    parser.add_argument("--qemu", type=Path)
    parser.add_argument("--uefi-code", type=Path)
    parser.add_argument("--uefi-vars", type=Path)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--timeout", type=int, default=90, help="boot deadline in seconds (default: 90)")
    args = parser.parse_args()
    if not 1 <= args.timeout <= 300:
        parser.error("--timeout must be between 1 and 300 seconds")
    session = None
    try:
        inspect_disk(args.image, args.debugfs)
        before = digest(args.image)
        firmware_before = {path: digest(path) for path in [args.uefi_code, args.uefi_vars] if path}
        arguments = ["--image", str(args.image), "--boot", args.boot, "--network", args.network]
        for name in ["qemu", "uefi_code", "uefi_vars"]:
            if getattr(args, name):
                arguments.extend(["--" + name.replace("_", "-"), str(getattr(args, name))])
        session = NativeSession(arguments)
        boot = session.read_until(r"moos-native login:\s*", args.timeout)
        version = re.search(r"MOOS_VERSION='([^']+)'", (ROOT / "system/overlay/etc/moos-release").read_text()).group(1)
        for pattern, description in [
            (r"MOOS Native disk boot \(GRUB\)", "disk bootloader marker"),
            (r"Linux version 6\.18\.43", "pinned kernel boot"),
            (r"VFS: Mounted root .* filesystem", "mounted system filesystem"),
            (re.escape("MOOS Native " + version), "MOOS version"),
            (r"Backend: native / Architecture: x86_64 / Release root: locked", "Native release identity"),
            (r"MOOS Native state: DATA READY at /var/lib/moos; schema 1; identity READY; SYSTEM_B RESERVED", "persistent state initialization"),
        ]:
            qemu_smoke.require(pattern, boot, description)
        if args.network == "user":
            qemu_smoke.require(r"udhcpc: lease of [0-9.]+ obtained", boot, "DHCP lease")
        session.send("root\n")
        session.read_until(r"Password:\s*", 10)
        session.send("\n")
        denial = session.read_until(r"moos-native login:\s*", 15)
        if re.search(r"\n#\s*$", denial, re.MULTILINE):
            raise AssertionError("Native release opened a root shell")
        session.send("\x01x")
        if session.wait_for_exit(5) != 0:
            raise AssertionError("QEMU did not terminate cleanly")
        if digest(args.image) != before:
            raise AssertionError("Native acceptance modified its backing disk")
        if any(digest(path) != checksum for path, checksum in firmware_before.items()):
            raise AssertionError("Native acceptance modified its firmware templates")
        print(f"MOOS Native {args.boot} disk boot: PASS; identity, locked root, clean stop, unchanged disk")
        return 0
    except (AssertionError, OSError, ValueError, RuntimeError, TimeoutError, subprocess.SubprocessError) as error:
        print("MOOS Native disk boot: FAIL: " + str(error), file=sys.stderr)
        if session:
            print(session.output[-4000:], file=sys.stderr)
        return 1
    finally:
        if session:
            session.close()
            if args.log:
                safe_write_text(args.log,session.output)


if __name__ == "__main__":
    raise SystemExit(main())
