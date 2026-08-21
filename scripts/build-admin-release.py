#!/usr/bin/env python3
"""Build a deterministic unsigned MOOS administrator release bundle.

Signing is intentionally outside this checkout tool. A trusted release system
must inspect and sign the resulting bytes without exposing its private key to
repository code.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import re
import stat
import sys
import tarfile
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "host"))
from moos_admin_installer import MAX_MEMBER_BYTES, RELEASE_MEMBERS  # noqa: E402


def read_stable(path: Path) -> bytes:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError(f"source must be a regular, singly-linked file: {path}")
        if before.st_size > MAX_MEMBER_BYTES:
            raise ValueError(f"source exceeds its size limit: {path}")
        chunks = []
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        identity = lambda value: (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
        )
        data = b"".join(chunks)
        if identity(before) != identity(after) or len(data) != before.st_size:
            raise ValueError(f"source changed while being bundled: {path}")
        return data
    finally:
        os.close(descriptor)


def validate_control_manifest(files: dict[str, bytes]) -> None:
    manifest_name = "configs/control-plane-manifest.sha256"
    manifest_data = files[manifest_name]
    manifest_digest = hashlib.sha256(manifest_data).hexdigest()
    installer = files["scripts/setup-control-plane.sh"].decode("utf-8")
    if f"MANIFEST_SHA256='{manifest_digest}'" not in installer:
        raise ValueError("control-plane installer does not trust the bundled manifest")
    entries = {}
    for line in manifest_data.decode("ascii").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_./-]+)", line)
        if match is None or match.group(2) in entries:
            raise ValueError("invalid control-plane manifest")
        entries[match.group(2)] = match.group(1)
    for relative, expected in entries.items():
        if relative not in files:
            raise ValueError(f"manifest source is absent from release: {relative}")
        if hashlib.sha256(files[relative]).hexdigest() != expected:
            raise ValueError(f"manifest source digest mismatch: {relative}")


def build_bundle(source_root: Path, output: Path) -> str:
    files = {
        relative: read_stable(source_root / relative) for relative in RELEASE_MEMBERS
    }
    for binary in ("target/release/moos-gateway", "target/release/moos-gateway-device"):
        if files[binary][:4] != b"\x7fELF":
            raise ValueError(f"Gateway release artifact is not ELF: {binary}")
    validate_control_manifest(files)

    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", dir=output.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with tarfile.open(temporary, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for relative in sorted(RELEASE_MEMBERS):
                data = files[relative]
                member = tarfile.TarInfo(relative)
                member.size = len(data)
                member.mode = RELEASE_MEMBERS[relative]
                member.uid = 0
                member.gid = 0
                member.uname = "root"
                member.gname = "root"
                member.mtime = 0
                archive.addfile(member, io.BytesIO(data))
        os.chmod(temporary, 0o644)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return hashlib.sha256(output.read_bytes()).hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    try:
        digest = build_bundle(arguments.source_root.resolve(), arguments.output.resolve())
    except (OSError, UnicodeError, ValueError, tarfile.TarError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"administrator release: {arguments.output}")
    print(f"sha256: {digest}")
    print("signature: create only in the trusted release-signing environment")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
