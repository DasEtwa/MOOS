#!/usr/bin/env python3
"""Small, local verification core for official MOOS artifacts.

The module deliberately contains no signing or key-generation support.  It
only consumes public verification material, canonical manifests and detached
signatures produced outside the checkout.  Callers that cross a privilege
boundary must still arrange for opaque private staging before calling the
verification helpers here.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = 1
MANIFEST_NAME = "moos-manifest.json"
PACKAGE_MANIFEST_NAME = "manifest.json"

ROLE_HOST_RELEASE = "HOST_RELEASE"
ROLE_ADMIN_RELEASE = "ADMIN_RELEASE"
ROLE_PERSONAL_RUNTIME = "PERSONAL_RUNTIME"
OFFICIAL_ROLES = frozenset(
    {ROLE_HOST_RELEASE, ROLE_ADMIN_RELEASE, ROLE_PERSONAL_RUNTIME}
)

ARTIFACT_HOST_PACKAGE = "HOST_PACKAGE"
ARTIFACT_ADMIN_RELEASE = "ADMIN_RELEASE"
ARTIFACT_PERSONAL_RUNTIME = "PERSONAL_RUNTIME"
OFFICIAL_ARTIFACT_TYPES = frozenset(
    {ARTIFACT_HOST_PACKAGE, ARTIFACT_ADMIN_RELEASE, ARTIFACT_PERSONAL_RUNTIME}
)
ARTIFACT_ROLE_MAP = {
    ARTIFACT_HOST_PACKAGE: ROLE_HOST_RELEASE,
    ARTIFACT_ADMIN_RELEASE: ROLE_ADMIN_RELEASE,
    ARTIFACT_PERSONAL_RUNTIME: ROLE_PERSONAL_RUNTIME,
}

PUBLISHER_OFFICIAL = "MOOS_OFFICIAL"
PROVENANCE_SIGNATURE = "detachedSignature"
PROVENANCE_PACKAGE_MEMBERSHIP = "packageMembership"
PROVENANCE_LEGACY_FIXTURE = "legacyFixture"
PROVENANCE_NONE = "none"

STATUS_VALID = "VALID"
STATUS_INVALID = "INVALID"
STATUS_NOT_PRESENT = "NOT_PRESENT"
STATUS_NOT_CHECKED = "NOT_CHECKED"

RESULT_VERIFIED = "VERIFIED"
RESULT_REJECTED = "REJECTED"
RESULT_INTEGRITY_ONLY = "INTEGRITY_ONLY"

DEFAULT_OPENSSL = Path("/usr/bin/openssl")
DEFAULT_TRUST_PATHS = {
    ROLE_HOST_RELEASE: Path("/etc/moos/trust/host-release.pem"),
    ROLE_ADMIN_RELEASE: Path("/etc/moos/trust/admin-release.pem"),
    ROLE_PERSONAL_RUNTIME: Path("/etc/moos/trust/personal-runtime.pem"),
}

MAX_MANIFEST_BYTES = 1024 * 1024
MAX_MANIFEST_FILES = 512
MAX_PATH_BYTES = 512
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_SIGNATURE_BYTES = 64 * 1024
MAX_PUBLIC_KEY_BYTES = 64 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_MEMBER_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = MAX_MANIFEST_FILES + 2

_SEMVER = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z"
)
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}\Z")
_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_PATH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,511}\Z")


class VerificationError(ValueError):
    """A bounded, machine-readable verification failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ManifestError(VerificationError):
    """The manifest is malformed, ambiguous or not canonical."""


class TrustError(VerificationError):
    """Public trust material is missing, unsafe or invalid."""


@dataclass(frozen=True)
class VerificationIssue:
    code: str
    message: str
    stage: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "stage": self.stage}


@dataclass(frozen=True)
class FileRecord:
    """One exact payload record in a canonical manifest."""

    sha256: str
    size: int

    def as_dict(self) -> dict[str, Any]:
        return {"sha256": self.sha256, "size": self.size}


@dataclass(frozen=True)
class Manifest:
    """Normalized representation of a strict official-artifact manifest."""

    artifact_type: str
    version: str
    platform: str
    architecture: str
    channel: str
    publisher: str
    role: str
    files: Mapping[str, FileRecord]
    minimum_version: str | None = None
    rollback: bool = False
    rollback_of: str | None = None
    schema_version: int = SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "architecture": self.architecture,
            "artifactType": self.artifact_type,
            "channel": self.channel,
            "files": {
                name: self.files[name].as_dict() for name in sorted(self.files)
            },
            "platform": self.platform,
            "publisher": self.publisher,
            "role": self.role,
            "schemaVersion": self.schema_version,
            "version": self.version,
        }
        if self.minimum_version is not None:
            data["minimumVersion"] = self.minimum_version
        if self.rollback:
            data["rollback"] = True
        if self.rollback_of is not None:
            data["rollbackOf"] = self.rollback_of
        return data


@dataclass(frozen=True)
class SignatureReport:
    status: str
    code: str
    message: str

    @property
    def valid(self) -> bool:
        return self.status == STATUS_VALID

    def as_dict(self) -> dict[str, str]:
        return {"status": self.status, "code": self.code, "message": self.message}


# A public SignatureReport is a display/result value and must never be enough
# to manufacture cryptographic evidence.  Only this module can create the
# internal wrapper used by the signed-artifact path.
_SIGNATURE_EVIDENCE_TOKEN = object()


@dataclass(frozen=True)
class _VerifiedSignature:
    report: SignatureReport
    token: object

    @property
    def valid(self) -> bool:
        return self.report.status == STATUS_VALID and self.token is _SIGNATURE_EVIDENCE_TOKEN


def _verified_signature(report: SignatureReport) -> _VerifiedSignature:
    if report.status != STATUS_VALID:
        raise ValueError("only a valid cryptographic report can be wrapped")
    return _VerifiedSignature(report, _SIGNATURE_EVIDENCE_TOKEN)


def _trusted_signature_report(
    signature: SignatureReport | _VerifiedSignature | None,
) -> SignatureReport | None:
    if signature is None:
        return None
    if isinstance(signature, _VerifiedSignature) and signature.valid:
        return signature.report
    if isinstance(signature, SignatureReport):
        if signature.status == STATUS_VALID:
            return SignatureReport(
                STATUS_INVALID,
                "untrustedSignatureEvidence",
                "signature validity must come from cryptographic verification",
            )
        return signature
    return SignatureReport(STATUS_INVALID, "signatureEvidence", "unsupported signature evidence")


