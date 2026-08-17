#!/usr/bin/env python3
"""Real managed Personal QEMU, terminal, and daemon-restart integration test.

Run only after the explicit root setup and Personal staging documented in the
README. This test changes the systemd state of moos-instance-personal.service.
"""

import os
import re
import select
import socket
import subprocess
import sys
import tempfile
import time
from collections import deque
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "host"))

from moos_protocol import FrameDecoder, encode_frame  # noqa: E402


UNIT = "moos-instance-personal.service"
ANSI_ESCAPE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))"
)


class ProtocolClient:
    def __init__(self, socket_path):
        self.connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.connection.connect(str(socket_path))
        self.decoder = FrameDecoder()
        self.pending = deque()

    def send(self, frame):
        self.connection.sendall(encode_frame(frame))

    def receive(self, timeout=5):
        deadline = time.monotonic() + timeout
        while True:
            while self.pending:
                result = self.pending.popleft()
                if result.error is not None or result.frame is None:
                    raise AssertionError("received an invalid daemon frame")
                return result.frame
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for daemon frame")
            readable, _, _ = select.select([self.connection], [], [], remaining)
            if not readable:
                raise TimeoutError("timed out waiting for daemon frame")
            data = self.connection.recv(4096)
            if not data:
                raise ConnectionError("daemon closed the protocol connection")
            self.pending.extend(self.decoder.feed(data))

    def close(self):
        self.connection.close()


def request(socket_path, operation, timeout=10):
    client = ProtocolClient(socket_path)
    try:
        client.send({"protocolVersion": 1, "operation": operation})
        return client.receive(timeout)
    finally:
        client.close()


def start_daemon(socket_path):
    socket_path.unlink(missing_ok=True)
    daemon = subprocess.Popen(
        [
            str(REPO_ROOT / "scripts/moosd.py"),
            "--socket",
            str(socket_path),
            "--runner",
            str(REPO_ROOT / "scripts/run-instance.sh"),
        ],
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if socket_path.exists():
            return daemon
        if daemon.poll() is not None:
            raise RuntimeError("moosd exited during startup: " + (daemon.stderr.read() or ""))
        time.sleep(0.02)
    daemon.terminate()
    daemon.wait(timeout=5)
    raise TimeoutError("timed out waiting for moosd socket")


def stop_daemon(daemon):
    if daemon.poll() is None:
        daemon.terminate()
        try:
            daemon.wait(timeout=5)
        except subprocess.TimeoutExpired:
            daemon.kill()
            daemon.wait(timeout=5)
    if daemon.stderr is not None:
        daemon.stderr.close()


def open_terminal(socket_path):
    terminal = ProtocolClient(socket_path)
    terminal.send(
        {"protocolVersion": 1, "operation": "personal.terminal.open"}
    )
    acknowledgment = terminal.receive(5)
    if not acknowledgment.get("ok"):
        raise AssertionError(f"terminal open failed: {acknowledgment}")
    return terminal


def terminal_input(terminal, data):
    terminal.send({"type": "input", "data": data})


def terminal_output_until(terminal, pattern, timeout):
    expression = re.compile(pattern, re.MULTILINE)
    output = ""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = terminal.receive(max(0.01, deadline - time.monotonic()))
        if frame.get("type") == "error":
            raise AssertionError(f"terminal error: {frame}")
        if frame.get("type") != "output" or not isinstance(frame.get("data"), str):
            raise AssertionError(f"invalid terminal output: {frame}")
        output += ANSI_ESCAPE.sub("", frame["data"]).replace("\r", "")
        if expression.search(output):
            return output
    raise TimeoutError(f"timed out waiting for terminal output: {pattern}")


def systemctl_state():
    completed = subprocess.run(
        ["systemctl", "show", UNIT, "--property=ActiveState", "--value"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("could not inspect the managed Personal unit")
    return completed.stdout.strip()


def preflight():
    if os.geteuid() != 0:
        raise PermissionError("run with sudo after reviewing the root integration test")
    if systemctl_state() not in {"", "inactive"}:
        raise RuntimeError(f"refusing to replace existing {UNIT} state")
    required = [
        Path("/var/lib/moos/instances/personal/bzImage"),
        Path("/var/lib/moos/instances/personal/rootfs.ext2"),
        Path("/var/lib/moos/runtime/qemu-host/bin/qemu-system-x86_64"),
        Path("/usr/lib/moos/run-qemu.sh"),
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError("managed Personal is not staged: " + ", ".join(missing))
    launcher_help = subprocess.run(
        ["/usr/lib/moos/run-qemu.sh", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    if "--console-socket" not in launcher_help.stdout:
        raise RuntimeError("installed run-qemu.sh is stale; rerun setup-runtime-user.sh")


def main():
    preflight()
    daemon = None
    terminal = None
    started = False
    try:
        with tempfile.TemporaryDirectory(prefix="moos-m5-", dir="/run") as temporary:
            socket_path = Path(temporary) / "moosd.sock"
            daemon = start_daemon(socket_path)

            started = True
            started_response = request(socket_path, "personal.start", timeout=15)
            if not started_response.get("ok"):
                raise AssertionError(f"Personal start failed: {started_response}")
            terminal = open_terminal(socket_path)
            terminal_output_until(terminal, r"login:\s*", 60)
            terminal_input(terminal, "root\n")
            terminal_output_until(terminal, r"\n#\s*$", 15)
            terminal_input(
                terminal,
                "echo MOOS_INFO_BEGIN; moos-info; echo MOOS_INFO_END\n",
            )
            info = terminal_output_until(terminal, r"^MOOS_INFO_END$", 15)
            if "version: MOOS 0.1.0-dev" not in info or "kernel:" not in info:
                raise AssertionError("real moos-info output was incomplete")

            stop_daemon(daemon)
            daemon = None
            terminal.close()
            terminal = None
            if systemctl_state() != "active":
                raise AssertionError("Personal stopped when moosd restarted")

            daemon = start_daemon(socket_path)
            terminal = open_terminal(socket_path)
            terminal_input(
                terminal,
                "\necho MOOS_RECONNECT_BEGIN; moos-info; echo MOOS_RECONNECT_END\n",
            )
            reconnect = terminal_output_until(
                terminal, r"^MOOS_RECONNECT_END$", 15
            )
            if "version: MOOS 0.1.0-dev" not in reconnect:
                raise AssertionError("moos-info failed after daemon restart")

            terminal.send({"type": "close"})
            terminal.close()
            terminal = None
            stopped_response = request(socket_path, "personal.stop", timeout=15)
            if not stopped_response.get("ok"):
                raise AssertionError(f"Personal stop failed: {stopped_response}")
            started = False

        print("MOOS managed Personal M5 integration test: PASS")
        print("  actual managed QEMU start and MOOS login: ok")
        print("  real moos-info through framed terminal: ok")
        print("  moosd restart left guest running and terminal reconnected: ok")
        print("  managed stop and process reaping: ok")
        return 0
    finally:
        if terminal is not None:
            terminal.close()
        if started:
            subprocess.run(["systemctl", "stop", UNIT], check=False)
        if daemon is not None:
            stop_daemon(daemon)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, ConnectionError, OSError, RuntimeError, TimeoutError) as error:
        print("MOOS managed Personal M5 integration test: FAIL", file=sys.stderr)
        print(f"  {error}", file=sys.stderr)
        sys.exit(1)
