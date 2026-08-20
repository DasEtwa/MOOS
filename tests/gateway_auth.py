#!/usr/bin/env python3
"""Verify the shared Swift/Rust Gateway-v1 authentication contract."""

import base64
import hashlib
import hmac
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "gateway-v1-contract.json"
SWIFT_TESTS = REPO_ROOT / "ios" / "MOOSApp" / "MOOSAppTests" / "MOOSAppTests.swift"


def decode(value: str) -> bytes:
    if not value or "=" in value:
        raise AssertionError("fixture base64url must be nonempty and unpadded")
    decoded = base64.b64decode(
        value + "=" * ((4 - len(value) % 4) % 4), altchars=b"-_", validate=True
    )
    if base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != value:
        raise AssertionError("fixture base64url is not canonical")
    return decoded


def proof(context: bytes, fixture: dict) -> bytes:
    payload = b"".join(
        (
            context,
            fixture["serverNonce"].encode("ascii"),
            b"\n",
            fixture["clientNonce"].encode("ascii"),
            b"\n",
            fixture["deviceId"].encode("ascii"),
            b"\n",
        )
    )
    return hmac.new(decode(fixture["deviceKey"]), payload, hashlib.sha256).digest()


def encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def main() -> int:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    required = {
        "authenticationProof",
        "clientNonce",
        "deviceId",
        "deviceKey",
        "gatewayVersion",
        "pairingCode",
        "serverNonce",
        "serverProof",
    }
    if set(fixture) != required or fixture["gatewayVersion"] != 1:
        raise AssertionError("invalid shared Gateway-v1 fixture")
    for field in ("deviceKey", "serverNonce", "clientNonce"):
        if len(decode(fixture[field])) != 32:
            raise AssertionError(f"{field} must decode to 32 bytes")
    if encode(proof(b"MOOS-GATEWAY-AUTH-V1\n", fixture)) != fixture["authenticationProof"]:
        raise AssertionError("client HMAC vector changed")
    if encode(proof(b"MOOS-GATEWAY-SERVER-V1\n", fixture)) != fixture["serverProof"]:
        raise AssertionError("server HMAC vector changed")
    expected_pairing = (
        f"moos-pair-v1:{fixture['deviceId']}:{fixture['deviceKey']}"
    )
    if fixture["pairingCode"] != expected_pairing:
        raise AssertionError("pairing-code contract changed")

    swift = SWIFT_TESTS.read_text(encoding="utf-8")
    for expected_hex in (
        proof(b"MOOS-GATEWAY-AUTH-V1\n", fixture).hex(),
        proof(b"MOOS-GATEWAY-SERVER-V1\n", fixture).hex(),
    ):
        if expected_hex not in swift:
            raise AssertionError("Swift client no longer shares the Gateway HMAC vector")

    print("MOOS Gateway authentication contract test: PASS")
    print("  canonical key/nonces and mutual HMAC contexts: ok")
    print("  pairing prefix and Swift cross-client vectors: ok")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        print("MOOS Gateway authentication contract test: FAIL", file=sys.stderr)
        print(f"  {error}", file=sys.stderr)
        sys.exit(1)
