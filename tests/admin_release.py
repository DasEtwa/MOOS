#!/usr/bin/env python3
"""Test the signed administrator-release trust boundary without root changes."""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from unittest.mock import patch
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests/fixtures"
TEST_GATEWAY_MEMBERS = {
    "target/release/moos-gateway",
    "target/release/moos-gateway-device",
}
TEST_GATEWAY_BINARY = b"\x7fELF" + b"MOOS test release\n"
sys.path.insert(0, str(REPO_ROOT / "host"))
import moos_admin_installer as admin  # noqa: E402
from moos_admin_installer import (  # noqa: E402
    InstallError,
    RELEASE_MEMBERS,
    activate_extracted_release,
    authenticate_to_private_files,
    extract_authenticated_bundle,
    load_verification_module,
)
from moos_verification import (  # noqa: E402
    ARTIFACT_ADMIN_RELEASE,
    ROLE_ADMIN_RELEASE,
    STATUS_VALID,
    VerificationPolicy,
    verify_archive_manifest,
)


def copy_release_source(destination: Path) -> None:
    for relative in RELEASE_MEMBERS:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        source = REPO_ROOT / relative
        if relative in TEST_GATEWAY_MEMBERS:
            target.write_bytes(TEST_GATEWAY_BINARY)
            target.chmod(0o755)
        else:
            assert source.is_file(), f"release source is missing: {relative}"
            shutil.copy2(source, target)


def run(*arguments: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(argument) for argument in arguments],
        text=True,
        capture_output=True,
        check=False,
    )


