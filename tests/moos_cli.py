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

    print("MOOS local CLI test: PASS")
    print("  status/start/stop use moosd: ok")
    print("  terminal command opens the daemon channel: ok")
    print("  CLI contains no QEMU lifecycle logic: ok")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, OSError) as error:
        print("MOOS local CLI test: FAIL", file=sys.stderr)
        print(f"  {error}", file=sys.stderr)
        sys.exit(1)
