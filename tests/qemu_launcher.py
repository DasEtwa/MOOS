#!/usr/bin/env python3
"""Validate the safe QEMU launcher defaults without booting a guest."""

import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "scripts" / "run-qemu.sh"


def run(*arguments, expected=0):
    # Dry-run policy checks must work in a clean checkout without built images.
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        images = root / "images"
        images.mkdir()
        (images / "bzImage").touch()
        (images / "rootfs.ext2").touch()
        qemu = root / "qemu" / "bin" / "qemu-system-x86_64"
        qemu.parent.mkdir(parents=True)
        (root / "qemu" / "lib").mkdir()
        (root / "qemu" / "share" / "qemu").mkdir(parents=True)
        qemu.write_text("#!/bin/sh\nexit 99\n")
        qemu.chmod(0o700)
        result = subprocess.run(
            [str(RUNNER), "--qemu", str(qemu), "--image-dir", str(images), *arguments],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    if result.returncode != expected:
        raise AssertionError(
            f"launcher returned {result.returncode}, expected {expected}: "
            f"{result.stdout}{result.stderr}"
        )
    return result.stdout + result.stderr


def require(needle, output, description):
    if needle not in output:
        raise AssertionError(f"missing {description}: {needle!r}")


def main():
    default = run("--serial-only", "--dry-run")
    require("isolation: bubblewrap rootless sandbox", default, "rootless isolation")
    require("network: disabled", default, "default network denial")
    require("memory: 256M", default, "memory limit")
    require("cpus: 1", default, "CPU limit")
    require("shared folders: none", default, "shared-folder denial")
    require("host device passthrough: none", default, "host-device denial")
    require("host-guest channel: serial console only", default, "channel boundary")

    user_network = run("--serial-only", "--network", "user", "--dry-run")
    require("network: QEMU user-mode NAT", user_network, "explicit user network")

    direct = run("--direct", "--serial-only", "--dry-run")
    require("isolation: direct host process", direct, "explicit direct mode")

    managed_console = run(
        "--serial-only",
        "--console-socket",
        "/run/moos-instances/personal/console.sock",
        "--dry-run",
    )
    require(
        "serial endpoint: managed reconnectable Unix socket",
        managed_console,
        "managed serial endpoint",
    )

    run("--serial-only", "--network", "invalid", "--dry-run", expected=2)
    run("--serial-only", "--cpus", "0", "--dry-run", expected=2)
    run("--serial-only", "--cpus", "9", "--dry-run", expected=2)
    run("--serial-only", "--memory", "4097M", "--dry-run", expected=2)
    run(
        "--serial-only",
        "--console-socket",
        "/tmp/arbitrary.sock",
        "--dry-run",
        expected=2,
    )
    run("--dry-run", expected=2)

    print("MOOS QEMU launcher isolation test: PASS")
    print("  isolated default, explicit network, direct escape hatch: ok")
    print("  invalid limits and non-serial isolated launch: rejected")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, OSError) as error:
        print("MOOS QEMU launcher isolation test: FAIL", file=sys.stderr)
        print(f"  {error}", file=sys.stderr)
        sys.exit(1)