def _external_signature_report(signature: SignatureReport | None) -> SignatureReport | None:
    """Convert public signature input into non-authorizing result evidence."""

    if signature is None:
        return None
    if not isinstance(signature, SignatureReport):
        return SignatureReport(STATUS_INVALID, "signatureEvidence", "unsupported signature evidence")
    return _trusted_signature_report(signature)


@dataclass(frozen=True)
class IntegrityReport:
    status: str
    code: str
    message: str
    checked_files: int = 0

    @property
    def valid(self) -> bool:
        return self.status == STATUS_VALID

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "code": self.code,
            "message": self.message,
            "checkedFiles": self.checked_files,
        }


@dataclass(frozen=True)
class VerificationPolicy:
    """Caller-supplied acceptance constraints, never artifact claims."""

    expected_role: str | None = None
    expected_artifact_type: str | None = None
    expected_platform: str | None = None
    expected_architecture: str | None = None
    expected_channel: str | None = None
    expected_version: str | None = None
    current_version: str | None = None
    minimum_version: str | None = None
    allow_rollback: bool = False


@dataclass(frozen=True)
class PolicyReport:
    status: str
    rollback: str
    issues: tuple[VerificationIssue, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.status == "ACCEPT"


@dataclass(frozen=True)
class VerificationResult:
    """Structured evidence suitable for CLI, doctor and future UI clients."""

    signature: str = STATUS_NOT_CHECKED
    integrity: str = STATUS_NOT_CHECKED
    manifest: str = STATUS_NOT_CHECKED
    publisher: str | None = None
    role: str | None = None
    channel: str | None = None
    version: str | None = None
    artifact_type: str | None = None
    platform: str | None = None
    architecture: str | None = None
    rollback: str = "UNKNOWN"
    policy: str = "NOT_CHECKED"
    provenance: str = PROVENANCE_NONE
    result: str = RESULT_REJECTED
    issues: tuple[VerificationIssue, ...] = ()

    @property
    def verified(self) -> bool:
        return self.result == RESULT_VERIFIED

    def as_dict(self) -> dict[str, Any]:
        # Keep field names stable and JSON-friendly.  The lower-camel
        # provenance values deliberately distinguish external package
        # membership from a locally verified detached signature.
        return {
            "signature": self.signature,
            "integrity": self.integrity,
            "manifest": self.manifest,
            "publisher": self.publisher,
            "role": self.role,
            "channel": self.channel,
            "version": self.version,
            "artifactType": self.artifact_type,
            "platform": self.platform,
            "architecture": self.architecture,
            "rollback": self.rollback,
            "policy": self.policy,
            "provenance": self.provenance,
            "result": self.result,
            "issues": [issue.as_dict() for issue in self.issues],
        }

    # A small mapping-like surface is useful to callers that want structured
    # JSON without making the result a mutable dictionary.
    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]


def _issue(code: str, message: str, stage: str) -> VerificationIssue:
    return VerificationIssue(code, message, stage)


