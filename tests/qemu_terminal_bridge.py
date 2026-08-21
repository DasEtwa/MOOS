#!/usr/bin/env python3
"""Boot real QEMU and verify moosd terminal reconnect without host mutation."""

import multiprocessing
import os
import re
import select
import socket
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "host"))

from moos_protocol import FrameDecoder, encode_frame  # noqa: E402
from moos_runtime import PersonalRuntime  # noqa: E402
from moosd import MoosdService, serve  # noqa: E402


CONSOLE = Path("/run/moos-instances/personal/console.sock")
CONTROL = Path("/run/moos-terminal-test/moosd.sock")
ANSI_ESCAPE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))"
)


class ActiveSystemdRunner:
    def __call__(self, argv, **kwargs):
        if argv[:3] != ["systemctl", "show", "moos-instance-personal.service"]:
            raise AssertionError(f"unexpected lifecycle command: {argv}")
        return subprocess.CompletedProcess(
            argv,
            0,
            "LoadState=loaded\nActiveState=active\nSubState=running\nResult=success\n",
            "",
        )


def daemon_main():
    runtime = PersonalRuntime(
        REPO_ROOT,
        runner=ActiveSystemdRunner(),
        console_socket=CONSOLE,
        expected_console_uid=os.getuid(),
    )
    serve(CONTROL, MoosdService(runtime))


class TerminalClient:
    def __init__(self):
        self.connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.connection.connect(str(CONTROL))
        self.decoder = FrameDecoder()
        self.pending = deque()
        self.connection.sendall(
            encode_frame(
                {
                    "protocolVersion": 1,
                    "operation": "personal.terminal.open",
                }
            )
        )
        acknowledgment = self.receive(5)
        if not acknowledgment.get("ok"):
            raise AssertionError(f"terminal open failed: {acknowledgment}")

    def receive(self, timeout):
        deadline = time.monotonic() + timeout
        while True:
            while self.pending:
                result = self.pending.popleft()
                if result.error is not None or result.frame is None:
                    raise AssertionError("invalid daemon frame")
                return result.frame
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for terminal frame")
            readable, _, _ = select.select([self.connection], [], [], remaining)
            if not readable:
                raise TimeoutError("timed out waiting for terminal frame")
            data = self.connection.recv(4096)
            if not data:
                raise ConnectionError("daemon closed the terminal")
            self.pending.extend(self.decoder.feed(data))

    def send(self, data):
        self.connection.sendall(encode_frame({"type": "input", "data": data}))

    def read_until(self, pattern, timeout):
        expression = re.compile(pattern, re.MULTILINE)
        output = ""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            frame = self.receive(max(0.01, deadline - time.monotonic()))
            if frame.get("type") != "output" or not isinstance(frame.get("data"), str):
                raise AssertionError(f"unexpected terminal frame: {frame}")
            output += ANSI_ESCAPE.sub("", frame["data"]).replace("\r", "")
            if expression.search(output):
                return output
        raise TimeoutError(f"timed out waiting for {pattern}")

    def close(self):
        self.connection.close()


def start_daemon():
    CONTROL.unlink(missing_ok=True)
    process = multiprocessing.get_context("fork").Process(target=daemon_main)
    process.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if CONTROL.exists():
            return process
        if not process.is_alive():
            process.join()
            raise RuntimeError("test moosd exited during startup")
        time.sleep(0.02)
    process.terminate()
    process.join(5)
    raise TimeoutError("timed out waiting for test moosd")


def stop_daemon(process):
    if process.is_alive():
        process.terminate()
    process.join(5)
    if process.is_alive():
        process.kill()
        process.join(5)
    if process.exitcode is None:
        raise AssertionError("test moosd was not reaped")


