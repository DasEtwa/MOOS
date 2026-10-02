#!/usr/bin/env python3
"""Real QEMU regression: a disk with destroyed BIOS boot code must fail acceptance."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import native_boot
from native_test_disk import DisposableDisk


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, default=native_boot.ROOT / "output/native/images/moos-native-x86_64.img")
    parser.add_argument("--qemu", type=Path)
    parser.add_argument("--debugfs", type=Path, default=native_boot.ROOT / "output/native/host/sbin/debugfs")
    args = parser.parse_args()
    try:
        with tempfile.TemporaryDirectory(prefix="moos-unbootable-") as temporary:
            image = Path(temporary) / "unbootable.img"
            shutil.copyfile(args.image, image)
            # Keep the signature, partition table, platform and locked SYSTEM_A
            # intact. Only boot code is destroyed; metadata inspection still passes.
            with image.open("r+b") as stream:
                # cli; hlt; jmp hlt: deliberately stop instead of executing data.
                stream.write(b"\xfa\xf4\xeb\xfd" + bytes(436))
            native_boot.inspect_disk(image, args.debugfs)
            before = native_boot.digest(image)
            command = [sys.executable, str(native_boot.ROOT / "tests/native_boot.py"),
                       "--image", str(image), "--debugfs", str(args.debugfs),
                       "--boot", "bios", "--timeout", "15"]
            if args.qemu:
                command += ["--qemu", str(args.qemu)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=40)
            if result.returncode != 1 or "timed out waiting for moos-native login" not in result.stderr:
                raise AssertionError("destroyed bootloader did not fail at disk boot:\n" + result.stdout + result.stderr)
            if native_boot.digest(image) != before:
                raise AssertionError("negative boot changed its backing image")
        print("MOOS Native non-bootable disk rejection: PASS (real QEMU boot deadline)")
        # A valid GRUB disk must not fall back to reserved SYSTEM_B when the
        # active PARTUUID is absent. Mutation remains a private regular copy.
        with DisposableDisk(args.image, args.debugfs) as disk:
            disk.mutate_entries(lambda entries: entries.__setitem__(slice(2 * 128 + 16, 2 * 128 + 32), bytes.fromhex('01010101010101010101010101010101')))
            before = native_boot.digest(disk.image)
            options = ['--image', str(disk.image), '--boot', 'bios']
            if args.qemu:
                options += ['--qemu', str(args.qemu)]
            session = native_boot.NativeSession(options)
            try:
                try:
                    session.read_until(r'moos-native login:\s*', 15)
                except TimeoutError:
                    if 'MOOS Native disk boot (GRUB)' not in session.output:
                        raise AssertionError('missing SYSTEM_A test did not reach bootloader')
                else:
                    raise AssertionError('missing SYSTEM_A unexpectedly reached login')
            finally:
                session.close()
            if native_boot.digest(disk.image) != before:
                raise AssertionError('missing SYSTEM_A test modified backing disk')
        print('MOOS Native missing SYSTEM_A: PASS (actual disk boot fails; no reserved-slot fallback)')
        return 0
    except (OSError, ValueError, AssertionError, subprocess.SubprocessError) as error:
        print("MOOS Native negative boot: FAIL: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
