#!/usr/bin/env python3
"""Exercise authenticated Gateway forwarding and authorization boundaries."""

import socket
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "host"))

from moos_gateway import (  # noqa: E402
    AdmissionLimiter,
    GatewaySession,
    validate_process_groups,
    validate_tailscale_address,
    validate_tailscale_interface_address,
)
from moos_gateway_auth import (  # noqa: E402
    DeviceAdminStore,
    encode_base64url,
    make_authenticate_frame,
    make_server_authentication_proof,
    parse_challenge,
)
from moos_protocol import FrameDecoder, encode_frame, make_control_success  # noqa: E402


def receive_frame(connection):
    decoder = FrameDecoder()
    pending = deque()
    while True:
        while pending:
            result = pending.popleft()
            assert result.error is None
            assert result.frame is not None
            return result.frame
        data = connection.recv(4096)
        assert data
        pending.extend(decoder.feed(data))


def start_session(store, backend_connector, *, auth_timeout=10.0):
    client, gateway = socket.socketpair()
    thread = threading.Thread(
        target=GatewaySession(
            store, backend_connector, auth_timeout=auth_timeout
        ).handle,
        args=(gateway,),
        daemon=True,
    )
    thread.start()
    return client, thread


def authenticate(connection, device, key=None):
    challenge = receive_frame(connection)
    nonce = parse_challenge(challenge)
    client_nonce = bytes(range(32))
    connection.sendall(
        encode_frame(
            make_authenticate_frame(
                device.device_id,
                device.key if key is None else key,
                nonce,
                client_nonce=client_nonce,
            )
        )
    )
    return receive_frame(connection), nonce, client_nonce


def main():
    validate_process_groups(
        primary_gid=100,
        control_gid=200,
        supplementary_gids={200},
    )
    try:
        validate_process_groups(
            primary_gid=100,
            control_gid=200,
            supplementary_gids={200, 999},
        )
    except ValueError:
        pass
    else:
        raise AssertionError("accepted an unexpected gateway supplementary group")

    clock = [0.0]
    limiter = AdmissionLimiter(clock=lambda: clock[0])
    for _ in range(4):
        assert limiter.admit("100.64.0.2")
    assert not limiter.admit("100.64.0.2")
    for _ in range(4):
        limiter.release("100.64.0.2")
    rate_limiter = AdmissionLimiter(clock=lambda: clock[0])
    for _ in range(20):
        assert rate_limiter.admit("100.64.0.3")
        rate_limiter.release("100.64.0.3")
    assert not rate_limiter.admit("100.64.0.3")
    clock[0] = 61.0
    assert rate_limiter.admit("100.64.0.3")

    assert validate_tailscale_address("100.64.0.1") == "100.64.0.1"
    assert validate_tailscale_address("100.127.255.254") == "100.127.255.254"
    assert validate_tailscale_address("fd7a:115c:a1e0::1") == "fd7a:115c:a1e0::1"
    assert validate_tailscale_interface_address(
        "100.64.0.1", assigned_addresses={"100.64.0.1"}
    ) == "100.64.0.1"
    try:
        validate_tailscale_interface_address(
            "100.64.0.1", assigned_addresses={"100.64.0.2"}
        )
    except ValueError:
        pass
    else:
        raise AssertionError("accepted a Tailscale address not assigned to tailscale0")
    for invalid in ("0.0.0.0", "127.0.0.1", "192.168.1.4", "tailscale0"):
        try:
            validate_tailscale_address(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted non-Tailscale listener: {invalid}")

    with tempfile.TemporaryDirectory() as temporary:
        store = DeviceAdminStore(Path(temporary) / "devices.json")
        device = store.add("Test iPhone", frozenset({"status"}))
        backend_requests = []

        def backend_connector():
            gateway_side, daemon_side = socket.socketpair()

            def daemon():
                request = receive_frame(daemon_side)
                backend_requests.append(request)
                daemon_side.sendall(
                    encode_frame(
                        make_control_success(
                            "status",
                            {
                                "personal": {
                                    "identity": "personal",
                                    "state": "running",
                                    "result": "success",
                                }
                            },
                        )
                    )
                )
                daemon_side.close()

            threading.Thread(target=daemon, daemon=True).start()
            return gateway_side

        connection, thread = start_session(store, backend_connector)
        with connection:
            authenticated, server_nonce, client_nonce = authenticate(connection, device)
            assert authenticated == {
                "gatewayVersion": 1,
                "type": "authenticated",
                "deviceId": device.device_id,
                "permissions": ["status"],
                "serverProof": encode_base64url(
                    make_server_authentication_proof(
                        device.key,
                        server_nonce,
                        client_nonce,
                        device.device_id,
                    )
                ),
            }

            connection.sendall(
                encode_frame({"protocolVersion": 1, "operation": "personal.stop"})
            )
            denied = receive_frame(connection)
            assert denied["ok"] is False
            assert denied["error"]["code"] == "forbidden"
            assert backend_requests == []

            connection.sendall(
                encode_frame({"protocolVersion": 1, "operation": "status"})
            )
            response = receive_frame(connection)
            assert response["ok"] is True
            assert response["operation"] == "status"
            assert response["data"]["personal"]["state"] == "running"
            assert backend_requests == [
                {"protocolVersion": 1, "operation": "status"}
            ]
        thread.join(timeout=2)

        no_backend = lambda: (_ for _ in ()).throw(AssertionError("backend used"))
        revoked_connection, revoked_thread = start_session(store, no_backend)
        with revoked_connection:
            authenticated, _, _ = authenticate(
                revoked_connection, store.load()[device.device_id]
            )
            assert authenticated["type"] == "authenticated"
            store.revoke(device.device_id)
            revoked_connection.sendall(
                encode_frame({"protocolVersion": 1, "operation": "status"})
            )
            revoked = receive_frame(revoked_connection)
            assert revoked["error"]["code"] == "forbidden"
        revoked_thread.join(timeout=2)

        bad_connection, bad_thread = start_session(store, no_backend)
        with bad_connection:
            failed, _, _ = authenticate(
                bad_connection,
                store.load()[device.device_id],
                key=bytes(32),
            )
            assert failed == {
                "gatewayVersion": 1,
                "type": "error",
                "code": "authentication_failed",
                "message": "Device authentication failed",
            }
        bad_thread.join(timeout=2)

        slow_connection, slow_thread = start_session(
            store, no_backend, auth_timeout=0.08
        )
        with slow_connection:
            receive_frame(slow_connection)
            started = time.monotonic()
            for byte in (b"{", b'"', b"x", b'"'):
                try:
                    slow_connection.sendall(byte)
                except OSError:
                    break
                time.sleep(0.04)
            slow_connection.settimeout(1)
            failed = receive_frame(slow_connection)
            assert failed["code"] == "authentication_failed"
            assert time.monotonic() - started < 0.20
        slow_thread.join(timeout=2)

    print("MOOS Gateway protocol test: PASS")
    print("  listener requires an address assigned to tailscale0: ok")
    print("  authenticated status reaches the local Protocol-v1 backend: ok")
    print("  unauthorized operations never reach moosd: ok")
    print("  live revocation and generic failed authentication: ok")
    print("  authentication has one absolute deadline: ok")
    print("  mutual server proof, source admission, and group boundary: ok")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, OSError, ValueError) as error:
        print("MOOS Gateway protocol test: FAIL", file=sys.stderr)
        print(f"  {error}", file=sys.stderr)
        sys.exit(1)