def _duplicate_rejecting_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError("duplicateKey", f"manifest contains duplicate key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ManifestError("invalidNumber", f"manifest contains unsupported JSON constant: {value}")


def _string(value: Any, field: str, *, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ManifestError("invalidField", f"manifest field {field} must be a bounded string")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise ManifestError("invalidField", f"manifest field {field} has an invalid value")
    return value


def _version(value: Any, field: str) -> str:
    return _string(value, field, pattern=_SEMVER)


def _safe_relative_path(value: Any) -> str:
    name = _string(value, "files member", pattern=_SAFE_PATH)
    if (
        name.startswith("/")
        or name.endswith("/")
        or "\\" in name
        or any(part in {"", ".", ".."} for part in name.split("/"))
        or len(name.encode("utf-8")) > MAX_PATH_BYTES
    ):
        raise ManifestError("unsafePath", f"manifest contains an unsafe file path: {name}")
    return name


def _manifest_from_object(value: Mapping[str, Any]) -> Manifest:
    required = {
        "schemaVersion",
        "artifactType",
        "version",
        "platform",
        "architecture",
        "channel",
        "publisher",
        "role",
        "files",
    }
    optional = {"minimumVersion", "rollback", "rollbackOf"}
    if not isinstance(value, dict):
        raise ManifestError("notObject", "manifest root must be a JSON object")
    keys = set(value)
    if keys - required - optional or required - keys:
        raise ManifestError("manifestKeys", "manifest has missing or unexpected fields")
    schema = value["schemaVersion"]
    if type(schema) is not int or schema != SCHEMA_VERSION:
        raise ManifestError("schemaVersion", "unsupported manifest schema version")
    artifact_type = _string(value["artifactType"], "artifactType", pattern=_TOKEN)
    if artifact_type not in OFFICIAL_ARTIFACT_TYPES:
        raise ManifestError("artifactType", "manifest artifact type is not an official MOOS type")
    version = _version(value["version"], "version")
    platform = _string(value["platform"], "platform", pattern=_TOKEN)
    architecture = _string(value["architecture"], "architecture", pattern=_TOKEN)
    channel = _string(value["channel"], "channel", pattern=_TOKEN)
    publisher = _string(value["publisher"], "publisher", pattern=_TOKEN)
    if publisher != PUBLISHER_OFFICIAL:
        raise ManifestError("publisher", "manifest publisher is not MOOS official")
    role = _string(value["role"], "role", pattern=_TOKEN)
    if role not in OFFICIAL_ROLES:
        raise ManifestError("role", "manifest role is not an authorized MOOS role")
    if ARTIFACT_ROLE_MAP[artifact_type] != role:
        raise ManifestError(
            "roleMapping",
            "manifest artifact type is not authorized for its role",
        )
    files_value = value["files"]
    if not isinstance(files_value, dict) or not files_value or len(files_value) > MAX_MANIFEST_FILES:
        raise ManifestError("files", "manifest files must be a bounded non-empty object")
    files: dict[str, FileRecord] = {}
    for raw_name, raw_record in files_value.items():
        name = _safe_relative_path(raw_name)
        if name in files:
            # Mapping input cannot normally contain duplicates; this also
            # protects callers that construct a custom Mapping implementation.
            raise ManifestError("duplicateFile", f"manifest contains duplicate file: {name}")
        if not isinstance(raw_record, dict) or set(raw_record) != {"sha256", "size"}:
            raise ManifestError("fileRecord", f"invalid manifest record for {name}")
        digest = _string(raw_record["sha256"], f"files.{name}.sha256", pattern=_HEX_64)
        size = raw_record["size"]
        if type(size) is not int or size < 0 or size > MAX_FILE_BYTES:
            raise ManifestError("fileSize", f"invalid manifest size for {name}")
        files[name] = FileRecord(digest, size)
    minimum_version = None
    if "minimumVersion" in value:
        minimum_version = _version(value["minimumVersion"], "minimumVersion")
    rollback = value.get("rollback", False)
    if type(rollback) is not bool:
        raise ManifestError("rollback", "manifest rollback must be a boolean")
    rollback_of = None
    if "rollbackOf" in value:
        rollback_of = _version(value["rollbackOf"], "rollbackOf")
        if not rollback:
            raise ManifestError("rollback", "rollbackOf requires rollback=true")
    if rollback and rollback_of is None:
        raise ManifestError("rollback", "rollback=true requires rollbackOf")
    return Manifest(
        artifact_type=artifact_type,
        version=version,
        platform=platform,
        architecture=architecture,
        channel=channel,
        publisher=publisher,
        role=role,
        files=files,
        minimum_version=minimum_version,
        rollback=rollback,
        rollback_of=rollback_of,
        schema_version=schema,
    )


def canonical_manifest_bytes(manifest: Manifest | Mapping[str, Any]) -> bytes:
    """Serialize an official manifest to its one canonical byte form."""

    normalized = manifest if isinstance(manifest, Manifest) else _manifest_from_object(manifest)
    return json.dumps(
        normalized.as_dict(),
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


serialize_manifest = canonical_manifest_bytes


def parse_manifest(data: bytes | bytearray | memoryview | str) -> Manifest:
    """Parse a strict, duplicate-free manifest and require canonical bytes."""

    if isinstance(data, str):
        raw = data.encode("utf-8")
    elif isinstance(data, (bytes, bytearray, memoryview)):
        raw = bytes(data)
    else:
        raise ManifestError("encoding", "manifest must be UTF-8 bytes")
    if not raw or len(raw) > MAX_MANIFEST_BYTES:
        raise ManifestError("size", "manifest is empty or exceeds its size limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ManifestError("encoding", "manifest is not valid UTF-8") from error
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_duplicate_rejecting_pairs,
            parse_constant=_reject_json_constant,
        )
    except ManifestError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ManifestError("json", "manifest is not valid JSON") from error
    normalized = _manifest_from_object(parsed)
    if raw != canonical_manifest_bytes(normalized):
        raise ManifestError("nonCanonical", "manifest bytes are not canonical")
    return normalized


def parse_manifest_file(path: Path, *, maximum: int = MAX_MANIFEST_BYTES) -> tuple[Manifest, bytes]:
    data = read_stable_file(path, maximum)
    return parse_manifest(data), data


def build_manifest(
    *,
    artifact_type: str,
    version: str,
    platform: str,
    architecture: str,
    channel: str,
    role: str,
    files: Mapping[str, bytes | bytearray | memoryview | FileRecord | Mapping[str, Any]],
    publisher: str = PUBLISHER_OFFICIAL,
    minimum_version: str | None = None,
    rollback: bool = False,
    rollback_of: str | None = None,
) -> Manifest:
    """Create a normalized manifest from exact payload bytes or records."""

    records: dict[str, FileRecord] = {}
    for raw_name, value in files.items():
        name = _safe_relative_path(raw_name)
        if isinstance(value, FileRecord):
            record = value
        elif isinstance(value, Mapping):
            if set(value) != {"sha256", "size"}:
                raise ManifestError("fileRecord", f"invalid record for {name}")
            record = FileRecord(str(value["sha256"]), value["size"])
        else:
            data = bytes(value)
            if len(data) > MAX_FILE_BYTES:
                raise ManifestError("fileSize", f"payload is too large: {name}")
            record = FileRecord(hashlib.sha256(data).hexdigest(), len(data))
        records[name] = record
    return _manifest_from_object(
        {
            "schemaVersion": SCHEMA_VERSION,
            "artifactType": artifact_type,
            "version": version,
            "platform": platform,
            "architecture": architecture,
            "channel": channel,
            "publisher": publisher,
            "role": role,
            "files": {name: records[name].as_dict() for name in sorted(records)},
            **({"minimumVersion": minimum_version} if minimum_version is not None else {}),
            **({"rollback": True} if rollback else {}),
            **({"rollbackOf": rollback_of} if rollback_of is not None else {}),
        }
    )


create_manifest = build_manifest


def version_tuple(version: str) -> tuple[int, int, int]:
    if _SEMVER.fullmatch(version) is None:
        raise ValueError(f"invalid numeric version: {version}")
    return tuple(int(part) for part in version.split("."))  # type: ignore[return-value]


def read_stable_file(path: Path, maximum: int = MAX_FILE_BYTES) -> bytes:
    """Read bounded regular bytes without following a source symlink."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise VerificationError("read", f"could not read verification input: {path}") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > maximum:
            raise VerificationError("unsafeInput", f"verification input is not a bounded regular file: {path}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65536, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise VerificationError("size", f"verification input exceeds its size limit: {path}")
        after = os.fstat(descriptor)
        identity = lambda value: (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )
        if identity(before) != identity(after) or total != before.st_size:
            raise VerificationError("changedInput", f"verification input changed while being read: {path}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _record_mapping(manifest: Manifest) -> dict[str, FileRecord]:
    return dict(manifest.files)


def verify_integrity(
    files: Mapping[str, bytes | bytearray | memoryview],
    manifest: Manifest,
    *,
    reject_extra: bool = True,
) -> IntegrityReport:
    """Verify exact payload names, sizes and SHA-256 digests."""

    actual = set(files)
    expected = set(manifest.files)
    if reject_extra and actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        detail = []
        if missing:
            detail.append("missing: " + ", ".join(missing[:4]))
        if extra:
            detail.append("unexpected: " + ", ".join(extra[:4]))
        return IntegrityReport(STATUS_INVALID, "memberSet", "payload member set differs (" + "; ".join(detail) + ")")
    checked = 0
    for name, record in manifest.files.items():
        if name not in files:
            return IntegrityReport(STATUS_INVALID, "missingFile", f"manifest payload is missing: {name}", checked)
        try:
            data = bytes(files[name])
        except (TypeError, ValueError):
            return IntegrityReport(STATUS_INVALID, "fileData", f"payload is not byte data: {name}", checked)
        if len(data) != record.size:
            return IntegrityReport(STATUS_INVALID, "sizeMismatch", f"payload size differs: {name}", checked)
        if hashlib.sha256(data).hexdigest() != record.sha256:
            return IntegrityReport(STATUS_INVALID, "hashMismatch", f"payload digest differs: {name}", checked)
        checked += 1
    return IntegrityReport(STATUS_VALID, "ok", "all manifest payload files match", checked)


def verify_directory_integrity(
    root: Path,
    manifest: Manifest,
    *,
    manifest_names: Iterable[str] = (MANIFEST_NAME, PACKAGE_MANIFEST_NAME),
) -> IntegrityReport:
    """Verify a directory payload without following links or accepting extras."""

    expected = set(manifest.files)
    files: dict[str, bytes] = {}
    try:
        _check_directory_root(root)
        for parent, directories, names in os.walk(root, topdown=True, followlinks=False):
            parent_path = Path(parent)
            for name in list(directories):
                candidate = parent_path / name
                metadata = candidate.lstat()
                if not stat.S_ISDIR(metadata.st_mode) or metadata.st_mode & 0o022:
                    return IntegrityReport(STATUS_INVALID, "unsafeDirectory", f"unsafe payload directory: {candidate}")
            for name in names:
                candidate = parent_path / name
                relative = str(candidate.relative_to(root))
                if relative in manifest_names:
                    continue
                metadata = candidate.lstat()
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                    return IntegrityReport(STATUS_INVALID, "unsafeFile", f"unsafe payload file: {relative}")
                if relative not in expected:
                    return IntegrityReport(STATUS_INVALID, "unexpectedFile", f"unexpected payload file: {relative}")
                files[relative] = read_stable_file(candidate, manifest.files[relative].size)
    except VerificationError as error:
        return IntegrityReport(STATUS_INVALID, error.code, str(error))
    except OSError as error:
        return IntegrityReport(STATUS_INVALID, "read", str(error))
    return verify_integrity(files, manifest)


def _check_directory_root(root: Path) -> None:
    """Reject a symlinked artifact root or any symlink in its ancestry."""

    root = Path(root)
    current = root
    while True:
        try:
            metadata = current.lstat()
        except OSError as error:
            raise VerificationError("unsafeDirectory", "artifact directory ancestry could not be inspected") from error
        if stat.S_ISLNK(metadata.st_mode):
            raise VerificationError("unsafeDirectory", "artifact directory root or ancestry must not be a symlink")
        if not stat.S_ISDIR(metadata.st_mode):
            raise VerificationError("unsafeDirectory", "artifact directory root or ancestry is not a directory")
        if current.parent == current:
            break
        current = current.parent


class TrustStore:
    """Role-scoped read-only public trust.

    The default store is the installed MOOS store.  Supplying paths is useful
    for isolated tests and offline tooling, but there is intentionally no
    method to add, replace, rotate or generate trust material.
    """

    def __init__(
        self,
        paths: Mapping[str, Path | str] | None = None,
        *,
        require_protected: bool | None = None,
        openssl: Path = DEFAULT_OPENSSL,
        expected_digests: Mapping[str, str] | None = None,
    ):
        self._paths = {
            role: Path(path)
            for role, path in (DEFAULT_TRUST_PATHS if paths is None else paths).items()
        }
        unknown = set(self._paths) - OFFICIAL_ROLES
        if unknown:
            raise TrustError("unknownRole", "trust store contains an unknown role")
        expected_digests = dict(expected_digests or {})
        if set(expected_digests) - OFFICIAL_ROLES:
            raise TrustError("unknownRole", "expected trust contains an unknown role")
        if any(not isinstance(value, str) or _HEX_64.fullmatch(value) is None for value in expected_digests.values()):
            raise TrustError("trustDigest", "expected trust digests must be lowercase SHA-256 values")
        self._require_protected = paths is None if require_protected is None else require_protected
        self.openssl = openssl
        self._expected_digests = expected_digests
        self._pinned_digests: dict[str, str] = {}

    @property
    def paths(self) -> Mapping[str, Path]:
        return dict(self._paths)

    def path_for_role(self, role: str) -> Path:
        if role not in OFFICIAL_ROLES:
            raise TrustError("unknownRole", f"no trust is defined for role: {role}")
        path = self._paths.get(role)
        if path is None:
            raise TrustError("trustMissing", f"public trust for role {role} is not configured")
        return path

    def _check_path(self, path: Path) -> None:
        try:
            metadata = path.lstat()
        except FileNotFoundError as error:
            raise TrustError("trustMissing", f"public trust for this role is missing: {path}") from error
        except OSError as error:
            raise TrustError("trustUnreadable", "public trust could not be inspected") from error
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_mode & 0o022
            or (self._require_protected and metadata.st_uid != 0)
        ):
            raise TrustError("trustUnsafe", "public trust has unsafe ownership, links or mode")
        if self._require_protected:
            for parent in path.parents:
                try:
                    parent_metadata = parent.lstat()
                except OSError as error:
                    raise TrustError("trustUnreadable", "public trust ancestry could not be inspected") from error
                if (
                    not stat.S_ISDIR(parent_metadata.st_mode)
                    or parent_metadata.st_mode & 0o022
                    or parent_metadata.st_uid != 0
                ):
                    raise TrustError("trustUnsafe", "public trust has unsafe parent directories")

    def public_key(self, role: str) -> bytes:
        path = self.path_for_role(role)
        self._check_path(path)
        data = read_stable_file(path, MAX_PUBLIC_KEY_BYTES)
        if b"PRIVATE KEY" in data or not re.search(
            rb"-----BEGIN (?:PUBLIC KEY|RSA PUBLIC KEY|EC PUBLIC KEY)-----", data
        ):
            raise TrustError("trustPrivateOrInvalid", "trust material must be a public PEM key")
        digest = hashlib.sha256(data).hexdigest()
        expected = self._expected_digests.get(role)
        if expected is not None and digest != expected:
            raise TrustError("trustMismatch", "public trust does not match its expected digest")
        pinned = self._pinned_digests.get(role)
        if pinned is not None and digest != pinned:
            raise TrustError("trustChanged", "public trust changed after its first approved read")
        if pinned is None:
            self._pinned_digests[role] = digest
        return data

    def fingerprint(self, role: str) -> str:
        key = self.public_key(role)
        try:
            result = subprocess.run(
                [str(self.openssl), "pkey", "-pubin", "-outform", "DER"],
                input=key,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"},
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise TrustError("trustInvalid", "public trust could not be parsed") from error
        if result.returncode != 0 or not result.stdout:
            raise TrustError("trustInvalid", "public trust could not be parsed")
        return hashlib.sha256(result.stdout).hexdigest()

    def verify(self, role: str, message: bytes, signature: bytes) -> SignatureReport:
        try:
            key = self.public_key(role)
        except TrustError as error:
            return SignatureReport(STATUS_INVALID, error.code, error.message)
        return verify_detached_signature_bytes(
            message, signature, key, openssl=self.openssl
        )


def verify_detached_signature_bytes(
    message: bytes,
    signature: bytes,
    public_key: bytes,
    *,
    openssl: Path = DEFAULT_OPENSSL,
) -> SignatureReport:
    """Verify a SHA-256 detached signature using public material only."""

    if len(message) > MAX_ARCHIVE_BYTES:
        return SignatureReport(STATUS_INVALID, "messageTooLarge", "signed bytes exceed their size limit")
    if not signature or len(signature) > MAX_SIGNATURE_BYTES:
        return SignatureReport(STATUS_INVALID, "signatureInput", "signature is empty or exceeds its size limit")
    if len(public_key) > MAX_PUBLIC_KEY_BYTES or b"PRIVATE KEY" in public_key:
        return SignatureReport(STATUS_INVALID, "privateKeyRejected", "private signing material is not accepted")
    if not re.search(rb"-----BEGIN (?:PUBLIC KEY|RSA PUBLIC KEY|EC PUBLIC KEY)-----", public_key):
        return SignatureReport(STATUS_INVALID, "publicKeyInput", "verification requires a public PEM key")
    try:
        with tempfile.TemporaryDirectory(prefix="moos-verify-") as name:
            root = Path(name)
            os.chmod(root, 0o700)
            message_path = root / "message"
            signature_path = root / "signature"
            key_path = root / "public.pem"
            message_path.write_bytes(message)
            signature_path.write_bytes(signature)
            key_path.write_bytes(public_key)
            for path in (message_path, signature_path, key_path):
                os.chmod(path, 0o600)
            result = subprocess.run(
                [
                    str(openssl),
                    "dgst",
                    "-sha256",
                    "-verify",
                    str(key_path),
                    "-signature",
                    str(signature_path),
                    str(message_path),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"},
                timeout=15,
                check=False,
            )
    except (OSError, subprocess.SubprocessError):
        return SignatureReport(STATUS_INVALID, "signatureTool", "signature verification could not be completed")
    if result.returncode == 0:
        return SignatureReport(STATUS_VALID, "ok", "detached signature is valid")
    return SignatureReport(STATUS_INVALID, "signatureMismatch", "detached signature is invalid")


def verify_detached_signature_files(
    message_path: Path,
    signature_path: Path,
    public_key_path: Path,
    *,
    openssl: Path = DEFAULT_OPENSSL,
) -> SignatureReport:
    try:
        message = read_stable_file(message_path, MAX_ARCHIVE_BYTES)
        signature = read_stable_file(signature_path, MAX_SIGNATURE_BYTES)
        public_key = read_stable_file(public_key_path, MAX_PUBLIC_KEY_BYTES)
    except VerificationError as error:
        return SignatureReport(STATUS_INVALID, error.code, error.message)
    return verify_detached_signature_bytes(message, signature, public_key, openssl=openssl)


def evaluate_policy(manifest: Manifest, policy: VerificationPolicy | None = None) -> PolicyReport:
    policy = policy or VerificationPolicy()
    issues: list[VerificationIssue] = []

    def expected(field: str, actual: str, value: str | None) -> None:
        if value is not None and actual != value:
            issues.append(_issue("wrong" + field[0].upper() + field[1:], f"manifest {field} does not match policy", "policy"))

    expected("role", manifest.role, policy.expected_role)
    expected("artifactType", manifest.artifact_type, policy.expected_artifact_type)
    expected("platform", manifest.platform, policy.expected_platform)
    expected("architecture", manifest.architecture, policy.expected_architecture)
    expected("channel", manifest.channel, policy.expected_channel)
    if policy.expected_version is not None and manifest.version != policy.expected_version:
        issues.append(_issue("wrongVersion", "manifest version does not match policy", "policy"))
    for value, field in (
        (policy.expected_version, "expectedVersion"),
        (policy.current_version, "currentVersion"),
        (policy.minimum_version, "minimumVersion"),
    ):
        if value is not None and _SEMVER.fullmatch(value) is None:
            issues.append(_issue("invalidPolicy", f"policy {field} is not a canonical version", "policy"))

    rollback = "YES" if manifest.rollback else "NO"
    if ARTIFACT_ROLE_MAP.get(manifest.artifact_type) != manifest.role:
        issues.append(_issue("roleMapping", "artifact type is not authorized for its role", "policy"))
    try:
        artifact_version = version_tuple(manifest.version)
        if policy.minimum_version is not None and artifact_version < version_tuple(policy.minimum_version):
            issues.append(_issue("belowMinimumVersion", "artifact is below the policy minimum version", "policy"))
        if manifest.minimum_version is not None and policy.current_version is not None:
            if version_tuple(policy.current_version) < version_tuple(manifest.minimum_version):
                issues.append(_issue("minimumVersion", "current installation is below the artifact minimum version", "policy"))
        if manifest.rollback:
            if policy.current_version is None:
                issues.append(_issue("rollbackCurrentVersionRequired", "rollback requires the current version", "policy"))
            elif manifest.rollback_of != policy.current_version:
                issues.append(_issue("rollbackTargetMismatch", "rollbackOf must equal the current version", "policy"))
            elif not policy.allow_rollback:
                issues.append(_issue("rollbackNotAuthorized", "rollback requires explicit policy authorization", "policy"))
        if policy.current_version is not None and artifact_version < version_tuple(policy.current_version):
            if not manifest.rollback:
                issues.append(_issue("rollbackRejected", "older artifact is not marked for rollback", "policy"))
    except ValueError:
        issues.append(_issue("invalidVersion", "manifest version is not comparable", "policy"))
    return PolicyReport("ACCEPT" if not issues else "REJECT", rollback, tuple(issues))


def result_from_evidence(
    *,
    manifest: Manifest | None,
    manifest_status: str,
    signature: SignatureReport | _VerifiedSignature | None = None,
    integrity: IntegrityReport | None = None,
    policy: PolicyReport | None = None,
    provenance: str = PROVENANCE_NONE,
    issues: Iterable[VerificationIssue] = (),
) -> VerificationResult:
    trusted_signature = _trusted_signature_report(signature)
    signature_status = trusted_signature.status if trusted_signature is not None else STATUS_NOT_CHECKED
    integrity_status = integrity.status if integrity is not None else STATUS_NOT_CHECKED
    policy_status = policy.status if policy is not None else "NOT_CHECKED"
    all_issues = list(issues)
    if trusted_signature is not None and trusted_signature.status != STATUS_VALID:
        all_issues.append(_issue(trusted_signature.code, trusted_signature.message, "signature"))
    if integrity is not None and integrity.status != STATUS_VALID:
        all_issues.append(_issue(integrity.code, integrity.message, "integrity"))
    if policy is not None:
        all_issues.extend(policy.issues)
    accepted = (
        manifest is not None
        and manifest_status == STATUS_VALID
        and signature_status == STATUS_VALID
        and integrity_status == STATUS_VALID
        and policy_status == "ACCEPT"
    )
    if accepted:
        final = RESULT_VERIFIED
    elif (
        manifest is not None
        and manifest_status == STATUS_VALID
        and integrity_status == STATUS_VALID
        and provenance == PROVENANCE_PACKAGE_MEMBERSHIP
        and signature_status == STATUS_NOT_PRESENT
        and policy_status == "ACCEPT"
    ):
        final = RESULT_INTEGRITY_ONLY
    else:
        final = RESULT_REJECTED
    return VerificationResult(
        signature=signature_status,
        integrity=integrity_status,
        manifest=manifest_status,
        publisher=manifest.publisher if manifest else None,
        role=manifest.role if manifest else None,
        channel=manifest.channel if manifest else None,
        version=manifest.version if manifest else None,
        artifact_type=manifest.artifact_type if manifest else None,
        platform=manifest.platform if manifest else None,
        architecture=manifest.architecture if manifest else None,
        rollback=policy.rollback if policy is not None else ("YES" if manifest and manifest.rollback else "UNKNOWN"),
        policy=policy_status,
        provenance=provenance,
        result=final,
        issues=tuple(all_issues),
    )


def verify_manifest_payload(
    manifest_bytes: bytes,
    files: Mapping[str, bytes | bytearray | memoryview],
    *,
    signature: SignatureReport | None = None,
    policy: VerificationPolicy | None = None,
    provenance: str = PROVENANCE_NONE,
) -> VerificationResult:
    """Verify public manifest/payload inputs without accepting forged evidence."""

    return _verify_manifest_payload(
        manifest_bytes,
        files,
        signature=_external_signature_report(signature),
        policy=policy,
        provenance=provenance,
    )


def _verify_manifest_payload(
    manifest_bytes: bytes,
    files: Mapping[str, bytes | bytearray | memoryview],
    *,
    signature: SignatureReport | _VerifiedSignature | None = None,
    policy: VerificationPolicy | None = None,
    provenance: str = PROVENANCE_NONE,
) -> VerificationResult:
    """Verify canonical manifest, payload integrity and policy."""

    try:
        manifest = parse_manifest(manifest_bytes)
    except ManifestError as error:
        return result_from_evidence(
            manifest=None,
            manifest_status=STATUS_INVALID,
            signature=signature,
            provenance=provenance,
            issues=(_issue(error.code, error.message, "manifest"),),
        )
    integrity = verify_integrity(files, manifest)
    policy_report = evaluate_policy(manifest, policy)
    return result_from_evidence(
        manifest=manifest,
        manifest_status=STATUS_VALID,
        signature=signature,
        integrity=integrity,
        policy=policy_report,
        provenance=provenance,
    )


def _archive_member_bytes(
    source: Path | bytes | bytearray | memoryview,
    *,
    include_manifest: bool = True,
) -> dict[str, bytes]:
    if isinstance(source, (bytes, bytearray, memoryview)):
        archive_bytes = bytes(source)
        source_name = "staged archive"
    else:
        archive_bytes = read_stable_file(Path(source), MAX_ARCHIVE_BYTES)
        source_name = str(source)
    if len(archive_bytes) > MAX_ARCHIVE_BYTES:
        raise VerificationError("archiveSize", "archive exceeds its size limit")
    files: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
            member_count = 0
            for member in archive.getmembers():
                member_count += 1
                if member_count > MAX_ARCHIVE_MEMBERS:
                    raise VerificationError("archiveMembers", "archive contains too many members")
                if not member.isfile() or not member.name or not _SAFE_PATH.fullmatch(member.name):
                    raise VerificationError("unsafeArchive", f"archive contains an unsafe member: {member.name}")
                if member.name.startswith("/") or "\\" in member.name or any(
                    part in {"", ".", ".."} for part in member.name.split("/")
                ):
                    raise VerificationError("unsafeArchive", f"archive contains an unsafe member: {member.name}")
                if member.name in files:
                    raise VerificationError("duplicateMember", f"archive contains duplicate member: {member.name}")
                if member.size < 0 or member.size > MAX_ARCHIVE_MEMBER_BYTES:
                    raise VerificationError("memberSize", f"archive member exceeds its size limit: {member.name}")
                stream = archive.extractfile(member)
                if stream is None:
                    raise VerificationError("memberRead", f"archive member could not be read: {member.name}")
                data = stream.read(MAX_ARCHIVE_MEMBER_BYTES + 1)
                if len(data) != member.size or len(data) > MAX_ARCHIVE_MEMBER_BYTES:
                    raise VerificationError("memberChanged", f"archive member size changed: {member.name}")
                if include_manifest or member.name not in {MANIFEST_NAME, PACKAGE_MANIFEST_NAME}:
                    files[member.name] = data
    except VerificationError:
        raise
    except (OSError, tarfile.TarError) as error:
        raise VerificationError("archiveRead", f"archive could not be safely parsed: {source_name}") from error
    return files


def _peek_archive_manifest_bytes(archive_bytes: bytes) -> tuple[bytes, str]:
    members = _archive_member_bytes(archive_bytes)
    for name in (MANIFEST_NAME, PACKAGE_MANIFEST_NAME):
        if name in members:
            return members[name], name
    raise VerificationError("manifestMissing", "official archive has no moos-manifest.json member")


def peek_archive_manifest(path: Path) -> tuple[bytes, str]:
    """Read only the bounded manifest member from an archive.

    This helper performs metadata-only parsing and never extracts or executes
    archive content.  Privileged admin installation uses an expected role and
    authenticates the opaque tar bytes before calling the full archive path.
    """

    return _peek_archive_manifest_bytes(read_stable_file(path, MAX_ARCHIVE_BYTES))


def verify_archive_manifest(
    archive_path: Path,
    *,
    signature: SignatureReport | None = None,
    policy: VerificationPolicy | None = None,
    provenance: str = PROVENANCE_NONE,
    require_manifest_name: str = MANIFEST_NAME,
) -> VerificationResult:
    """Verify a manifest and exact member bytes in an already trusted archive."""

    signature = _external_signature_report(signature)
    try:
        archive_bytes = read_stable_file(archive_path, MAX_ARCHIVE_BYTES)
        return _verify_archive_manifest_bytes(
            archive_bytes,
            signature=signature,
            policy=policy,
            provenance=provenance,
            require_manifest_name=require_manifest_name,
        )
    except VerificationError as error:
        return result_from_evidence(
            manifest=None,
            manifest_status=STATUS_INVALID,
            signature=signature,
            provenance=provenance,
            issues=(_issue(error.code, error.message, "manifest"),),
        )


def _verify_archive_manifest_bytes(
    archive_bytes: bytes,
    *,
    signature: SignatureReport | _VerifiedSignature | None = None,
    policy: VerificationPolicy | None = None,
    provenance: str = PROVENANCE_NONE,
    require_manifest_name: str = MANIFEST_NAME,
) -> VerificationResult:
    members = _archive_member_bytes(archive_bytes)
    if require_manifest_name not in members:
        raise VerificationError("manifestMissing", f"archive has no {require_manifest_name} member")
    manifest_bytes = members.pop(require_manifest_name)
    # A second manifest name would make the artifact ambiguous.
    other = MANIFEST_NAME if require_manifest_name == PACKAGE_MANIFEST_NAME else PACKAGE_MANIFEST_NAME
    if other in members:
        raise VerificationError("manifestAmbiguous", "archive contains more than one manifest member")
    return _verify_manifest_payload(
        manifest_bytes,
        members,
        signature=signature,
        policy=policy,
        provenance=provenance,
    )


def verify_signed_archive(
    archive_path: Path,
    signature_path: Path,
    *,
    trust: TrustStore | None = None,
    expected_role: str | None = None,
    policy: VerificationPolicy | None = None,
    openssl: Path = DEFAULT_OPENSSL,
) -> VerificationResult:
    """Verify a detached-signed archive, then its manifest and payload."""

    trust = trust or TrustStore(openssl=openssl)
    manifest_bytes: bytes | None = None
    manifest: Manifest | None = None
    role = expected_role or (policy.expected_role if policy is not None else None)
    try:
        archive = read_stable_file(archive_path, MAX_ARCHIVE_BYTES)
        detached = read_stable_file(signature_path, MAX_SIGNATURE_BYTES)
    except VerificationError as error:
        return result_from_evidence(
            manifest=None,
            manifest_status=STATUS_NOT_CHECKED,
            signature=SignatureReport(STATUS_INVALID, error.code, error.message),
            provenance=PROVENANCE_SIGNATURE,
        )
    if role is None:
        try:
            manifest_bytes, _ = _peek_archive_manifest_bytes(archive)
            manifest = parse_manifest(manifest_bytes)
            role = manifest.role
        except VerificationError as error:
            return result_from_evidence(
                manifest=None,
                manifest_status=STATUS_INVALID,
                signature=SignatureReport(STATUS_NOT_CHECKED, "notChecked", "signature was not attempted"),
                provenance=PROVENANCE_SIGNATURE,
                issues=(_issue(error.code, error.message, "manifest"),),
            )
    if role not in OFFICIAL_ROLES:
        return result_from_evidence(
            manifest=manifest,
            manifest_status=STATUS_VALID if manifest else STATUS_NOT_CHECKED,
            signature=SignatureReport(STATUS_INVALID, "unknownRole", "artifact role is not authorized"),
            provenance=PROVENANCE_SIGNATURE,
        )
    try:
        key = trust.public_key(role)
        signature_report = verify_detached_signature_bytes(archive, detached, key, openssl=openssl)
    except VerificationError as error:
        signature_report = SignatureReport(STATUS_INVALID, error.code, error.message)
    # Do not inspect the manifest or archive members after an invalid
    # signature when the caller provided the expected role.  The admin helper
    # relies on this ordering at its privilege boundary.
    if not signature_report.valid:
        if expected_role is not None or (policy is not None and policy.expected_role is not None):
            return result_from_evidence(
                manifest=None,
                manifest_status=STATUS_NOT_CHECKED,
                signature=signature_report,
                provenance=PROVENANCE_SIGNATURE,
            )
        return _verify_archive_manifest_bytes(
            archive,
            signature=signature_report,
            policy=policy,
            provenance=PROVENANCE_SIGNATURE,
            require_manifest_name=MANIFEST_NAME,
        )
    role_policy = policy
    if expected_role is not None:
        base = policy or VerificationPolicy()
        role_policy = VerificationPolicy(
            expected_role=expected_role,
            expected_artifact_type=base.expected_artifact_type,
            expected_platform=base.expected_platform,
            expected_architecture=base.expected_architecture,
            expected_channel=base.expected_channel,
            expected_version=base.expected_version,
            current_version=base.current_version,
            minimum_version=base.minimum_version,
            allow_rollback=base.allow_rollback,
        )
    try:
        return _verify_archive_manifest_bytes(
            archive,
            signature=_verified_signature(signature_report),
            policy=role_policy,
            provenance=PROVENANCE_SIGNATURE,
            require_manifest_name=MANIFEST_NAME,
        )
    except VerificationError as error:
        return result_from_evidence(
            manifest=None,
            manifest_status=STATUS_INVALID,
            signature=_verified_signature(signature_report),
            provenance=PROVENANCE_SIGNATURE,
            issues=(_issue(error.code, error.message, "manifest"),),
        )


def verify_legacy_admin_fixture(
    archive_path: Path,
    *,
    signature: SignatureReport | None = None,
) -> VerificationResult:
    """Explicit regression-only result for the pre-manifest admin fixture.

    This path intentionally never returns ``VERIFIED``.  It exists so tests
    can preserve historical detached-signature and allowlist coverage while
    the normal installer requires the canonical manifest member.
    """

    return result_from_evidence(
        manifest=None,
        manifest_status="LEGACY",
        signature=signature,
        provenance=PROVENANCE_LEGACY_FIXTURE,
        issues=(
            _issue(
                "legacyManifestRequired",
                "legacy administrator fixture has no canonical manifest; it is regression evidence only",
                "manifest",
            ),
        ),
    )


def verify_directory_artifact(
    root: Path,
    *,
    signature_path: Path | None = None,
    trust: TrustStore | None = None,
    policy: VerificationPolicy | None = None,
    manifest_name: str = MANIFEST_NAME,
    provenance: str = PROVENANCE_SIGNATURE,
) -> VerificationResult:
    """Verify a directory artifact whose detached signature covers its manifest."""

    try:
        _check_directory_root(root)
        manifest_path = root / manifest_name
        manifest_bytes = read_stable_file(manifest_path, MAX_MANIFEST_BYTES)
        manifest = parse_manifest(manifest_bytes)
    except VerificationError as error:
        return result_from_evidence(
            manifest=None,
            manifest_status=STATUS_INVALID,
            provenance=provenance,
            issues=(_issue(error.code, error.message, "manifest"),),
        )
    signature_report: SignatureReport | None = None
    if signature_path is not None:
        trust = trust or TrustStore()
        try:
            key = trust.public_key(manifest.role)
            signature = read_stable_file(signature_path, MAX_SIGNATURE_BYTES)
            signature_report = verify_detached_signature_bytes(manifest_bytes, signature, key)
        except VerificationError as error:
            signature_report = SignatureReport(STATUS_INVALID, error.code, error.message)
    else:
        signature_report = SignatureReport(STATUS_NOT_PRESENT, "signatureMissing", "no detached signature was supplied")
    if signature_report.status != STATUS_VALID and provenance == PROVENANCE_SIGNATURE:
        return result_from_evidence(
            manifest=manifest,
            manifest_status=STATUS_VALID,
            signature=signature_report,
            provenance=provenance,
        )
    integrity = verify_directory_integrity(root, manifest, manifest_names=(manifest_name,))
    policy_report = evaluate_policy(manifest, policy)
    result_signature: SignatureReport | _VerifiedSignature = (
        _verified_signature(signature_report)
        if signature_report.status == STATUS_VALID
        else signature_report
    )
    return result_from_evidence(
        manifest=manifest,
        manifest_status=STATUS_VALID,
        signature=result_signature,
        integrity=integrity,
        policy=policy_report,
        provenance=provenance,
    )


def verify_artifact(
    artifact: Path,
    *,
    signature_path: Path | None = None,
    trust: TrustStore | None = None,
    policy: VerificationPolicy | None = None,
) -> VerificationResult:
    """Verify one local archive or directory without changing trust."""

    artifact = Path(artifact)
    if artifact.is_dir():
        signature = signature_path
        if signature is None:
            candidate = artifact / (MANIFEST_NAME + ".sig")
            if candidate.is_file():
                signature = candidate
        return verify_directory_artifact(
            artifact,
            signature_path=signature,
            trust=trust,
            policy=policy,
        )
    if artifact.is_file() and (artifact.suffix == ".tar" or artifact.name.endswith(".tar")):
        signature = signature_path or Path(str(artifact) + ".sig")
        if not signature.exists():
            return result_from_evidence(
                manifest=None,
                manifest_status=STATUS_NOT_CHECKED,
                signature=SignatureReport(STATUS_NOT_PRESENT, "signatureMissing", "detached signature is missing"),
                provenance=PROVENANCE_SIGNATURE,
            )
        return verify_signed_archive(artifact, signature, trust=trust, policy=policy)
    return result_from_evidence(
        manifest=None,
        manifest_status=STATUS_NOT_CHECKED,
        provenance=PROVENANCE_NONE,
        issues=(_issue("unsupportedArtifact", "artifact must be a MOOS archive or artifact directory", "input"),),
    )


def package_membership_result(
    manifest_bytes: bytes,
    files: Mapping[str, bytes | bytearray | memoryview],
    *,
    policy: VerificationPolicy | None = None,
) -> VerificationResult:
    """Verify package contents while making external channel provenance explicit."""

    return verify_manifest_payload(
        manifest_bytes,
        files,
        signature=SignatureReport(STATUS_NOT_PRESENT, "externalPackage", "package authentication is supplied by the external OS channel"),
        policy=policy,
        provenance=PROVENANCE_PACKAGE_MEMBERSHIP,
    )


__all__ = [
    "ARTIFACT_ADMIN_RELEASE",
    "ARTIFACT_HOST_PACKAGE",
    "ARTIFACT_PERSONAL_RUNTIME",
    "ARTIFACT_ROLE_MAP",
    "DEFAULT_TRUST_PATHS",
    "FileRecord",
    "IntegrityReport",
    "MANIFEST_NAME",
    "Manifest",
    "ManifestError",
    "OFFICIAL_ROLES",
    "PACKAGE_MANIFEST_NAME",
    "PolicyReport",
    "PROVENANCE_LEGACY_FIXTURE",
    "PROVENANCE_PACKAGE_MEMBERSHIP",
    "PROVENANCE_SIGNATURE",
    "RESULT_INTEGRITY_ONLY",
    "RESULT_REJECTED",
    "RESULT_VERIFIED",
    "ROLE_ADMIN_RELEASE",
    "ROLE_HOST_RELEASE",
    "ROLE_PERSONAL_RUNTIME",
    "SCHEMA_VERSION",
    "SignatureReport",
    "TrustError",
    "TrustStore",
    "VerificationError",
    "VerificationIssue",
    "VerificationPolicy",
    "VerificationResult",
    "build_manifest",
    "canonical_manifest_bytes",
    "create_manifest",
    "evaluate_policy",
    "package_membership_result",
    "parse_manifest",
    "parse_manifest_file",
    "peek_archive_manifest",
    "read_stable_file",
    "result_from_evidence",
    "serialize_manifest",
    "verify_archive_manifest",
    "verify_artifact",
    "verify_detached_signature_bytes",
    "verify_detached_signature_files",
    "verify_directory_artifact",
    "verify_directory_integrity",
    "verify_integrity",
    "verify_legacy_admin_fixture",
    "verify_manifest_payload",
    "verify_signed_archive",
    "version_tuple",
]
