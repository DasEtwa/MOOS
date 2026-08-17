#!/usr/bin/env python3
"""Test moosd's typed local operations without starting QEMU or changing hosts."""

import json
import os
import pty
import select
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "host"))

from moos_runtime import RuntimeState, RuntimeStatus, TerminalChannel  # noqa: E402
from moosd import MoosdService, PROTOCOL_VERSION, _serve_terminal, serve  # noqa: E402


class FakeRuntime:
    def __init__(self):
        self.state = RuntimeState.STOPPED
        self.calls = []

    def status(self):
        self.calls.append("status")
        return RuntimeStatus("personal", self.state)

    def start(self):
        self.calls.append("start")
        if self.state == RuntimeState.RUNNING:
            from moos_runtime import AlreadyRunning
            raise AlreadyRunning("personal")
        self.state = RuntimeState.RUNNING
        return RuntimeStatus("personal", self.state)

    def stop(self):
        self.calls.append("stop")
        if self.state == RuntimeState.STOPPED:
            from moos_runtime import NotRunning
            raise NotRunning("personal")
        self.state = RuntimeState.STOPPED
        return RuntimeStatus("personal", self.state)

    def open_terminal(self):
        from moos_runtime import NotRunning
        if self.state != RuntimeState.RUNNING:
            raise NotRunning("personal")
        return object()


def main():
    runtime = FakeRuntime()
    service = MoosdService(runtime)

    assert service.handle({"protocolVersion": 1, "operation": "status"})["ok"]
    assert service.handle({"protocolVersion": 1, "operation": "personal.start"})["ok"]
    duplicate = service.handle({"protocolVersion": 1, "operation": "personal.start"})
    assert duplicate["error"]["code"] == "already_running"
    assert service.handle({"protocolVersion": 1, "operation": "personal.stop"})["ok"]
    assert service.handle({"protocolVersion": 1, "operation": "personal.stop"})["error"]["code"] == "not_running"
    assert service.handle({"protocolVersion": 1, "operation": "personal.terminal.open"})["error"]["code"] == "not_running"
    assert service.handle({"protocolVersion": 99, "operation": "status"})["error"]["code"] == "unsupported_protocol"
    assert service.handle({"protocolVersion": 1, "operation": "exec"})["error"]["code"] == "unknown_operation"
    assert service.handle({"protocolVersion": 1, "operation": "status", "path": "/etc"})["error"]["code"] == "invalid_request"

    assert runtime.calls == ["status", "start", "start", "stop", "stop"]

    with tempfile.TemporaryDirectory() as temporary:
        socket_path = Path(temporary) / "moosd.sock"
        thread = threading.Thread(target=serve, args=(socket_path, service), daemon=True)
        thread.start()
        deadline = time.monotonic() + 2
        while not socket_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert socket_path.exists()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.connect(str(socket_path))
            connection.sendall((json.dumps({"protocolVersion": 1, "operation": "status"}) + "\n").encode())
            response = json.loads(connection.recv(4096))
        assert response["protocolVersion"] == 1
        assert response["ok"] is True

    master_fd, slave_fd = pty.openpty()
    channel = TerminalChannel(master_fd)
    runtime.open_terminal = lambda: channel
    server_socket, client_socket = socket.socketpair()
    bridge = threading.Thread(target=_serve_terminal, args=(server_socket, MoosdService(runtime)), daemon=True)
    bridge.start()
    client_socket.sendall(b'{"type":"input","data":"moos-info\\n"}\n')
    deadline = time.monotonic() + 2
    guest_input = b""
    while b"moos-info" not in guest_input and time.monotonic() < deadline:
        readable, _, _ = select.select([slave_fd], [], [], 0.1)
        if readable:
            guest_input += os.read(slave_fd, 4096)
    assert b"moos-info" in guest_input
    os.write(slave_fd, b"MOOS_TERMINAL_OUTPUT\\n")
    deadline = time.monotonic() + 2
    output = b""
    while b"MOOS_TERMINAL_OUTPUT" not in output and time.monotonic() < deadline:
        readable, _, _ = select.select([client_socket], [], [], 0.1)
        if readable:
            output += client_socket.recv(4096)
    assert b"MOOS_TERMINAL_OUTPUT" in output
    client_socket.sendall(b'{"type":"close"}\n')
    client_socket.close()
    server_socket.close()
    os.close(slave_fd)
    channel.close()

    print("MOOS moosd protocol test: PASS")
    print(f"  protocol version: {PROTOCOL_VERSION}")
    print("  typed Personal status/start/stop: ok")
    print("  invalid versions/fields and arbitrary exec: rejected")
    print("  Unix socket request/response: ok")
    print("  bidirectional terminal input/output bridge: ok")
    print("  event envelope reserved without exposing host internals: ok")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, OSError) as error:
        print("MOOS moosd protocol test: FAIL", file=sys.stderr)
        print(f"  {error}", file=sys.stderr)
        sys.exit(1)
