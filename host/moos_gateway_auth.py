"""MOOS Gateway device credentials and versioned challenge authentication."""

from __future__ import annotations

import base64
import binascii
import fcntl
import hashlib
import hmac
import json
import os
import secrets
import stat
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


GATEWAY_VERSION = 1
DEVICE_STORE_SCHEMA_VERSION = 1
NONCE_BYTES = 32
DEVICE_KEY_BYTES = 32
MAX_DEVICE_STORE_BYTES = 1024 * 1024
REMOTE_OPERATIONS = frozenset({"status"})
AUTH_CONTEXT = b"MOOS-GATEWAY-AUTH-V1\n"
SERVER_AUTH_CONTEXT = b"MOOS-GATEWAY-SERVER-V1\n"
PAIRING_PREFIX = "moos-pair-v1"


class GatewayAuthError(ValueError):
    """A deliberately generic authentication failure."""


@dataclass(frozen=True)
class AuthorizedDevice:
    device_id: str
    name: str
    key: bytes
    permissions: frozenset[str]
    revoked: bool
    created_at: str
    updated_at: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def encode_base64url(value: bytes) -> str:
    if not isinstance(value, bytes):
        raise TypeError("base64url input must be bytes")
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def decode_base64url(value: object, *, expected_bytes: int) -> bytes:
    if not isinstance(value, str) or not value or "=" in value:
        raise GatewayAuthError("authentication failed")
    try:
        padding = "=" * ((4 - len(value) % 4) % 4)
        decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (ValueError, UnicodeEncodeError, binascii.Error):
        raise GatewayAuthError("authentication failed") from None
    if len(decoded) != expected_bytes or encode_base64url(decoded) != value:
        raise GatewayAuthError("authentication failed")
    return decoded


def validate_device_id(value: object) -> str:
    if not isinstance(value, str):
        raise GatewayAuthError("authentication failed")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        raise GatewayAuthError("authentication failed") from None
    if str(parsed) != value:
        raise GatewayAuthError("authentication failed")
    return value


