#!/usr/bin/env python3
"""Unit-test Personal lifecycle/status behavior without changing the host."""

import os
import socket
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "host"))

from moos_runtime import (  # noqa: E402
    AlreadyRunning,
    NotRunning,
    PersonalRuntime,
    RuntimeErrorBase,
    RuntimeState,
    TerminalBusy,
    UnsupportedOperation,
)


class FakeRunner:
    def __init__(self):
        self.load = "not-found"
        self.active = "inactive"
        self.substate = "dead"
        self.result = "success"
        self.calls = []
        self.fail_show = False
        self.fail_launch = False
        self.fail_stop = False

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[:3] == ["systemctl", "show", "moos-instance-personal.service"]:
            if self.fail_show:
                return subprocess.CompletedProcess(argv, 1, "", "manager unavailable")
            output = (
                f"LoadState={self.load}\n"
                f"ActiveState={self.active}\n"
                f"SubState={self.substate}\n"
                f"Result={self.result}\n"
            )
            return subprocess.CompletedProcess(argv, 0, output, "")
        if argv[:2] == ["systemctl", "reset-failed"]:
            self.load = "not-found"
            self.active = "inactive"
            self.substate = "dead"
            self.result = "success"
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:2] == ["systemctl", "stop"]:
            if self.fail_stop:
                return subprocess.CompletedProcess(argv, 1, "", "stop denied")
            self.load = "not-found"
            self.active = "inactive"
            self.substate = "dead"
            self.result = "success"
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv == [str(REPO_ROOT / "scripts/run-instance.sh"), "--id", "personal"]:
            if self.fail_launch:
                return subprocess.CompletedProcess(argv, 1, "", "launch failed")
            self.load = "loaded"
            self.active = "active"
            self.substate = "running"
            self.result = "success"
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(f"unexpected command: {argv}")


def set_state(runner, load, active, substate, result="success"):
    runner.load = load
    runner.active = active
    runner.substate = substate
    runner.result = result


def expect_error(error_type, callback, message):
    try:
        callback()
    except error_type:
        return
    raise AssertionError(message)


def main():
    with tempfile.TemporaryDirectory() as temporary:
        console_path = Path(temporary) / "console.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as console_listener:
            console_listener.bind(str(console_path))
            console_listener.listen(4)

            runner = FakeRunner()
            runtime = PersonalRuntime(
                REPO_ROOT,
                runner=runner,
                console_socket=console_path,
                expected_console_uid=os.getuid(),
            )

            assert runtime.status().state == RuntimeState.STOPPED
            expect_error(
                NotRunning,
                runtime.stop,
                "stopping an inactive Personal must be rejected",
            )

            assert runtime.start().state == RuntimeState.RUNNING
            assert runner.calls.count(
                [str(REPO_ROOT / "scripts/run-instance.sh"), "--id", "personal"]
            ) == 1
            assert runtime.serial_connection().available is True

            expect_error(
                AlreadyRunning,
                runtime.start,
                "starting a running Personal must be rejected",
            )

            channel = runtime.open_terminal()
            accepted, _ = console_listener.accept()
            expect_error(
                TerminalBusy,
                runtime.open_terminal,
                "a second terminal client must be rejected",
            )
            channel.close()
            accepted.close()

            runner.fail_stop = True
            expect_error(
                RuntimeErrorBase,
                runtime.stop,
                "failed systemctl stop must be reported",
            )
            assert runtime.status().state == RuntimeState.RUNNING
            restarted_runtime = PersonalRuntime(
                REPO_ROOT,
                runner=runner,
                console_socket=console_path,
                expected_console_uid=os.getuid(),
            )
            reconnect = restarted_runtime.open_terminal()
            accepted, _ = console_listener.accept()
            reconnect.close()
            accepted.close()

            symlink_path = Path(temporary) / "symlink-console.sock"
            symlink_path.symlink_to(console_path)
            symlink_runtime = PersonalRuntime(
                REPO_ROOT,
                runner=runner,
                console_socket=symlink_path,
                expected_console_uid=os.getuid(),
            )
            expect_error(
                RuntimeErrorBase,
                symlink_runtime.open_terminal,
                "a runtime-controlled console symlink must be rejected",
            )

            class WrongPeer:
                def getsockopt(self, *_args):
                    return struct.pack("3i", 123, os.getuid() + 1, os.getgid())

            expect_error(
                RuntimeErrorBase,
                lambda: runtime._validate_console_peer(WrongPeer()),
                "a console server with the wrong peer UID must be rejected",
            )

            runner.fail_stop = False
            assert runtime.stop().state == RuntimeState.STOPPED

            state_cases = [
                (("loaded", "activating", "start-pre", "success"), RuntimeState.STARTING),
                (("loaded", "active", "running", "success"), RuntimeState.RUNNING),
                (("loaded", "deactivating", "stop-sigterm", "success"), RuntimeState.STOPPING),
                (("loaded", "inactive", "dead", "success"), RuntimeState.STOPPED),
                (("loaded", "failed", "failed", "exit-code"), RuntimeState.FAILED),
                (("loaded", "active", "exited", "success"), RuntimeState.UNKNOWN),
            ]
            for properties, expected in state_cases:
                set_state(runner, *properties)
                status = runtime.status()
                assert status.state == expected, (properties, status)
            assert runtime.status().result == "success"

            runner.fail_show = True
            unknown = runtime.status()
            assert unknown.state == RuntimeState.UNKNOWN
            assert unknown.result == "status-unavailable"
            expect_error(
                RuntimeErrorBase,
                runtime.start,
                "unknown state must not risk a duplicate launch",
            )
            runner.fail_show = False

            set_state(runner, "not-found", "inactive", "dead")
            runner.fail_launch = True
            expect_error(
                RuntimeErrorBase,
                runtime.start,
                "launcher failure must be detected",
            )

            expect_error(
                UnsupportedOperation,
                runtime.reboot,
                "reboot must remain unavailable",
            )

    print("MOOS Personal runtime control test: PASS")
    print("  structured starting/running/stopping/stopped/failed/unknown states: ok")
    print("  synchronous launch failure and duplicate start detection: ok")
    print("  failed stop and new runtime object preserve terminal reconnectability: ok")
    print("  terminal exclusivity and reconnect after disconnect: ok")
    print("  symlinked console paths and wrong peer UIDs: rejected")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, OSError) as error:
        print("MOOS Personal runtime control test: FAIL", file=sys.stderr)
        print(f"  {error}", file=sys.stderr)
        sys.exit(1)