def read_hex_fixture(name: str) -> bytes:
    encoded = (FIXTURES / name).read_text(encoding="ascii")
    compact = "".join(encoded.split())
    assert re.fullmatch(r"[0-9a-f]+", compact), f"invalid hex fixture: {name}"
    assert len(compact) % 2 == 0, f"odd-length hex fixture: {name}"
    return bytes.fromhex(compact)


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
    assert 'Path("/usr/libexec/moos/moos_verification.py")' in helper_source
    assert "/usr/lib/moos/bootstrap" not in helper_source
    assert "MODULE_ROOT" not in helper_source
    assert 'parser.add_argument("--key"' not in helper_source
    install_source = helper_source[
        helper_source.index("def install_authenticated_release"):
        helper_source.index("def parse_args")
    ]
    authentication_call = install_source.index(
        "staged_bundle, digest = authenticate_to_private_files("
    )
    verification_anchor_call = install_source.index(
        "verification_module = load_verification_module()"
    )
    assert verification_anchor_call < authentication_call
    extraction_call = install_source.index(
        "extract_authenticated_bundle(staged_bundle, extracted)"
    )
    assert authentication_call < extraction_call
    try:
        load_verification_module()
    except InstallError as error:
        assert "trusted file" in str(error) or "trusted directory" in str(error)
    else:
        raise AssertionError("unprovisioned verification module was accepted")
    with patch.object(
        admin,
        "_require_root_owned_file",
        side_effect=InstallError("trusted file has unsafe ownership or mode"),
    ):
        try:
            load_verification_module()
        except InstallError as error:
            assert "unsafe ownership or mode" in str(error)
        else:
            raise AssertionError("unsafe verification module was accepted")
    builder_source = (REPO_ROOT / "scripts/build-admin-release.py").read_text(
        encoding="utf-8"
    )
    assert "--signing-key" not in builder_source
    nist_public_key = FIXTURES / "nist-rsa-pkcs1v15-sha256-public.pub"
    vector_message_data = read_hex_fixture(
        "nist-rsa-pkcs1v15-sha256-message.hex"
    )
    vector_signature_data = read_hex_fixture(
        "nist-rsa-pkcs1v15-sha256-signature.hex"
    )
    assert len(vector_message_data) == 128
    assert len(vector_signature_data) == 256
    assert hashlib.sha256(vector_message_data).hexdigest() == (
        "565ff4f36e8bd4a96007ed577f3248b4f9943826d721b417a20bb12c3ae6e874"
    )
    assert hashlib.sha256(vector_signature_data).hexdigest() == (
        "163d8eda000cc67e1af3d9e909731c895fb3c31e186faf2828e803067a89cf6f"
    )
    assert hashlib.sha256(nist_public_key.read_bytes()).hexdigest() == (
        "9804e93d8f3e1c0f8d291ec95b9830de13e966126e5d2d74ecc9c10cd7e58d76"
    )
    release_fixture = FIXTURES / "moos-admin-release-v1.tar"
    release_signature = FIXTURES / "moos-admin-release-v1.tar.sig"
    release_public_key = FIXTURES / "moos-admin-release-v1.pub"
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
        fixture_data = release_fixture.read_bytes()
        assert hashlib.sha256(fixture_data).hexdigest() == (
            "399de03de8faa07f0cd3bdbf01913bb68a0d2ac533f7c0cea23259ccfbb2f7d2"
        )
        # The historical signed fixture is immutable regression evidence. It is
        # intentionally not rewritten into the current canonical format.
        fixture_source = temporary / "fixture-source"
        fixture_source.mkdir()
        fixture_staging = temporary / "fixture-staging"
        fixture_staging.mkdir(mode=0o700)
        staged_fixture, _ = authenticate_to_private_files(
            release_fixture, release_signature, release_public_key, fixture_staging,
            openssl=openssl,
        )
        try:
            extract_authenticated_bundle(staged_fixture, fixture_source)
        except InstallError as error:
            assert "no canonical manifest" in str(error)
        extract_authenticated_bundle(staged_fixture, fixture_source, allow_legacy=True)
        current_core = fixture_source / "host/moos_verification.py"
        current_core.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / "host/moos_verification.py", current_core)
        fixture_generated = temporary / "fixture-generated.tar"
        result = run(builder, "--source-root", fixture_source, "--output", fixture_generated)
        assert result.returncode == 0, result.stderr
        assert fixture_generated.read_bytes() != fixture_data
        manifest_check = verify_archive_manifest(
            fixture_generated,
            policy=VerificationPolicy(
                expected_artifact_type=ARTIFACT_ADMIN_RELEASE,
                expected_role=ROLE_ADMIN_RELEASE,
                expected_platform="linux",
                expected_architecture="x86_64",
                expected_channel="stable",
            ),
        )
        assert manifest_check.manifest == STATUS_VALID, manifest_check.as_dict()
        assert manifest_check.integrity == STATUS_VALID, manifest_check.as_dict()
        assert manifest_check.policy == "ACCEPT", manifest_check.as_dict()
        assert hashlib.sha256(release_signature.read_bytes()).hexdigest() == (
            "31a22ab36a29d48e2de9370b50d0d165cea6fe333a00fcc3c6bccea63ee781f1"
        )
        assert hashlib.sha256(release_public_key.read_bytes()).hexdigest() == (
            "83165476282c93a07bdeff408860825d69e5462b6cccdd8537560a41be2501da"
        )

        legacy_release_staging = temporary / "legacy-release-staging"
        legacy_release_staging.mkdir(mode=0o700)
        authenticated_legacy, legacy_digest = authenticate_to_private_files(
            release_fixture,
            release_signature,
            release_public_key,
            legacy_release_staging,
            openssl=openssl,
        )
        assert authenticated_legacy.read_bytes() == fixture_data
        assert legacy_digest == hashlib.sha256(fixture_data).hexdigest()

        vector_message = temporary / "nist-vector-message"
        vector_message.write_bytes(vector_message_data)
        vector_signature = temporary / "nist-vector-signature"
        vector_signature.write_bytes(vector_signature_data)
        vector_private = temporary / "vector-private"
        vector_private.mkdir(mode=0o700)
        authenticated_vector, vector_digest = authenticate_to_private_files(
            vector_message,
            vector_signature,
            nist_public_key,
            vector_private,
            openssl=openssl,
        )
        assert authenticated_vector.read_bytes() == vector_message_data
        assert vector_digest == hashlib.sha256(vector_message_data).hexdigest()

        tampered_vector = temporary / "tampered-vector-message"
        tampered_vector.write_bytes(
            vector_message_data[:-1] + bytes([vector_message_data[-1] ^ 1])
        )
        rejected_vector = temporary / "rejected-vector"
        rejected_vector.mkdir()
        try:
            authenticate_to_private_files(
                tampered_vector,
                vector_signature,
                nist_public_key,
                rejected_vector,
                openssl=openssl,
            )
        except InstallError as error:
            assert "signature verification failed" in str(error)
        else:
            raise AssertionError("tampered public verification vector was accepted")

        extracted = temporary / "extracted"
        extracted.mkdir()
        extract_authenticated_bundle(fixture_generated, extracted)
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
        extract_authenticated_bundle(fixture_generated, invalid_digest_extract)
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
        extract_authenticated_bundle(fixture_generated, repeated)
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
        extract_authenticated_bundle(fixture_generated, symlinked_extract)
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
            extract_authenticated_bundle(fixture_generated, next_extracted)
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

        tampered_release = temporary / "tampered-release.tar"
        tampered_release.write_bytes(
            fixture_data[:-1] + bytes([fixture_data[-1] ^ 1])
        )
        tampered_signature = temporary / "tampered-release.sig"
        signature_data = release_signature.read_bytes()
        tampered_signature.write_bytes(
            signature_data[:-1] + bytes([signature_data[-1] ^ 1])
        )
        for label, candidate, signature in (
            ("bundle", tampered_release, release_signature),
            ("signature", release_fixture, tampered_signature),
        ):
            rejected_release = temporary / f"rejected-{label}"
            rejected_release.mkdir()
            try:
                authenticate_to_private_files(
                    candidate,
                    signature,
                    release_public_key,
                    rejected_release,
                    openssl=openssl,
                )
            except InstallError as error:
                assert "signature verification failed" in str(error)
            else:
                raise AssertionError(f"tampered release {label} was accepted")

        malicious = temporary / "malicious.tar"
        with tarfile.open(malicious, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            member = tarfile.TarInfo("scripts/setup-control-plane.sh")
            member.type = tarfile.SYMTYPE
            member.linkname = "/bin/sh"
            archive.addfile(member, io.BytesIO())
        malicious_extract = temporary / "malicious-extract"
        malicious_extract.mkdir()
        try:
            extract_authenticated_bundle(malicious, malicious_extract)
        except InstallError as error:
            assert "unsafe" in str(error) or "incomplete" in str(error)
        else:
            raise AssertionError("unsafe authenticated archive was extracted")

    print("MOOS administrator release trust-boundary test: PASS")
    print("  NIST public vector: opaque-copied and signature-verified; tamper rejected")
    print("  signed legacy fixture: staged authentication, normal install rejection, tamper rejected")
    print("  current bundle: canonical manifest metadata and member integrity verified")
    print("  authenticated archive: exact allowlist, regular files only")
    print("  malicious symlink archive: rejected")
    print("  documentation: no privileged checkout entry point")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