def validate_device_name(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("device name must be text")
    name = value.strip()
    if not name or len(name) > 80 or not all(character.isprintable() for character in name):
        raise ValueError("device name must contain 1 to 80 printable characters")
    return name


def validate_permissions(value: object) -> frozenset[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise ValueError("permissions must be a collection")
    if not value or not all(isinstance(item, str) for item in value):
        raise ValueError("at least one text permission is required")
    permissions = frozenset(value)
    if not permissions <= REMOTE_OPERATIONS:
        raise ValueError("unsupported remote permission")
    return permissions


def make_challenge() -> tuple[dict[str, Any], bytes]:
    nonce = secrets.token_bytes(NONCE_BYTES)
    return (
        {
            "gatewayVersion": GATEWAY_VERSION,
            "type": "challenge",
            "nonce": encode_base64url(nonce),
        },
        nonce,
    )


def make_authentication_proof(
    key: bytes,
    server_nonce: bytes,
    client_nonce: bytes,
    device_id: str,
) -> bytes:
    if len(key) != DEVICE_KEY_BYTES:
        raise ValueError("invalid device key length")
    validate_device_id(device_id)
    if len(server_nonce) != NONCE_BYTES or len(client_nonce) != NONCE_BYTES:
        raise ValueError("invalid authentication nonce length")
    payload = b"".join(
        (
            AUTH_CONTEXT,
            encode_base64url(server_nonce).encode("ascii"),
            b"\n",
            encode_base64url(client_nonce).encode("ascii"),
            b"\n",
            device_id.encode("ascii"),
            b"\n",
        )
    )
    return hmac.new(key, payload, hashlib.sha256).digest()


def make_server_authentication_proof(
    key: bytes,
    server_nonce: bytes,
    client_nonce: bytes,
    device_id: str,
) -> bytes:
    if len(key) != DEVICE_KEY_BYTES:
        raise ValueError("invalid device key length")
    validate_device_id(device_id)
    if len(server_nonce) != NONCE_BYTES or len(client_nonce) != NONCE_BYTES:
        raise ValueError("invalid authentication nonce length")
    payload = b"".join(
        (
            SERVER_AUTH_CONTEXT,
            encode_base64url(server_nonce).encode("ascii"),
            b"\n",
            encode_base64url(client_nonce).encode("ascii"),
            b"\n",
            device_id.encode("ascii"),
            b"\n",
        )
    )
    return hmac.new(key, payload, hashlib.sha256).digest()


def make_authenticate_frame(
    device_id: str,
    key: bytes,
    server_nonce: bytes,
    *,
    client_nonce: bytes | None = None,
) -> dict[str, Any]:
    client_nonce = client_nonce or secrets.token_bytes(NONCE_BYTES)
    proof = make_authentication_proof(key, server_nonce, client_nonce, device_id)
    return {
        "gatewayVersion": GATEWAY_VERSION,
        "type": "authenticate",
        "deviceId": device_id,
        "clientNonce": encode_base64url(client_nonce),
        "proof": encode_base64url(proof),
    }


def parse_challenge(frame: object) -> bytes:
    if not isinstance(frame, dict) or set(frame) != {
        "gatewayVersion",
        "type",
        "nonce",
    }:
        raise GatewayAuthError("authentication failed")
    if type(frame.get("gatewayVersion")) is not int or frame["gatewayVersion"] != 1:
        raise GatewayAuthError("authentication failed")
    if frame.get("type") != "challenge":
        raise GatewayAuthError("authentication failed")
    return decode_base64url(frame.get("nonce"), expected_bytes=NONCE_BYTES)


def verify_authenticate_frame(
    frame: object,
    server_nonce: bytes,
    devices: dict[str, AuthorizedDevice],
) -> tuple[AuthorizedDevice, bytes]:
    if not isinstance(frame, dict) or set(frame) != {
        "gatewayVersion",
        "type",
        "deviceId",
        "clientNonce",
        "proof",
    }:
        raise GatewayAuthError("authentication failed")
    if type(frame.get("gatewayVersion")) is not int or frame["gatewayVersion"] != 1:
        raise GatewayAuthError("authentication failed")
    if frame.get("type") != "authenticate":
        raise GatewayAuthError("authentication failed")

    device_id = validate_device_id(frame.get("deviceId"))
    client_nonce = decode_base64url(
        frame.get("clientNonce"), expected_bytes=NONCE_BYTES
    )
    supplied_proof = decode_base64url(
        frame.get("proof"), expected_bytes=hashlib.sha256().digest_size
    )
    device = devices.get(device_id)
    verification_key = device.key if device is not None else bytes(DEVICE_KEY_BYTES)
    expected_proof = make_authentication_proof(
        verification_key, server_nonce, client_nonce, device_id
    )
    proof_matches = hmac.compare_digest(supplied_proof, expected_proof)
    if device is None or device.revoked or not proof_matches:
        raise GatewayAuthError("authentication failed")
    return device, client_nonce


def make_authenticated_frame(
    device: AuthorizedDevice,
    server_nonce: bytes,
    client_nonce: bytes,
) -> dict[str, Any]:
    return {
        "gatewayVersion": GATEWAY_VERSION,
        "type": "authenticated",
        "deviceId": device.device_id,
        "permissions": sorted(device.permissions),
        "serverProof": encode_base64url(
            make_server_authentication_proof(
                device.key, server_nonce, client_nonce, device.device_id
            )
        ),
    }


def make_authentication_error() -> dict[str, Any]:
    return {
        "gatewayVersion": GATEWAY_VERSION,
        "type": "error",
        "code": "authentication_failed",
        "message": "Device authentication failed",
    }


def pairing_code(device: AuthorizedDevice) -> str:
    return f"{PAIRING_PREFIX}:{device.device_id}:{encode_base64url(device.key)}"


def parse_pairing_code(value: str) -> tuple[str, bytes]:
    if not isinstance(value, str):
        raise ValueError("pairing code must be text")
    parts = value.strip().split(":")
    if len(parts) != 3 or parts[0] != PAIRING_PREFIX:
        raise ValueError("invalid pairing code")
    try:
        device_id = validate_device_id(parts[1])
        key = decode_base64url(parts[2], expected_bytes=DEVICE_KEY_BYTES)
    except GatewayAuthError:
        raise ValueError("invalid pairing code") from None
    return device_id, key


class DeviceStore:
    """Strict read-only view of the root-managed authorized-device store."""

    def __init__(
        self,
        path: Path,
        *,
        expected_uid: int | None = None,
        expected_gid: int | None = None,
    ) -> None:
        self.path = path
        self.expected_uid = expected_uid
        self.expected_gid = expected_gid

    def load(self) -> dict[str, AuthorizedDevice]:
        self._validate_parent()
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags)
        except FileNotFoundError:
            return {}
        except OSError as error:
            raise ValueError("device store cannot be opened safely") from error
        try:
            status = os.fstat(descriptor)
            if not stat.S_ISREG(status.st_mode):
                raise ValueError("device store must be a regular file")
            if status.st_nlink != 1:
                raise ValueError("device store must not have hard links")
            if stat.S_IMODE(status.st_mode) != 0o640:
                raise ValueError("device store must have mode 0640")
            self._validate_owner(status, "device store")
            if status.st_size > MAX_DEVICE_STORE_BYTES:
                raise ValueError("device store is too large")
            with os.fdopen(descriptor, "r", encoding="utf-8") as input_file:
                descriptor = -1
                value = json.load(input_file)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if not isinstance(value, dict) or set(value) != {"schemaVersion", "devices"}:
            raise ValueError("invalid device store")
        if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
            raise ValueError("unsupported device store schema")
        if not isinstance(value["devices"], list):
            raise ValueError("invalid device list")

        devices: dict[str, AuthorizedDevice] = {}
        for item in value["devices"]:
            device = self._parse_device(item)
            if device.device_id in devices:
                raise ValueError("duplicate device ID")
            devices[device.device_id] = device
        return devices

    def _validate_parent(self) -> None:
        if self.expected_uid is None and self.expected_gid is None:
            return
        try:
            status = self.path.parent.lstat()
        except OSError as error:
            raise ValueError("device store directory is unavailable") from error
        if not stat.S_ISDIR(status.st_mode) or self.path.parent.is_symlink():
            raise ValueError("device store directory must be a regular directory")
        if stat.S_IMODE(status.st_mode) != 0o750:
            raise ValueError("device store directory must have mode 0750")
        self._validate_owner(status, "device store directory")

    def _validate_owner(self, status: os.stat_result, label: str) -> None:
        if self.expected_uid is not None and status.st_uid != self.expected_uid:
            raise ValueError(f"{label} has an unexpected owner")
        if self.expected_gid is not None and status.st_gid != self.expected_gid:
            raise ValueError(f"{label} has an unexpected group")

    @staticmethod
    def _parse_device(item: object) -> AuthorizedDevice:
        required = {
            "id",
            "name",
            "key",
            "permissions",
            "revoked",
            "createdAt",
            "updatedAt",
        }
        if not isinstance(item, dict) or set(item) != required:
            raise ValueError("invalid device entry")
        try:
            device_id = validate_device_id(item["id"])
        except GatewayAuthError:
            raise ValueError("invalid device ID") from None
        key = decode_base64url(item["key"], expected_bytes=DEVICE_KEY_BYTES)
        if type(item["revoked"]) is not bool:
            raise ValueError("invalid revocation flag")
        for timestamp_field in ("createdAt", "updatedAt"):
            if not isinstance(item[timestamp_field], str) or not item[timestamp_field]:
                raise ValueError("invalid device timestamp")
        return AuthorizedDevice(
            device_id=device_id,
            name=validate_device_name(item["name"]),
            key=key,
            permissions=validate_permissions(item["permissions"]),
            revoked=item["revoked"],
            created_at=item["createdAt"],
            updated_at=item["updatedAt"],
        )


class DeviceAdminStore(DeviceStore):
    """Atomic administrator mutations for the authorized-device store."""

    def add(self, name: str, permissions: frozenset[str]) -> AuthorizedDevice:
        with self._mutation_lock():
            devices = self.load()
            now = _utc_now()
            device = AuthorizedDevice(
                device_id=str(uuid.uuid4()),
                name=validate_device_name(name),
                key=secrets.token_bytes(DEVICE_KEY_BYTES),
                permissions=validate_permissions(permissions),
                revoked=False,
                created_at=now,
                updated_at=now,
            )
            devices[device.device_id] = device
            self._save_unlocked(devices)
            return device

    def revoke(self, device_id: str) -> AuthorizedDevice:
        return self._replace(device_id, revoked=True)

    def rotate(self, device_id: str) -> AuthorizedDevice:
        return self._replace(
            device_id,
            key=secrets.token_bytes(DEVICE_KEY_BYTES),
            revoked=False,
        )

    def set_permissions(
        self, device_id: str, permissions: frozenset[str]
    ) -> AuthorizedDevice:
        return self._replace(device_id, permissions=validate_permissions(permissions))

    def _replace(self, device_id: str, **changes: Any) -> AuthorizedDevice:
        try:
            device_id = validate_device_id(device_id)
        except GatewayAuthError:
            raise KeyError("device not found") from None
        with self._mutation_lock():
            devices = self.load()
            current = devices.get(device_id)
            if current is None:
                raise KeyError("device not found")
            values = {
                "device_id": current.device_id,
                "name": current.name,
                "key": current.key,
                "permissions": current.permissions,
                "revoked": current.revoked,
                "created_at": current.created_at,
                "updated_at": _utc_now(),
            }
            values.update(changes)
            updated = AuthorizedDevice(**values)
            devices[device_id] = updated
            self._save_unlocked(devices)
            return updated

    def save(self, devices: dict[str, AuthorizedDevice]) -> None:
        with self._mutation_lock():
            self._save_unlocked(devices)

    @contextmanager
    def _mutation_lock(self):
        self.path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        parent_status = self.path.parent.lstat()
        if not stat.S_ISDIR(parent_status.st_mode) or self.path.parent.is_symlink():
            raise ValueError("device store directory must be a regular directory")
        lock_path = self.path.parent / ".devices.lock"
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except OSError as error:
            raise ValueError("device store lock cannot be opened safely") from error
        try:
            lock_status = os.fstat(descriptor)
            if not stat.S_ISREG(lock_status.st_mode) or lock_status.st_nlink != 1:
                raise ValueError("device store lock must be a regular file")
            if stat.S_IMODE(lock_status.st_mode) != 0o600:
                raise ValueError("device store lock must have mode 0600")
            if lock_status.st_uid != os.geteuid():
                raise ValueError("device store lock has an unexpected owner")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    def _save_unlocked(self, devices: dict[str, AuthorizedDevice]) -> None:
        payload = {
            "schemaVersion": DEVICE_STORE_SCHEMA_VERSION,
            "devices": [
                {
                    "id": device.device_id,
                    "name": device.name,
                    "key": encode_base64url(device.key),
                    "permissions": sorted(device.permissions),
                    "revoked": device.revoked,
                    "createdAt": device.created_at,
                    "updatedAt": device.updated_at,
                }
                for device in sorted(devices.values(), key=lambda item: item.device_id)
            ],
        }
        encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.path.parent, prefix=".devices-", suffix=".json"
        )
        try:
            parent_status = self.path.parent.stat()
            os.fchown(descriptor, parent_status.st_uid, parent_status.st_gid)
            os.fchmod(descriptor, 0o640)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_name, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            Path(temporary_name).unlink(missing_ok=True)
            raise
