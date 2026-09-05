#!/usr/bin/python3 -I
"""Install an authenticated MOOS administrator release.

This program is a trust anchor. Production use is supported only from the
root-owned path below with the root-owned public key below. Developer checkout
copies are source material for packaging and tests, never a sudo entry point.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


TRUSTED_PROGRAM = Path("/usr/libexec/moos/moos-admin-installer")
TRUSTED_KEY = Path("/etc/moos/trust/admin-release.pem")
RELEASE_ROOT = Path("/usr/lib/moos/admin-releases")
CURRENT_LINK = Path("/usr/lib/moos/admin-current")
ROLLBACK_LINK = Path("/usr/lib/moos/admin-rollback")
RELEASE_RETENTION = 2
OPENSSL = Path("/usr/bin/openssl")
MAX_BUNDLE_BYTES = 160 * 1024 * 1024
MAX_SIGNATURE_BYTES = 64 * 1024
MAX_MEMBER_BYTES = 64 * 1024 * 1024
RELEASE_DIGEST = re.compile(r"[0-9a-f]{64}")

RELEASE_MEMBERS = {
    "GATEWAY.md": 0o644,
    "HOST_GUEST_ISOLATION.md": 0o644,
    "configs/control-plane-manifest.sha256": 0o644,
    "host/moos_protocol.py": 0o644,
    "host/moos_runtime.py": 0o644,
    "host/moosd.py": 0o644,
    "scripts/moos": 0o755,
    "scripts/moosd.py": 0o755,
    "scripts/run-instance.sh": 0o755,
    "scripts/run-qemu.sh": 0o755,
    "scripts/setup-control-plane.sh": 0o755,
    "scripts/setup-gateway.sh": 0o755,
    "scripts/setup-runtime-user.sh": 0o755,
    "scripts/stage-instance.sh": 0o755,
    "systemd/moos-gateway.service": 0o644,
    "systemd/moosd.service": 0o644,
    "systemd/moosd.socket": 0o644,
    "target/release/moos-gateway": 0o755,
    "target/release/moos-gateway-device": 0o755,
}


class InstallError(RuntimeError):
    """A fail-closed administrator-release installation error."""


def _read_opaque(source: Path, maximum: int) -> bytes:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(source, flags)
    except OSError as error:
        raise InstallError(f"unsafe or unreadable input {source}: {error}") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise InstallError(f"input must be a regular, singly-linked file: {source}")
        if before.st_size > maximum:
            raise InstallError(f"input exceeds its size limit: {source}")
        chunks = []
        length = 0
        while True:
            chunk = os.read(descriptor, min(65536, maximum + 1 - length))
            if not chunk:
                break
            chunks.append(chunk)
            length += len(chunk)
            if length > maximum:
                raise InstallError(f"input exceeds its size limit: {source}")
        after = os.fstat(descriptor)
        identity = lambda value: (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
        )
        if identity(before) != identity(after) or length != before.st_size:
            raise InstallError(f"input changed while being copied: {source}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _write_private(destination: Path, data: bytes, mode: int = 0o600) -> None:
    descriptor = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        mode,
    )
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _require_root_owned_file(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise InstallError(f"trusted file is unavailable: {path}: {error}") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or metadata.st_mode & 0o022
    ):
        raise InstallError(f"trusted file has unsafe ownership or mode: {path}")


def _require_root_owned_path(path: Path) -> None:
    current = path
    while True:
        metadata = current.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_mode & 0o022
        ):
            raise InstallError(f"trusted directory has unsafe ownership or mode: {current}")
        if current.parent == current:
            return
        current = current.parent


def authenticate_to_private_files(
    bundle: Path,
    signature: Path,
    trusted_key: Path,
    private_root: Path,
    *,
    openssl: Path = OPENSSL,
) -> tuple[Path, str]:
    """Opaque-copy inputs, authenticate the copied bytes, and return the bundle."""

    bundle_data = _read_opaque(bundle, MAX_BUNDLE_BYTES)
    signature_data = _read_opaque(signature, MAX_SIGNATURE_BYTES)
    key_data = _read_opaque(trusted_key, MAX_SIGNATURE_BYTES)
    staged_bundle = private_root / "release.tar"
    staged_signature = private_root / "release.sig"
    staged_key = private_root / "release.pem"
    _write_private(staged_bundle, bundle_data)
    _write_private(staged_signature, signature_data)
    _write_private(staged_key, key_data)
    result = subprocess.run(
        [
            str(openssl),
            "dgst",
            "-sha256",
            "-verify",
            str(staged_key),
            "-signature",
            str(staged_signature),
            str(staged_bundle),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        env={"PATH": "/usr/bin:/bin", "LANG": "C"},
        check=False,
    )
    if result.returncode != 0:
        raise InstallError("administrator release signature verification failed")
    return staged_bundle, hashlib.sha256(bundle_data).hexdigest()


def extract_authenticated_bundle(bundle: Path, destination: Path) -> None:
    """Extract an already authenticated archive using an exact allowlist."""

    seen = set()
    with tarfile.open(bundle, mode="r:") as archive:
        members = archive.getmembers()
        for member in members:
            if member.name in seen or member.name not in RELEASE_MEMBERS:
                raise InstallError(
                    f"administrator release has an unexpected member: {member.name}"
                )
            seen.add(member.name)
            if not member.isfile() or member.size > MAX_MEMBER_BYTES:
                raise InstallError(f"administrator release member is unsafe: {member.name}")
        if seen != set(RELEASE_MEMBERS):
            missing = sorted(set(RELEASE_MEMBERS) - seen)
            raise InstallError("administrator release is incomplete: " + ", ".join(missing))

        for member in members:
            target = destination / member.name
            target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            parent = target.parent
            while parent != destination:
                os.chmod(parent, 0o755)
                parent = parent.parent
            source = archive.extractfile(member)
            if source is None:
                raise InstallError(
                    f"could not read administrator release member: {member.name}"
                )
            data = source.read(MAX_MEMBER_BYTES + 1)
            if len(data) != member.size or len(data) > MAX_MEMBER_BYTES:
                raise InstallError(f"administrator release member changed size: {member.name}")
            _write_private(target, data, RELEASE_MEMBERS[member.name])


def _same_release(left: Path, right: Path, expected_uid: int) -> bool:
    actual = set()
    for candidate in right.rglob("*"):
        metadata = candidate.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            if metadata.st_uid != expected_uid or metadata.st_mode & 0o022:
                return False
            continue
        if not stat.S_ISREG(metadata.st_mode):
            return False
        actual.add(str(candidate.relative_to(right)))
    if actual != set(RELEASE_MEMBERS):
        return False
    for relative in RELEASE_MEMBERS:
        left_path = left / relative
        right_path = right / relative
        try:
            metadata = right_path.lstat()
            if (
                metadata.st_uid != expected_uid
                or stat.S_IMODE(metadata.st_mode) != RELEASE_MEMBERS[relative]
            ):
                return False
            left_data = _read_opaque(left_path, MAX_MEMBER_BYTES)
            right_data = _read_opaque(right_path, MAX_MEMBER_BYTES)
        except InstallError:
            return False
        if left_data != right_data:
            return False
    return True


def activate_extracted_release(
    extracted: Path,
    digest: str,
    release_root: Path,
    current_link: Path,
    *,
    expected_uid: int = 0,
    rollback_link: Path | None = None,
    retention: int = RELEASE_RETENTION,
) -> Path:
    """Activate a release and retain only active and rollback releases."""

    if retention < 2:
        raise InstallError("release retention must preserve active and rollback releases")
    if not RELEASE_DIGEST.fullmatch(digest):
        raise InstallError("administrator release digest is invalid")
    release_root = release_root.resolve()
    if rollback_link is not None and rollback_link == current_link:
        raise InstallError("current and rollback release pointers must differ")

    previous_target = _linked_release_target(current_link, release_root)
    if (
        rollback_link is not None
        and not current_link.is_symlink()
        and (rollback_link.exists() or rollback_link.is_symlink())
    ):
        raise InstallError("administrator rollback pointer exists without an active release")

    final = release_root / digest
    if final.is_symlink() or (final.exists() and not final.is_dir()):
        raise InstallError(f"existing administrator release is unsafe: {final}")
    if final.exists():
        if not _same_release(extracted, final, expected_uid):
            raise InstallError(f"existing administrator release is unsafe: {final}")
        shutil.rmtree(extracted)
    else:
        os.chmod(extracted, 0o755)
        os.replace(extracted, final)

    if rollback_link is not None and previous_target is not None:
        _replace_release_link(rollback_link, previous_target)

    _replace_release_link(current_link, final)
    if rollback_link is not None:
        cleanup_old_releases(
            release_root,
            current_link,
            rollback_link,
            retention=retention,
        )
    return final


def _linked_release_target(link: Path, release_root: Path) -> Path | None:
    if not link.is_symlink():
        if link.exists():
            raise InstallError(f"administrator release pointer is not a symlink: {link}")
        return None
    try:
        target = link.resolve(strict=True)
    except OSError as error:
        raise InstallError(f"administrator release pointer is broken: {link}") from error
    if target.parent != release_root or not RELEASE_DIGEST.fullmatch(target.name):
        raise InstallError(f"administrator release pointer targets an unsafe path: {link}")
    if not target.is_dir() or target.is_symlink():
        raise InstallError(f"administrator release pointer target is not a directory: {link}")
    return target


def _replace_release_link(link: Path, target: Path) -> None:
    if link.exists() and not link.is_symlink():
        raise InstallError(f"administrator release pointer is not a symlink: {link}")
    temporary_link = link.with_name(f".{link.name}.{os.getpid()}")
    try:
        temporary_link.unlink(missing_ok=True)
        os.symlink(target, temporary_link)
        os.replace(temporary_link, link)
    finally:
        temporary_link.unlink(missing_ok=True)


def cleanup_old_releases(
    release_root: Path,
    current_link: Path,
    rollback_link: Path,
    *,
    retention: int = RELEASE_RETENTION,
) -> None:
    """Remove unreferenced digest releases beyond the retention count."""

    if retention < 2:
        raise InstallError("release retention must preserve active and rollback releases")
    release_root = release_root.resolve()
    current = _linked_release_target(current_link, release_root)
    rollback = _linked_release_target(rollback_link, release_root)
    if current is None:
        raise InstallError("cannot clean releases without an active release")

    candidates = []
    for candidate in release_root.iterdir():
        if (
            candidate.parent == release_root
            and RELEASE_DIGEST.fullmatch(candidate.name)
            and candidate.is_dir()
            and not candidate.is_symlink()
        ):
            candidates.append(candidate)
    candidates.sort(
        key=lambda candidate: (candidate.stat().st_mtime_ns, candidate.name),
        reverse=True,
    )

    protected = {current}
    if rollback is not None:
        protected.add(rollback)
    keep = set(protected)
    for candidate in candidates:
        if len(keep) >= retention:
            break
        keep.add(candidate)
    for candidate in candidates:
        if candidate not in keep:
            shutil.rmtree(candidate)


def install_authenticated_release(bundle: Path, signature: Path) -> Path:
    _require_root_owned_file(TRUSTED_PROGRAM)
    _require_root_owned_file(TRUSTED_KEY)
    _require_root_owned_file(OPENSSL)
    _require_root_owned_path(TRUSTED_PROGRAM.parent)
    _require_root_owned_path(TRUSTED_KEY.parent)
    _require_root_owned_path(OPENSSL.parent)

    _require_root_owned_path(Path("/usr/lib"))
    if not RELEASE_ROOT.parent.exists():
        RELEASE_ROOT.parent.mkdir(mode=0o755)
    _require_root_owned_path(RELEASE_ROOT.parent)
    RELEASE_ROOT.mkdir(mode=0o755, exist_ok=True)
    _require_root_owned_path(RELEASE_ROOT)
    work_root = Path(tempfile.mkdtemp(prefix=".admin-install.", dir=RELEASE_ROOT))
    os.chmod(work_root, 0o700)
    extracted = work_root / "extracted"
    extracted.mkdir(mode=0o700)
    try:
        staged_bundle, digest = authenticate_to_private_files(
            bundle, signature, TRUSTED_KEY, work_root
        )
        extract_authenticated_bundle(staged_bundle, extracted)
        return activate_extracted_release(
            extracted,
            digest,
            RELEASE_ROOT,
            CURRENT_LINK,
            rollback_link=ROLLBACK_LINK,
        )
    finally:
        shutil.rmtree(work_root, ignore_errors=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--signature", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    if os.geteuid() != 0:
        print("error: the administrator release installer must run as root", file=sys.stderr)
        return 1
    if Path(__file__).resolve() != TRUSTED_PROGRAM:
        print(
            f"error: run only the provisioned trust anchor at {TRUSTED_PROGRAM}",
            file=sys.stderr,
        )
        return 1
    arguments = parse_args()
    try:
        release = install_authenticated_release(arguments.bundle, arguments.signature)
    except (InstallError, OSError, tarfile.TarError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"MOOS administrator release authenticated and installed: {release}")
    print(f"  active: {CURRENT_LINK}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
