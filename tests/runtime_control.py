#!/usr/bin/env python3
"""Unit-test the Personal lifecycle adapter without changing the host."""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "host"))

from moos_runtime import (  # noqa: E402
    AlreadyRunning,
    NotRunning,
    PersonalRuntime,
    RuntimeState,
    UnsupportedOperation,
)


class FakeRunner:
    def __init__(self, active="inactive"):
        self.active = active
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[:3] == ["systemctl", "show", "moos-instance-personal.service"]:
            return subprocess.CompletedProcess(argv, 0, self.active + "\n", "")
        if argv[:2] == ["systemctl", "stop"]:
            self.active = "inactive"
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(f"unexpected command: {argv}")


class FakePopen:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        return object()


def main():
    runner = FakeRunner()
    popen = FakePopen()
    runtime = PersonalRuntime(REPO_ROOT, runner=runner, popen=popen)

    assert runtime.status().state == RuntimeState.STOPPED
    try:
        runtime.stop()
    except NotRunning:
        pass
    else:
        raise AssertionError("stopping an inactive Personal must be rejected")

    runtime.start()
    assert popen.calls[0][0] == [str(REPO_ROOT / "scripts/run-instance.sh"), "--id", "personal"]
    runner.active = "active"
    assert runtime.status().state == RuntimeState.RUNNING
    assert runtime.serial_connection().kind == "serial-console"
    assert runtime.serial_connection().available is True
    assert runtime.serial_connection().interactive is False

    try:
        runtime.start()
    except AlreadyRunning:
        pass
    else:
        raise AssertionError("starting a running Personal must be rejected")

    runtime.stop()
    assert runtime.status().state == RuntimeState.STOPPED
    try:
        runtime.reboot()
    except UnsupportedOperation:
        pass
    else:
        raise AssertionError("reboot must stay unavailable before terminal bridge")

    print("MOOS Personal runtime control test: PASS")
    print("  stopped -> start -> running -> stop: ok")
    print("  duplicate start and inactive stop: rejected")
    print("  serial connection does not expose a host PTY path: ok")
    print("  reboot remains unavailable before M5: ok")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, OSError) as error:
        print("MOOS Personal runtime control test: FAIL", file=sys.stderr)
        print(f"  {error}", file=sys.stderr)
        sys.exit(1)
