#!/usr/bin/env python3
"""Verify MOOS Gateway pairing, device storage, and challenge authentication."""

import multiprocessing
import os
import stat
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "host"))

from moos_gateway_auth import (  # noqa: E402
    DeviceAdminStore,
    DeviceStore,
    GatewayAuthError,
    decode_base64url,
    make_authenticate_frame,
    make_authentication_proof,
    make_server_authentication_proof,
    make_challenge,
    pairing_code,
    parse_challenge,
    parse_pairing_code,
    validate_permissions,
    verify_authenticate_frame,
)


def expect_auth_failure(function):
    try:
        function()
    except GatewayAuthError as error:
        assert str(error) == "authentication failed"
    else:
        raise AssertionError("expected generic authentication failure")


def add_test_device(store_path, index):
    DeviceAdminStore(Path(store_path)).add(
        f"Concurrent device {index}", frozenset({"status"})
    )


def main():
    assert make_authentication_proof(
        bytes([7]) * 32,
        bytes([3]) * 32,
        bytes([4]) * 32,
        "00000000-0000-4000-8000-000000000001",
    ).hex() == "fadb524047e066a7546f364eab2e10cbd5203e945c9ae44aaf1346c0ee72689b"

    with tempfile.TemporaryDirectory() as temporary:
        store_path = Path(temporary) / "devices.json"
        store = DeviceAdminStore(store_path)
        device = store.add("Daniel's iPhone", frozenset({"status"}))
        assert stat.S_IMODE(store_path.stat().st_mode) == 0o640
        assert store_path.stat().st_gid == Path(temporary).stat().st_gid
        assert store.load()[device.device_id] == device

        code = pairing_code(device)
        assert parse_pairing_code(code) == (device.device_id, device.key)
        assert code not in store_path.read_text()
        assert "pairing code" not in store_path.read_text().lower()

        challenge_frame, server_nonce = make_challenge()
        assert parse_challenge(challenge_frame) == server_nonce
        authenticate = make_authenticate_frame(
            device.device_id,
            device.key,
            server_nonce,
            client_nonce=bytes(range(32)),
        )
        verified, verified_client_nonce = verify_authenticate_frame(
            authenticate, server_nonce, store.load()
        )
        assert verified == device
        assert verified_client_nonce == bytes(range(32))

        wrong_nonce = bytes(reversed(range(32)))
        expect_auth_failure(
            lambda: verify_authenticate_frame(authenticate, wrong_nonce, store.load())
        )
        wrong_key_frame = make_authenticate_frame(
            device.device_id, os.urandom(32), server_nonce
        )
        expect_auth_failure(
            lambda: verify_authenticate_frame(wrong_key_frame, server_nonce, store.load())
        )
        unknown_frame = make_authenticate_frame(
            "00000000-0000-4000-8000-000000000000",
            os.urandom(32),
            server_nonce,
        )
        expect_auth_failure(
            lambda: verify_authenticate_frame(unknown_frame, server_nonce, store.load())
        )

        revoked = store.revoke(device.device_id)
        assert revoked.revoked
        expect_auth_failure(
            lambda: verify_authenticate_frame(authenticate, server_nonce, store.load())
        )

        rotated = store.rotate(device.device_id)
        assert not rotated.revoked
        assert rotated.key != device.key
        expect_auth_failure(
            lambda: verify_authenticate_frame(authenticate, server_nonce, store.load())
        )
        rotated_frame = make_authenticate_frame(
            rotated.device_id, rotated.key, server_nonce
        )
        verified_rotated, _ = verify_authenticate_frame(
            rotated_frame, server_nonce, store.load()
        )
        assert verified_rotated == rotated

        malformed = authenticate | {"extra": True}
        expect_auth_failure(
            lambda: verify_authenticate_frame(malformed, server_nonce, store.load())
        )
        for invalid in ("", "AA==", "not/base64", "AA"):
            expect_auth_failure(
                lambda value=invalid: decode_base64url(value, expected_bytes=32)
            )

        try:
            store.set_permissions(device.device_id, frozenset({"personal.stop"}))
        except ValueError:
            pass
        else:
            raise AssertionError("unsupported remote permission was accepted")
        for invalid_permissions in ([], [{"status": True}], [1], "status"):
            try:
                validate_permissions(invalid_permissions)
            except ValueError:
                pass
            else:
                raise AssertionError("malformed remote permissions were accepted")

        assert make_server_authentication_proof(
            bytes([7]) * 32,
            bytes([3]) * 32,
            bytes([4]) * 32,
            "00000000-0000-4000-8000-000000000001",
        ).hex() == "62674501e0aece3d3a6b34217c1eec59313614c6a68142d37c589459929f118f"

        os.chmod(temporary, 0o750)
        strict_store = DeviceStore(
            store_path, expected_uid=os.getuid(), expected_gid=os.getgid()
        )
        assert strict_store.load()[device.device_id] == rotated

        os.chmod(store_path, 0o644)
        try:
            strict_store.load()
        except ValueError as error:
            assert "mode 0640" in str(error)
        else:
            raise AssertionError("unsafe device store mode was accepted")
        os.chmod(store_path, 0o640)

        os.chmod(temporary, 0o770)
        try:
            strict_store.load()
        except ValueError as error:
            assert "mode 0750" in str(error)
        else:
            raise AssertionError("unsafe device directory mode was accepted")
        os.chmod(temporary, 0o750)

        wrong_owner_store = DeviceStore(
            store_path, expected_uid=os.getuid() + 1, expected_gid=os.getgid()
        )
        try:
            wrong_owner_store.load()
        except ValueError as error:
            assert "unexpected owner" in str(error)
        else:
            raise AssertionError("unexpected device store owner was accepted")

        link_path = Path(temporary) / "linked-devices.json"
        link_path.symlink_to(store_path)
        try:
            DeviceStore(link_path).load()
        except ValueError:
            pass
        else:
            raise AssertionError("symlink device store was accepted")

        hardlink_path = Path(temporary) / "hardlinked-devices.json"
        os.link(store_path, hardlink_path)
        try:
            DeviceStore(store_path).load()
        except ValueError as error:
            assert "hard links" in str(error)
        else:
            raise AssertionError("hard-linked device store was accepted")
        hardlink_path.unlink()

    with tempfile.TemporaryDirectory() as temporary:
        store_path = Path(temporary) / "devices.json"
        context = multiprocessing.get_context("fork")
        processes = [
            context.Process(target=add_test_device, args=(store_path, index))
            for index in range(12)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=5)
            assert process.exitcode == 0
        assert len(DeviceStore(store_path).load()) == len(processes)
        lock_path = Path(temporary) / ".devices.lock"
        assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
        assert lock_path.stat().st_nlink == 1

    print("MOOS Gateway authentication test: PASS")
    print("  per-device keys and one-time pairing representation: ok")
    print("  challenge binding, malformed input, and generic failures: ok")
    print("  cross-client HMAC test vector: ok")
    print("  direction-separated server proof: ok")
    print("  atomic store, strict ownership/modes, revocation, and rotation: ok")
    print("  concurrent administrator mutations retain every update: ok")
    print("  remote authorization defaults to status only: ok")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, OSError, ValueError) as error:
        print("MOOS Gateway authentication test: FAIL", file=sys.stderr)
        print(f"  {error}", file=sys.stderr)
        sys.exit(1)