def run_inside_namespace():
    CONTROL.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    qemu = subprocess.Popen(
        [
            str(REPO_ROOT / "scripts/run-qemu.sh"),
            "--direct",
            "--serial-only",
            "--network",
            "none",
            "--console-socket",
            str(CONSOLE),
        ],
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    daemon = None
    terminal = None
    try:
        deadline = time.monotonic() + 5
        while not CONSOLE.exists() and time.monotonic() < deadline:
            if qemu.poll() is not None:
                raise RuntimeError("QEMU startup failed: " + (qemu.stderr.read() or ""))
            time.sleep(0.02)
        if not CONSOLE.exists():
            raise TimeoutError("QEMU console socket did not appear")

        daemon = start_daemon()
        terminal = TerminalClient()
        terminal.read_until(r"login:\s*", 45)
        terminal.send("root\n")
        terminal.read_until(r"\n#\s*$", 15)
        terminal.send("echo MOOS_INFO_BEGIN; moos-info; echo MOOS_INFO_END\n")
        info = terminal.read_until(r"^MOOS_INFO_END$", 15)
        if "version: MOOS 0.1.0-dev" not in info or "kernel:" not in info:
            raise AssertionError("real moos-info output was incomplete")

        stop_daemon(daemon)
        daemon = None
        terminal.close()
        terminal = None
        if qemu.poll() is not None:
            raise AssertionError("QEMU exited when moosd stopped")

        daemon = start_daemon()
        terminal = TerminalClient()
        terminal.send(
            "\necho MOOS_RECONNECT_BEGIN; moos-info; echo MOOS_RECONNECT_END\n"
        )
        reconnect = terminal.read_until(r"^MOOS_RECONNECT_END$", 15)
        if "version: MOOS 0.1.0-dev" not in reconnect:
            raise AssertionError("real moos-info failed after daemon restart")

        terminal.send("poweroff\n")
        qemu.wait(timeout=15)
        if qemu.returncode != 0:
            raise AssertionError(f"QEMU exited with status {qemu.returncode}")

        print("MOOS real QEMU terminal bridge test: PASS")
        print("  real login and moos-info through framed moosd terminal: ok")
        print("  daemon process restart/reap while QEMU stayed running: ok")
        print("  terminal reconnect and second real moos-info: ok")
        print("  guest poweroff and QEMU reap: ok")
        return 0
    finally:
        if terminal is not None:
            terminal.close()
        if daemon is not None:
            stop_daemon(daemon)
        if qemu.poll() is None:
            qemu.terminate()
            try:
                qemu.wait(timeout=5)
            except subprocess.TimeoutExpired:
                qemu.kill()
                qemu.wait(timeout=5)
        if qemu.stderr is not None:
            qemu.stderr.close()


def main():
    if os.environ.get("MOOS_TERMINAL_TEST_NAMESPACE") == "1":
        return run_inside_namespace()
    if not (REPO_ROOT / "output/images/bzImage").exists():
        raise RuntimeError("MOOS images are missing; run scripts/build.sh first")
    environment = os.environ.copy()
    environment["MOOS_TERMINAL_TEST_NAMESPACE"] = "1"
    command = [
        "bwrap",
        "--unshare-user",
        "--unshare-pid",
        "--unshare-net",
        "--die-with-parent",
        "--new-session",
        "--ro-bind",
        "/",
        "/",
        "--ro-bind",
        str(REPO_ROOT),
        "/mnt",
        "--dev",
        "/dev",
        "--tmpfs",
        "/run",
        "--tmpfs",
        "/tmp",
        "--tmpfs",
        "/var/tmp",
        "--dir",
        "/run/moos-instances",
        "--dir",
        "/run/moos-instances/personal",
        sys.executable,
        "/mnt/tests/qemu_terminal_bridge.py",
    ]
    return subprocess.run(command, cwd=REPO_ROOT, env=environment, check=False).returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, ConnectionError, OSError, RuntimeError, TimeoutError) as error:
        print("MOOS real QEMU terminal bridge test: FAIL", file=sys.stderr)
        print(f"  {error}", file=sys.stderr)
        sys.exit(1)
