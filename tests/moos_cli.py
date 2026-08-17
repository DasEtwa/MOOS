#!/usr/bin/env python3
"""Test the local CLI against a temporary typed moosd socket."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "host"))

from moos_runtime import RuntimeState, RuntimeStatus  # noqa: E402
from moos_protocol import encode_frame  # noqa: E402
from moosd import MoosdService, serve  # noqa: E402


class FakeRuntime:
    def status(self):
        return RuntimeStatus("personal", RuntimeState.RUNNING)

    def start(self):
        return RuntimeStatus("personal", RuntimeState.STARTING)

    def stop(self):
        return RuntimeStatus("personal", RuntimeState.STOPPED)

    def open_terminal(self):
        return FakeChannel()


class FakeChannel:
    def __init__(self):
        self.fd, self.write_fd = os.pipe()

    def fileno(self):
        return self.fd

    def read(self, size=4096):
        return b""

    def write(self, data):
        return os.write(self.write_fd, data)

    def close(self):
        os.close(self.fd)
        os.close(self.write_fd)


def run_cli(socket_path, *arguments, input_text=None):
    return subprocess.run(
        [str(REPO_ROOT / "scripts/moos"), "--socket", str(socket_path), *arguments],
        cwd=REPO_ROOT, capture_output=True, text=True, input=input_text, check=False,
    )


def main():
    with tempfile.TemporaryDirectory() as temporary:
        socket_path = Path(temporary) / "moosd.sock"
        thread = threading.Thread(target=serve, args=(socket_path, MoosdService(FakeRuntime())), daemon=True)
        thread.start()
        deadline = time.monotonic() + 2
        while not socket_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)

        status = run_cli(socket_path, "status")
        assert status.returncode == 0, status.stderr
        assert '"identity": "personal"' in status.stdout

        start = run_cli(socket_path, "personal", "start")
        assert start.returncode == 0, start.stderr
        stop = run_cli(socket_path, "personal", "stop")
        assert stop.returncode == 0, stop.stderr

        terminal = run_cli(socket_path, "personal", "terminal", input_text="")
        assert terminal.returncode == 0, terminal.stderr

        direct_qemu = REPO_ROOT / "scripts" / "run-qemu.sh"
        assert "run-qemu" not in Path(REPO_ROOT / "scripts/moos").read_text()
        assert direct_qemu.exists()

        fragmented_path = Path(temporary) / "fragmented.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(fragmented_path))
            listener.listen(1)

            def send_fragmented_response():
                connection, _ = listener.accept()
                with connection:
                    connection.recv(4096)
                    frame = encode_frame(
                        {
                            "protocolVersion": 1,
                            "ok": True,
                            "operation": "status",
                            "data": {
                                "personal": {
                                    "identity": "personal",
                                    "state": "running",
                                    "result": "success",
                                }
                            },
                            "events": [],
                        }
                    )
                    for byte in frame:
                        connection.sendall(bytes([byte]))

            sender = threading.Thread(target=send_fragmented_response, daemon=True)
            sender.start()
            fragmented = run_cli(fragmented_path, "status")
            assert fragmented.returncode == 0, fragmented.stderr

        combined_path = Path(temporary) / "combined.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(combined_path))
            listener.listen(1)

            def send_combined_terminal_frames():
                connection, _ = listener.accept()
                with connection:
                    connection.recv(4096)
                    connection.sendall(
                        encode_frame(
                            {
                                "protocolVersion": 1,
                                "ok": True,
                                "operation": "personal.terminal.open",
                                "data": {"channel": "serial-console"},
                                "events": [],
                            }
                        )
                        + encode_frame({"type": "output", "data": "FIRST_OUTPUT\n"})
                    )
                    connection.recv(4096)

            sender = threading.Thread(
                target=send_combined_terminal_frames, daemon=True
            )
            sender.start()
            combined = run_cli(combined_path, "personal", "terminal", input_text="")
            assert combined.returncode == 0, combined.stderr
            assert "FIRST_OUTPUT" in combined.stdout

    print("MOOS local CLI test: PASS")
    print("  status/start/stop use moosd: ok")
    print("  terminal command opens the daemon channel: ok")
    print("  fragmented response and combined terminal ACK/output: ok")
    print("  CLI contains no QEMU lifecycle logic: ok")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, OSError) as error:
        print("MOOS local CLI test: FAIL", file=sys.stderr)
        print(f"  {error}", file=sys.stderr)
        sys.exit(1)
