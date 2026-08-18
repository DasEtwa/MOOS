#!/usr/bin/env python3
"""Regression-test bounded moosd framing and terminal protocol behavior."""

import os
import socket
import stat
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "host"))

from moos_protocol import (  # noqa: E402
    MAX_FRAME_BYTES,
    PROTOCOL_VERSION,
    FrameDecoder,
    encode_frame,
)
from moos_runtime import (  # noqa: E402
    AlreadyRunning,
    NotRunning,
    RuntimeState,
    RuntimeStatus,
    TerminalChannel,
)
import moosd  # noqa: E402
from moosd import MoosdService, serve  # noqa: E402


class FakeRuntime:
    def __init__(self):
        self.state = RuntimeState.STOPPED
        self.calls = []
        self.guest_channels = deque()
        self.closed_channels = 0

    def status(self):
        self.calls.append("status")
        return RuntimeStatus("personal", self.state, "success")

    def start(self):
        self.calls.append("start")
        if self.state == RuntimeState.RUNNING:
            raise AlreadyRunning("personal")
        self.state = RuntimeState.RUNNING
        return RuntimeStatus("personal", self.state, "success")

    def stop(self):
        self.calls.append("stop")
        if self.state == RuntimeState.STOPPED:
            raise NotRunning("personal")
        self.state = RuntimeState.STOPPED
        return RuntimeStatus("personal", self.state, "success")

    def open_terminal(self):
        if self.state != RuntimeState.RUNNING:
            raise NotRunning("personal")
        daemon_side, guest_side = socket.socketpair()
        self.guest_channels.append(guest_side)

        def released():
            self.closed_channels += 1

        return TerminalChannel(daemon_side, released)


def response(service, request):
    dispatched = service.dispatch(request)
    if dispatched.terminal is not None:
        dispatched.terminal.close()
    return dispatched.response


def receive_frames(connection, count, timeout=2.0):
    decoder = FrameDecoder()
    frames = []
    deadline = time.monotonic() + timeout
    while len(frames) < count and time.monotonic() < deadline:
        connection.settimeout(max(0.01, deadline - time.monotonic()))
        data = connection.recv(4096)
        if not data:
            break
        for result in decoder.feed(data):
            assert result.error is None, result.error
            assert result.frame is not None
            frames.append(result.frame)
    assert len(frames) == count, frames
    return frames


def wait_for_channel(runtime):
    deadline = time.monotonic() + 2
    while not runtime.guest_channels and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runtime.guest_channels
    return runtime.guest_channels.popleft()


