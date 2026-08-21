#!/usr/bin/env python3
"""Fail unless a generated MOOS rootfs has a locked root password."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


MAX_SHADOW_BYTES = 1024 * 1024


def validate_shadow(content: bytes) -> None:
    if len(content) > MAX_SHADOW_BYTES:
        raise ValueError("shadow file exceeds 1 MiB")
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError("shadow file is not UTF-8") from error
    root_entries = [line.split(":", 2) for line in lines if line.startswith("root:")]
    if len(root_entries) != 1 or len(root_entries[0]) < 2:
        raise ValueError("shadow file must contain exactly one root entry")
    password_field = root_entries[0][1]
    if not password_field.startswith(("!", "*")):
        raise ValueError("release root account is not locked")


def read_image_shadow(debugfs: Path, image: Path) -> bytes:
    if not debugfs.is_file() or not image.is_file():
        raise ValueError("release rootfs validator input is missing")
    completed = subprocess.run(
        [str(debugfs), "-R", "cat /etc/shadow", str(image)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError("could not read /etc/shadow from the release rootfs")
    return completed.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--shadow-file", type=Path)
    source.add_argument("--rootfs-image", type=Path)
    parser.add_argument("--debugfs", type=Path)
    args = parser.parse_args()
    try:
        if args.shadow_file is not None:
            content = args.shadow_file.read_bytes()
        else:
            if args.debugfs is None:
                parser.error("--debugfs is required with --rootfs-image")
            content = read_image_shadow(args.debugfs, args.rootfs_image)
        validate_shadow(content)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print("MOOS release root credential check: PASS (root account locked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
