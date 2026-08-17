#!/usr/bin/env python3
"""Validate the Phase 3.1 runtime-account and cgroup command policies."""

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SETUP = REPO_ROOT / "scripts" / "setup-runtime-user.sh"
STAGE = REPO_ROOT / "scripts" / "stage-instance.sh"
RUN_INSTANCE = REPO_ROOT / "scripts" / "run-instance.sh"


def run(script, *arguments, expected=0):
    result = subprocess.run(
        [str(script), *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != expected:
        raise AssertionError(
            f"{script.name} returned {result.returncode}, expected {expected}: "
            f"{result.stdout}{result.stderr}"
        )
    return result.stdout + result.stderr


def require(needle, output, description):
    if needle not in output:
        raise AssertionError(f"missing {description}: {needle!r}")


def main():
    setup = run(SETUP, "--dry-run")
    require("runtime user: moos-runtime", setup, "dedicated runtime user")
    require("login shell: ", setup, "nologin shell")
    require("account state: locked, no supplementary groups", setup, "account lock")
    require("host mutation: none (dry-run)", setup, "setup dry-run")

    staged = run(STAGE, "--id", "luna", "--dry-run")
    require("instance: luna", staged, "Instance ID")
    require("root:moos-runtime, mode 0750", staged, "root-owned Instance storage")
    require("mode 0440", staged, "read-only staged images")
    require("host mutation: none (dry-run)", staged, "staging dry-run")

    managed = run(RUN_INSTANCE, "--id", "luna", "--dry-run")
    require("runtime user: moos-runtime:moos-runtime", managed, "runtime identity")
    require("cgroup CPUQuota: 200%", managed, "CPU cgroup limit")
    require("cgroup MemoryMax: 2G", managed, "memory cgroup limit")
    require("cgroup TasksMax: 512", managed, "PID/task cgroup limit")
    require("host home: protected", managed, "home protection")
    require("host processes: hidden", managed, "process protection")
    require("host devices: private", managed, "device protection")
    require("arbitrary host command API: none", managed, "command boundary")

    managed_io = run(
        RUN_INSTANCE,
        "--id",
        "luna",
        "--io-device",
        "/dev/sda1",
        "--network",
        "user",
        "--dry-run",
    )
    require("/dev/sda1 (10M read / 10M write)", managed_io, "explicit I/O policy")
    require("network: user", managed_io, "explicit network policy")

    run(STAGE, "--id", "../escape", "--dry-run", expected=2)
    run(RUN_INSTANCE, "--id", "luna", "--network", "bridge", "--dry-run", expected=2)
    run(RUN_INSTANCE, "--id", "luna", "--memory", "2049M", "--dry-run", expected=2)
    run(RUN_INSTANCE, "--id", "luna", "--cpus", "3", "--dry-run", expected=2)
    run(RUN_INSTANCE, "--id", "luna", "--io-read", "unlimited", "--dry-run", expected=2)

    print("MOOS Phase 3.1 policy test: PASS")
    print("  dedicated nologin runtime account plan: ok")
    print("  private Instance staging plan: ok")
    print("  systemd CPU/RAM/PID/I/O policy: ok")
    print("  invalid IDs, limits, network, and I/O values: rejected")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, OSError) as error:
        print("MOOS Phase 3.1 policy test: FAIL", file=sys.stderr)
        print(f"  {error}", file=sys.stderr)
        sys.exit(1)