def main():
    runtime = FakeRuntime()
    service = MoosdService(runtime)

    assert response(service, {"protocolVersion": 1, "operation": "status"})["ok"]
    gateway_status = service.dispatch(
        {"protocolVersion": 1, "operation": "status"},
        allowed_operations=frozenset({"status"}),
    ).response
    assert gateway_status["ok"] is True
    gateway_stop = service.dispatch(
        {"protocolVersion": 1, "operation": "personal.stop"},
        allowed_operations=frozenset({"status"}),
    ).response
    assert gateway_stop["error"]["code"] == "forbidden"
    assert runtime.calls == ["status", "status"]

    original_gateway_uids = moosd._gateway_uids
    moosd._gateway_uids = lambda: frozenset({os.getuid()})
    client_side, daemon_side = socket.socketpair()
    gateway_thread = threading.Thread(
        target=moosd._handle_client,
        args=(daemon_side, service),
        daemon=True,
    )
    gateway_thread.start()
    try:
        client_side.sendall(
            encode_frame({"protocolVersion": 1, "operation": "personal.stop"})
        )
        peer_denied = receive_frames(client_side, 1)[0]
        assert peer_denied["error"]["code"] == "forbidden"
        assert runtime.calls == ["status", "status"]
    finally:
        client_side.close()
        gateway_thread.join(timeout=2)
        moosd._gateway_uids = original_gateway_uids
    assert response(service, {"protocolVersion": 1, "operation": "personal.start"})["ok"]
    duplicate = response(service, {"protocolVersion": 1, "operation": "personal.start"})
    assert duplicate["error"]["code"] == "already_running"
    assert response(service, {"protocolVersion": 1, "operation": "personal.stop"})["ok"]
    assert response(service, {"protocolVersion": 1, "operation": "personal.stop"})[
        "error"
    ]["code"] == "not_running"
    assert response(
        service, {"protocolVersion": 1, "operation": "personal.terminal.open"}
    )["error"]["code"] == "not_running"
    assert response(service, {"protocolVersion": 99, "operation": "status"})[
        "error"
    ]["code"] == "unsupported_protocol"
    for invalid_version in (True, 1.0):
        assert response(
            service,
            {"protocolVersion": invalid_version, "operation": "status"},
        )["error"]["code"] == "unsupported_protocol"
    assert response(service, {"protocolVersion": 1, "operation": "exec"})["error"][
        "code"
    ] == "unknown_operation"
    assert response(
        service, {"protocolVersion": 1, "operation": "status", "path": "/etc"}
    )["error"]["code"] == "invalid_request"

    with tempfile.TemporaryDirectory() as temporary:
        socket_path = Path(temporary) / "moosd.sock"
        thread = threading.Thread(
            target=serve, args=(socket_path, service), daemon=True
        )
        thread.start()
        deadline = time.monotonic() + 2
        while not socket_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert socket_path.exists()
        assert stat.S_IMODE(socket_path.stat().st_mode) == 0o660

        status_frame = encode_frame(
            {"protocolVersion": 1, "operation": "personal.status"}
        )
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.connect(str(socket_path))
            for byte in status_frame:
                connection.sendall(bytes([byte]))
            assert receive_frames(connection, 1)[0]["ok"] is True

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.connect(str(socket_path))
            connection.sendall(status_frame + status_frame)
            frames = receive_frames(connection, 2)
            assert all(frame["ok"] for frame in frames)

        invalid_values = [b"[]\n", b'"text"\n', b"7\n", b"null\n", b"{bad}\n"]
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.connect(str(socket_path))
            connection.sendall(b"".join(invalid_values) + status_frame)
            frames = receive_frames(connection, 6)
            assert all(not frame["ok"] for frame in frames[:5])
            assert all(
                frame["error"]["code"] == "invalid_request" for frame in frames[:5]
            )
            assert frames[5]["ok"] is True

        oversized = b'{"value":"' + (b"x" * MAX_FRAME_BYTES) + b'"}\n'
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.connect(str(socket_path))
            connection.sendall(oversized + status_frame)
            frames = receive_frames(connection, 2)
            assert frames[0]["error"]["code"] == "request_too_large"
            assert frames[1]["ok"] is True

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.connect(str(socket_path))
            connection.sendall(b'{"protocolVersion":1')
            connection.shutdown(socket.SHUT_WR)
            truncated = receive_frames(connection, 1)[0]
            assert truncated["error"]["code"] == "invalid_request"

        runtime.state = RuntimeState.RUNNING
        open_frame = encode_frame(
            {"protocolVersion": 1, "operation": "personal.terminal.open"}
        )
        input_frame = encode_frame({"type": "input", "data": "moos-info\n"})
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.connect(str(socket_path))
            midpoint = len(input_frame) // 2
            connection.sendall(open_frame + input_frame[:midpoint])
            acknowledgment = receive_frames(connection, 1)[0]
            assert acknowledgment["ok"] is True
            guest = wait_for_channel(runtime)
            connection.sendall(input_frame[midpoint:])
            guest.settimeout(2)
            assert guest.recv(4096) == b"moos-info\n"

            connection.sendall(b'[]\n"text"\n3\nnull\n{bad}\n')
            errors = receive_frames(connection, 5)
            assert all(error["type"] == "error" for error in errors)

            guest.sendall(b"MOOS_TERMINAL_OUTPUT\n")
            output = receive_frames(connection, 1)[0]
            assert output == {"type": "output", "data": "MOOS_TERMINAL_OUTPUT\n"}

            close_frame = encode_frame({"type": "close"})
            for byte in close_frame:
                connection.sendall(bytes([byte]))
            guest.close()

        deadline = time.monotonic() + 2
        while runtime.closed_channels < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert runtime.closed_channels >= 1

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.connect(str(socket_path))
            connection.sendall(open_frame)
            assert receive_frames(connection, 1)[0]["ok"] is True
            guest = wait_for_channel(runtime)
            guest.sendall(b"RECONNECTED\n")
            assert receive_frames(connection, 1)[0]["data"] == "RECONNECTED\n"
            connection.sendall(encode_frame({"type": "close"}))
            guest.close()

        deadline = time.monotonic() + 2
        while runtime.closed_channels < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert runtime.closed_channels >= 2

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.connect(str(socket_path))
            connection.sendall(open_frame + b'{"type":"input"')
            assert receive_frames(connection, 1)[0]["ok"] is True
            guest = wait_for_channel(runtime)
            connection.shutdown(socket.SHUT_WR)
            terminal_error = receive_frames(connection, 1)[0]
            assert terminal_error == {"type": "error", "code": "invalid_frame"}
            guest.close()

    combined = encode_frame({"ok": True}) + encode_frame(
        {"type": "output", "data": "boot"}
    )
    decoder = FrameDecoder()
    results = []
    for byte in combined:
        results.extend(decoder.feed(bytes([byte])))
    assert [result.frame for result in results] == [
        {"ok": True},
        {"type": "output", "data": "boot"},
    ]

    print("MOOS moosd protocol test: PASS")
    print("  fragmented and concatenated bounded request frames: ok")
    print("  fragmented/combined response decoding: ok")
    print("  invalid JSON values and malformed/oversized JSON stay client-local: ok")
    print("  truncated control and terminal frames return structured errors: ok")
    print("  strict versions and terminal disconnect/reconnect behavior: ok")
    print(f"  protocol version: {PROTOCOL_VERSION}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, OSError, TimeoutError) as error:
        print("MOOS moosd protocol test: FAIL", file=sys.stderr)
        print(f"  {error}", file=sys.stderr)
        sys.exit(1)
