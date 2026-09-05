#!/usr/bin/env python3
"""Test the signed administrator-release trust boundary without root changes."""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "host"))
from moos_admin_installer import (  # noqa: E402
    InstallError,
    RELEASE_MEMBERS,
    activate_extracted_release,
    authenticate_to_private_files,
    extract_authenticated_bundle,
)


def copy_release_source(destination: Path) -> None:
    for relative in RELEASE_MEMBERS:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        source = REPO_ROOT / relative
        if source.is_file():
            shutil.copy2(source, target)
        else:
            target.write_bytes(b"\x7fELF" + b"MOOS test release\n")
            target.chmod(0o755)


def run(*arguments: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(argument) for argument in arguments],
        text=True,
        capture_output=True,
        check=False,
    )


def sign(openssl: Path, private_key: Path, bundle: Path, signature: Path) -> None:
    subprocess.run(
        [
            str(openssl),
            "dgst",
            "-sha256",
            "-sign",
            str(private_key),
            "-out",
            str(signature),
            str(bundle),
        ],
        check=True,
    )


def main() -> int:
    openssl_name = shutil.which("openssl")
    assert openssl_name is not None, "openssl is required"
    openssl = Path(openssl_name)
    helper_source = (REPO_ROOT / "host/moos_admin_installer.py").read_text(
        encoding="utf-8"
    )
    assert helper_source.startswith("#!/usr/bin/python3 -I\n")
    assert 'Path("/usr/libexec/moos/moos-admin-installer")' in helper_source
    assert 'Path("/etc/moos/trust/admin-release.pem")' in helper_source
    assert 'parser.add_argument("--key"' not in helper_source
    builder_source = (REPO_ROOT / "scripts/build-admin-release.py").read_text(
        encoding="utf-8"
    )
    assert "--signing-key" not in builder_source
    for privileged_script in (
        "scripts/setup-control-plane.sh",
        "scripts/setup-gateway.sh",
        "scripts/setup-runtime-user.sh",
        "scripts/stage-instance.sh",
        "scripts/run-instance.sh",
    ):
        script_source = (REPO_ROOT / privileged_script).read_text(
            encoding="utf-8"
        )
        assert "PATH='/usr/sbin:/usr/bin:/sbin:/bin'" in script_source
        assert "never run" in script_source
    for documentation in ("README.md", "GATEWAY.md", "HOST_GUEST_ISOLATION.md"):
        content = (REPO_ROOT / documentation).read_text(encoding="utf-8")
        assert not re.search(r"^\s*sudo\s+\./", content, re.MULTILINE), documentation
        assert not re.search(r"^\s*sudo\s+python3\s+tests/", content, re.MULTILINE), documentation
    assert "ADMIN_RELEASES.md" in (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="moos-admin-release-test-") as temporary_name:
        temporary = Path(temporary_name)
        source = temporary / "source"
        copy_release_source(source)
        first = temporary / "first.tar"
        second = temporary / "second.tar"
        builder = REPO_ROOT / "scripts/build-admin-release.py"
        for output in (first, second):
            result = run(builder, "--source-root", source, "--output", output)
            assert result.returncode == 0, result.stderr
            assert (
                "signature: create only in the trusted release-signing environment"
                in result.stdout
            )
        assert first.read_bytes() == second.read_bytes(), "bundle is not deterministic"

        private_key = temporary / "release-private.pem"
        public_key = temporary / "release-public.pem"
        signature = temporary / "release.sig"
        subprocess.run(
            [
                str(openssl),
                "genpkey",
                "-algorithm",
                "RSA",
                "-pkeyopt",
                "rsa_keygen_bits:2048",
                "-out",
                str(private_key),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        subprocess.run(
            [
                str(openssl),
                "pkey",
                "-in",
                str(private_key),
                "-pubout",
                "-out",
                str(public_key),
            ],
            stdout=subprocess.DEVNULL,
            check=True,
        )
        sign(openssl, private_key, first, signature)

        private = temporary / "private"
        private.mkdir(mode=0o700)
        authenticated, _ = authenticate_to_private_files(
            first, signature, public_key, private, openssl=openssl
        )
        extracted = temporary / "extracted"
        extracted.mkdir()
        extract_authenticated_bundle(authenticated, extracted)
        extracted_files = {
            str(path.relative_to(extracted))
            for path in extracted.rglob("*")
            if path.is_file()
        }
        assert extracted_files == set(RELEASE_MEMBERS)
        releases = temporary / "releases"
        releases.mkdir()
        current = temporary / "current"
        invalid_digest_extract = temporary / "extracted-invalid-digest"
        invalid_digest_extract.mkdir()
        extract_authenticated_bundle(authenticated, invalid_digest_extract)
        try:
            activate_extracted_release(
                invalid_digest_extract,
                "../outside",
                releases,
                current,
                expected_uid=os.getuid(),
            )
        except InstallError as error:
            assert "digest is invalid" in str(error)
        else:
            raise AssertionError("release activation accepted an unsafe digest")
        final = activate_extracted_release(
            extracted,
            "a" * 64,
            releases,
            current,
            expected_uid=os.getuid(),
        )
        assert current.is_symlink() and current.resolve() == final

        repeated = temporary / "repeated"
        repeated.mkdir()
        extract_authenticated_bundle(authenticated, repeated)
        assert activate_extracted_release(
            repeated,
            "a" * 64,
            releases,
            current,
            expected_uid=os.getuid(),
        ) == final

        symlinked_target = releases / ("b" * 64)
        symlinked_target.symlink_to(final)
        symlinked_extract = temporary / "extracted-symlink"
        symlinked_extract.mkdir()
        extract_authenticated_bundle(authenticated, symlinked_extract)
        try:
            activate_extracted_release(
                symlinked_extract,
                "b" * 64,
                releases,
                current,
                expected_uid=os.getuid(),
            )
        except InstallError as error:
            assert "unsafe" in str(error)
        else:
            raise AssertionError("release activation followed a digest symlink")
        symlinked_target.unlink()

        rollback = temporary / "rollback"
        for digest in ("b" * 64, "c" * 64, "d" * 64):
            next_extracted = temporary / f"extracted-{digest[0]}"
            next_extracted.mkdir()
            extract_authenticated_bundle(authenticated, next_extracted)
            final = activate_extracted_release(
                next_extracted,
                digest,
                releases,
                current,
                expected_uid=os.getuid(),
                rollback_link=rollback,
            )

        assert current.resolve() == final
        assert rollback.resolve() == releases / ("c" * 64)
        assert (releases / ("c" * 64)).is_dir()
        assert (releases / ("d" * 64)).is_dir()
        assert not (releases / ("a" * 64)).exists()
        assert not (releases / ("b" * 64)).exists()

        tampered = temporary / "tampered.tar"
        tampered.write_bytes(
            first.read_bytes()[:-1] + bytes([first.read_bytes()[-1] ^ 1])
        )
        rejected = temporary / "rejected"
        rejected.mkdir()
        try:
            authenticate_to_private_files(
                tampered, signature, public_key, rejected, openssl=openssl
            )
        except InstallError as error:
            assert "signature verification failed" in str(error)
        else:
            raise AssertionError("tampered release passed signature verification")

        malicious = temporary / "malicious.tar"
        with tarfile.open(malicious, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            member = tarfile.TarInfo("scripts/setup-control-plane.sh")
            member.type = tarfile.SYMTYPE
            member.linkname = "/bin/sh"
            archive.addfile(member, io.BytesIO())
        malicious_signature = temporary / "malicious.sig"
        sign(openssl, private_key, malicious, malicious_signature)
        malicious_private = temporary / "malicious-private"
        malicious_private.mkdir()
        authenticated_malicious, _ = authenticate_to_private_files(
            malicious, malicious_signature, public_key, malicious_private, openssl=openssl
        )
        malicious_extract = temporary / "malicious-extract"
        malicious_extract.mkdir()
        try:
            extract_authenticated_bundle(authenticated_malicious, malicious_extract)
        except InstallError as error:
            assert "unsafe" in str(error) or "incomplete" in str(error)
        else:
            raise AssertionError("unsafe authenticated archive was extracted")

    print("MOOS administrator release trust-boundary test: PASS")
    print("  checkout bytes: opaque-copied and signature-verified before archive parsing")
    print("  archive: deterministic, exact allowlist, regular files only")
    print("  tampered signature and authenticated symlink archive: rejected")
    print("  documentation: no privileged checkout entry point")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
