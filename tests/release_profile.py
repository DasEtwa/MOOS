#!/usr/bin/env python3
"""Validate separation of insecure development and locked release guests."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEVELOPMENT = REPO_ROOT / "configs/moos_qemu_x86_64_defconfig"
RELEASE = REPO_ROOT / "configs/moos_qemu_x86_64_release_defconfig"
VALIDATOR = REPO_ROOT / "scripts/validate-release-rootfs.py"
BUILD_SCRIPT = REPO_ROOT / "scripts/build.sh"
KERNEL_VERSION = "6.18.43"
KERNEL_SHA256 = "a1aeb926c7c4a1564368200b1082e45bb958007682d804aff88abbc3a7a47b5d"


def validate_fixture(root: Path, password_field: str, expected: int) -> None:
    shadow = root / "shadow"
    shadow.write_text(f"root:{password_field}:20000:0:99999:7:::\n", encoding="utf-8")
    completed = subprocess.run(
        [str(VALIDATOR), "--shadow-file", str(shadow)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != expected:
        raise AssertionError(completed.stdout + completed.stderr)


def main() -> int:
    development = DEVELOPMENT.read_text(encoding="utf-8")
    release = RELEASE.read_text(encoding="utf-8")
    build_script = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert "JOBS=4\nPROFILE='release'\n" in build_script
    assert "BUILDROOT_RELEASE='2026.05.1'" in build_script
    assert "BUILDROOT_REF='cb857ba4c87a93e5265a9e4a3f32071abf39e14a'" in build_script
    assert 'BUILDROOT_PATCH_DIR="$MOOS_ROOT/patches/buildroot"' in build_script
    qemu_patch = (
        REPO_ROOT / "patches/buildroot/0001-moo-qemu-maintenance-and-host-seccomp.patch"
    ).read_text(encoding="utf-8")
    assert "QEMU_VERSION = 11.0.3" in qemu_patch
    assert "HOST_QEMU_OPTS += --enable-seccomp" in qemu_patch
    assert 'BR2_TARGET_ENABLE_ROOT_LOGIN=y' in development
    assert 'BR2_TARGET_GENERIC_ROOT_PASSWD=""' in development
    assert '# BR2_TARGET_ENABLE_ROOT_LOGIN is not set' in release
    assert 'BR2_TARGET_GENERIC_ROOT_PASSWD=""' not in release
    assert f'BR2_LINUX_KERNEL_CUSTOM_VERSION_VALUE="{KERNEL_VERSION}"' in development
    assert f'BR2_LINUX_KERNEL_CUSTOM_VERSION_VALUE="{KERNEL_VERSION}"' in release
    for hash_path in (
        REPO_ROOT / "patches/linux/linux.hash",
        REPO_ROOT / "patches/linux-headers/linux-headers.hash",
    ):
        kernel_hash = hash_path.read_text(encoding="utf-8")
        assert KERNEL_SHA256 in kernel_hash
        assert f"linux-{KERNEL_VERSION}.tar.xz" in kernel_hash

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        validate_fixture(root, "", 2)
        validate_fixture(root, "$6$committed-password-hash", 2)
        validate_fixture(root, "*", 0)
        validate_fixture(root, "!", 0)

    print("MOOS release profile test: PASS")
    print("  development blank login is explicit opt-in and isolated: ok")
    print("  release root login is disabled and final-image validation is strict: ok")
    print("  maintained Linux 6.18.43 pin and source hash: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
