#!/usr/bin/env python3
"""Focused public-only tests for the local MOOS verification core."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "host"))

import moos_verification as verification  # noqa: E402
from moos_verification import (  # noqa: E402
    ARTIFACT_ADMIN_RELEASE,
    ARTIFACT_HOST_PACKAGE,
    ARTIFACT_PERSONAL_RUNTIME,
    FileRecord,
    MANIFEST_NAME,
    PACKAGE_MANIFEST_NAME,
    PROVENANCE_PACKAGE_MEMBERSHIP,
    RESULT_INTEGRITY_ONLY,
    RESULT_REJECTED,
    RESULT_VERIFIED,
    ROLE_ADMIN_RELEASE,
    ROLE_HOST_RELEASE,
    ROLE_PERSONAL_RUNTIME,
    ManifestError,
    SignatureReport,
    STATUS_INVALID,
    STATUS_VALID,
    TrustError,
    TrustStore,
    VerificationPolicy,
    build_manifest,
    canonical_manifest_bytes,
    evaluate_policy,
    package_membership_result,
    parse_manifest,
    verify_archive_manifest,
    verify_detached_signature_bytes,
    verify_integrity,
    verify_legacy_admin_fixture,
    verify_manifest_payload,
    verify_signed_archive,
)


def read_hex(name: str) -> bytes:
    return bytes.fromhex("".join((FIXTURES / name).read_text(encoding="ascii").split()))


def sample_files() -> dict[str, bytes]:
    return {"host/moosd.py": b"fixed daemon\n", "images/rootfs.ext2": b"fixed image\n"}


def sample_manifest(**overrides):
    values = dict(
        artifact_type=ARTIFACT_ADMIN_RELEASE,
        version="1.2.3",
        platform="linux",
        architecture="x86_64",
        channel="stable",
        role=ROLE_ADMIN_RELEASE,
        files=sample_files(),
    )
    values.update(overrides)
    return build_manifest(**values)


def archive_for(manifest, files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, data in sorted(files.items()):
            item = tarfile.TarInfo(name)
            item.size = len(data)
            item.mode = 0o644
            item.uid = item.gid = item.mtime = 0
            archive.addfile(item, io.BytesIO(data))
        item = tarfile.TarInfo(MANIFEST_NAME)
        manifest_bytes = canonical_manifest_bytes(manifest)
        item.size = len(manifest_bytes)
        item.mode = 0o644
        item.uid = item.gid = item.mtime = 0
        archive.addfile(item, io.BytesIO(manifest_bytes))
    return output.getvalue()


def raw_archive(entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for item in entries:
            info = tarfile.TarInfo(item[0])
            info.size = len(item[1])
            info.mode = 0o644
            info.uid = info.gid = info.mtime = 0
            if len(item) > 2:
                info.type = item[2]
                if info.type == tarfile.SYMTYPE:
                    info.linkname = "target"
            archive.addfile(info, io.BytesIO(item[1]) if info.isfile() else None)
    return output.getvalue()


def main() -> int:
    public = FIXTURES / "nist-rsa-pkcs1v15-sha256-public.pub"
    message = read_hex("nist-rsa-pkcs1v15-sha256-message.hex")
    signature = read_hex("nist-rsa-pkcs1v15-sha256-signature.hex")
    public_bytes = public.read_bytes()

    # The only valid detached-signature evidence is the immutable public NIST
    # vector.  No release identity is generated in this checkout.
    report = verify_detached_signature_bytes(message, signature, public_bytes)
    assert report.status == STATUS_VALID, report
    tampered = message[:-1] + bytes([message[-1] ^ 1])
    assert verify_detached_signature_bytes(tampered, signature, public_bytes).status == STATUS_INVALID
    assert verify_detached_signature_bytes(message, signature[:-1] + b"\0", public_bytes).status == STATUS_INVALID
    assert verify_detached_signature_bytes(message, signature, b"-----BEGIN PRIVATE KEY-----\n").code == "privateKeyRejected"
    assert verify_detached_signature_bytes(message, b"x" * (64 * 1024 + 1), public_bytes).code == "signatureInput"

    files = sample_files()
    manifest = sample_manifest()
    encoded = canonical_manifest_bytes(manifest)
    assert parse_manifest(encoded) == manifest
    assert encoded == canonical_manifest_bytes(parse_manifest(encoded))
    assert canonical_manifest_bytes(manifest) == canonical_manifest_bytes(sample_manifest())
    for malformed in (
        encoded.replace(b'"files":', b'"files":', 1)[:-1] + b',"role":"ADMIN_RELEASE"}',
        b" " + encoded,
        encoded.replace(b'"size":13', b'"size":true'),
        encoded.replace(b'"host/moosd.py"', b'"../host/moosd.py"'),
    ):
        try:
            parse_manifest(malformed)
        except ValueError:
            pass
        else:
            raise AssertionError("malformed/noncanonical manifest was accepted")

    # Payload hashes and sizes are checked independently of policy.
    assert verify_integrity(files, manifest).valid
    changed = dict(files)
    changed["host/moosd.py"] = b"changed"
    assert verify_integrity(changed, manifest).code == "sizeMismatch"
    wrong = dict(manifest.files)
    wrong["host/moosd.py"] = FileRecord("0" * 64, len(files["host/moosd.py"]))
    wrong_manifest = sample_manifest(files=wrong)
    assert verify_integrity(files, wrong_manifest).code == "hashMismatch"
    assert verify_integrity(files | {"extra": b"x"}, manifest).code == "memberSet"

    # Artifact type and release role are a fixed security mapping, independent
    # of caller policy.
    for artifact_type, role in (
        (ARTIFACT_HOST_PACKAGE, ROLE_HOST_RELEASE),
        (ARTIFACT_ADMIN_RELEASE, ROLE_ADMIN_RELEASE),
        (ARTIFACT_PERSONAL_RUNTIME, ROLE_PERSONAL_RUNTIME),
    ):
        assert build_manifest(
            artifact_type=artifact_type,
            version="1.2.3",
            platform="linux",
            architecture="x86_64",
            channel="stable",
            role=role,
            files=files,
        ).role == role
    for artifact_type, role in (
        (ARTIFACT_HOST_PACKAGE, ROLE_ADMIN_RELEASE),
        (ARTIFACT_ADMIN_RELEASE, ROLE_HOST_RELEASE),
        (ARTIFACT_PERSONAL_RUNTIME, ROLE_ADMIN_RELEASE),
    ):
        try:
            build_manifest(
                artifact_type=artifact_type,
                version="1.2.3",
                platform="linux",
                architecture="x86_64",
                channel="stable",
                role=role,
                files=files,
            )
        except ManifestError as error:
            assert error.code == "roleMapping"
        else:
            raise AssertionError("artifact type accepted an unauthorized role")

    # A public SignatureReport is not cryptographic evidence.  It must not be
    # usable to manufacture VERIFIED from an arbitrary payload.
    valid_signature = SignatureReport(STATUS_VALID, "ok", "test vector accepted")
    forged = verify_manifest_payload(
        encoded,
        files,
        signature=valid_signature,
        policy=VerificationPolicy(expected_role=ROLE_ADMIN_RELEASE),
        provenance="detachedSignature",
    )
    assert forged.result == RESULT_REJECTED, forged.as_dict()
    assert any(issue.code == "untrustedSignatureEvidence" for issue in forged.issues)
    for policy, code in (
        (VerificationPolicy(expected_role=ROLE_HOST_RELEASE), "wrongRole"),
        (VerificationPolicy(expected_platform="darwin"), "wrongPlatform"),
        (VerificationPolicy(expected_channel="beta"), "wrongChannel"),
        (VerificationPolicy(current_version="2.0.0"), "rollbackRejected"),
    ):
        result = verify_manifest_payload(encoded, files, signature=valid_signature, policy=policy,
                                         provenance="detachedSignature")
        assert result.result == RESULT_REJECTED, result.as_dict()
        assert any(issue.code == code for issue in result.issues), result.as_dict()
    rollback_manifest = sample_manifest(rollback=True, rollback_of="2.0.0")
    assert not evaluate_policy(rollback_manifest, VerificationPolicy(current_version="2.0.0")).accepted
    assert evaluate_policy(rollback_manifest, VerificationPolicy(current_version="2.0.0", allow_rollback=True)).accepted
    mismatch = evaluate_policy(
        rollback_manifest,
        VerificationPolicy(current_version="1.9.9", allow_rollback=True),
    )
    assert not mismatch.accepted
    assert any(issue.code == "rollbackTargetMismatch" for issue in mismatch.issues)
    assert not evaluate_policy(rollback_manifest, VerificationPolicy(allow_rollback=True)).accepted
    for version in ("2.0.0", "3.0.0"):
        equal_or_newer = sample_manifest(version=version, rollback=True, rollback_of="2.0.0")
        denied = evaluate_policy(equal_or_newer, VerificationPolicy(current_version="2.0.0"))
        assert not denied.accepted
        assert any(issue.code == "rollbackNotAuthorized" for issue in denied.issues)
        assert evaluate_policy(
            equal_or_newer,
            VerificationPolicy(current_version="2.0.0", allow_rollback=True),
        ).accepted
    try:
        sample_manifest(rollback=True)
    except ManifestError as error:
        assert error.code == "rollback"
    else:
        raise AssertionError("rollback=true without rollbackOf was accepted")
    assert evaluate_policy(sample_manifest(minimum_version="1.0.0"), VerificationPolicy(current_version="1.0.0")).accepted
    assert not evaluate_policy(sample_manifest(minimum_version="2.0.0"), VerificationPolicy(current_version="1.0.0")).accepted

    # A public signature report cannot authorize direct archive parsing.
    with tempfile.TemporaryDirectory(prefix="moos-verification-test-") as temporary:
        root = Path(temporary)
        archive = root / "release.tar"
        archive_bytes = archive_for(manifest, files)
        archive.write_bytes(archive_bytes)
        untrusted = verify_archive_manifest(
            archive,
            signature=valid_signature,
            policy=VerificationPolicy(expected_role=ROLE_ADMIN_RELEASE, expected_platform="linux",
                                       expected_architecture="x86_64", expected_channel="stable"),
            provenance="detachedSignature",
        )
        assert untrusted.result == RESULT_REJECTED, untrusted.as_dict()
        assert any(issue.code == "untrustedSignatureEvidence" for issue in untrusted.issues)
        tampered = dict(files)
        tampered["host/moosd.py"] = b"wrong bytes"
        tampered_archive = root / "tampered.tar"
        tampered_archive.write_bytes(archive_for(manifest, tampered))
        rejected = verify_archive_manifest(tampered_archive, signature=valid_signature,
                                           provenance="detachedSignature")
        assert rejected.result == RESULT_REJECTED
        assert any(issue.code == "untrustedSignatureEvidence" for issue in rejected.issues)

        # Simulate the cryptographic verifier while replacing the pathname
        # after authentication.  The authenticated bytes must still be the
        # bytes parsed and checked by the rest of the pipeline.
        key = root / "public.pem"
        shutil.copy2(public, key)
        os.chmod(key, 0o600)
        sig = root / "release.tar.sig"
        sig.write_bytes(b"detached signature")
        tampered_bytes = tampered_archive.read_bytes()
        original_verify = verification.verify_detached_signature_bytes

        def authenticate_then_replace(message_bytes, detached_bytes, public_key, *, openssl):
            assert message_bytes == archive_bytes
            archive.write_bytes(tampered_bytes)
            return SignatureReport(STATUS_VALID, "ok", "test cryptographic verifier")

        verification.verify_detached_signature_bytes = authenticate_then_replace
        try:
            accepted = verify_signed_archive(
                archive,
                sig,
                trust=TrustStore({ROLE_ADMIN_RELEASE: key}, require_protected=False),
                expected_role=ROLE_ADMIN_RELEASE,
                policy=VerificationPolicy(expected_role=ROLE_ADMIN_RELEASE),
            )
        finally:
            verification.verify_detached_signature_bytes = original_verify
        assert accepted.result == RESULT_VERIFIED, accepted.as_dict()
        assert accepted.integrity == STATUS_VALID

        # Real verification still rejects the altered archive before parsing
        # when the expected role is supplied.
        early = verify_signed_archive(
            tampered_archive,
            sig,
            trust=TrustStore({ROLE_ADMIN_RELEASE: key}, require_protected=False),
            expected_role=ROLE_ADMIN_RELEASE,
        )
        assert early.signature == STATUS_INVALID
        assert early.manifest == "NOT_CHECKED"

        # Safe key replacement is still a continuity failure after first read.
        continuity_key = root / "continuity.pem"
        shutil.copy2(public, continuity_key)
        os.chmod(continuity_key, 0o600)
        continuity = TrustStore({ROLE_ADMIN_RELEASE: continuity_key}, require_protected=False)
        assert continuity.public_key(ROLE_ADMIN_RELEASE) == public_bytes
        continuity_key.write_bytes((FIXTURES / "nist-ecdsa-p256-public.pub").read_bytes())
        try:
            continuity.public_key(ROLE_ADMIN_RELEASE)
        except TrustError as error:
            assert error.code == "trustChanged"
        else:
            raise AssertionError("safe trust-key replacement was silently adopted")

        explicit_key = root / "explicit.pem"
        explicit_key.write_bytes(public_bytes)
        os.chmod(explicit_key, 0o600)
        explicit = TrustStore(
            {ROLE_ADMIN_RELEASE: explicit_key},
            require_protected=False,
            expected_digests={ROLE_ADMIN_RELEASE: hashlib.sha256(public_bytes).hexdigest()},
        )
        assert explicit.public_key(ROLE_ADMIN_RELEASE) == public_bytes
        explicit_key.write_bytes((FIXTURES / "nist-ecdsa-p256-public.pub").read_bytes())
        try:
            explicit.public_key(ROLE_ADMIN_RELEASE)
        except TrustError as error:
            assert error.code == "trustMismatch"
        else:
            raise AssertionError("explicit trust digest mismatch was accepted")

        # Archive member hazards are rejected before any payload is trusted.
        for index, bad_member in enumerate(
            (
                ("../escape", b"x"),
                ("dir\\escape", b"x"),
                ("linked", b"", tarfile.SYMTYPE),
                ("directory", b"", tarfile.DIRTYPE),
                ("fifo", b"", tarfile.FIFOTYPE),
            )
        ):
            bad_archive = root / f"bad-{index}.tar"
            bad_archive.write_bytes(raw_archive([bad_member]))
            bad_result = verify_archive_manifest(bad_archive)
            assert bad_result.result == RESULT_REJECTED
            assert any(issue.code == "unsafeArchive" for issue in bad_result.issues), bad_result.as_dict()

        duplicate_archive = root / "duplicate.tar"
        duplicate_archive.write_bytes(raw_archive([("same", b"one"), ("same", b"two")]))
        duplicate_result = verify_archive_manifest(duplicate_archive)
        assert any(issue.code == "duplicateMember" for issue in duplicate_result.issues), duplicate_result.as_dict()

        # A symlinked artifact root and symlinked ancestry are not directories
        # that can be safely verified.
        directory = root / "artifact"
        directory.mkdir()
        (directory / MANIFEST_NAME).write_bytes(encoded)
        for name, data in files.items():
            destination = directory / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        root_link = root / "artifact-link"
        root_link.symlink_to(directory, target_is_directory=True)
        assert verification.verify_directory_integrity(root_link, manifest).code == "unsafeDirectory"
        parent = root / "parent"
        parent.mkdir()
        nested = parent / "nested"
        nested.mkdir()
        nested_link_parent = root / "parent-link"
        nested_link_parent.symlink_to(parent, target_is_directory=True)
        assert verification.verify_directory_integrity(nested_link_parent / "nested", manifest).code == "unsafeDirectory"

    # Package membership is an external authenticity prerequisite, not a
    # detached signature claim.  Local evidence is integrity-only.
    package = package_membership_result(encoded, files,
                                        policy=VerificationPolicy(expected_role=ROLE_ADMIN_RELEASE))
    assert package.provenance == PROVENANCE_PACKAGE_MEMBERSHIP
    assert package.signature == "NOT_PRESENT"
    assert package.result == RESULT_INTEGRITY_ONLY

    legacy = verify_legacy_admin_fixture(FIXTURES / "moos-admin-release-v1.tar",
                                         signature=valid_signature)
    assert legacy.result == RESULT_REJECTED
    assert legacy.manifest == "LEGACY"
    assert legacy.provenance != PROVENANCE_PACKAGE_MEMBERSHIP

    print("MOOS verification core test: PASS")
    print("  canonical strict manifest, duplicate rejection and deterministic bytes: ok")
    print("  public detached signature, integrity and policy boundaries: ok")
    print("  archive authentication ordering and package/legacy provenance: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
