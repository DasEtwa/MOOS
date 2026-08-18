#!/usr/bin/env python3
"""Validate the Phase 3.1 runtime-account and cgroup command policies."""

import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SETUP = REPO_ROOT / "scripts" / "setup-runtime-user.sh"
STAGE = REPO_ROOT / "scripts" / "stage-instance.sh"
RUN_INSTANCE = REPO_ROOT / "scripts" / "run-instance.sh"
SETUP_CONTROL = REPO_ROOT / "scripts" / "setup-control-plane.sh"
SETUP_GATEWAY = REPO_ROOT / "scripts" / "setup-gateway.sh"


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

    with tempfile.TemporaryDirectory() as temporary:
        fixture_root = Path(temporary)
        image_dir = fixture_root / "images"
        qemu_bin = fixture_root / "qemu-host" / "bin" / "qemu-system-x86_64"
        image_dir.mkdir()
        qemu_bin.parent.mkdir(parents=True)
        (fixture_root / "qemu-host" / "lib").mkdir()
        (fixture_root / "qemu-host" / "share" / "qemu").mkdir(parents=True)
        (image_dir / "bzImage").touch()
        (image_dir / "rootfs.ext2").touch()
        qemu_bin.touch(mode=0o700)

        staged = run(
            STAGE,
            "--id",
            "luna",
            "--image-dir",
            str(image_dir),
            "--qemu",
            str(qemu_bin),
            "--dry-run",
        )
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
    require("reconnectable managed Unix socket", managed, "reconnectable console")

    control = run(SETUP_CONTROL, "--dry-run")
    require("control group: moos-control", control, "dedicated client group")
    require("daemon identity: root:moos-control", control, "daemon identity")
    require("mode 0660", control, "bounded socket access")
    require("restart on failure, journald logging", control, "operating model")
    require("sudo policy: none", control, "sudo boundary")
    require("host mutation: none (dry-run)", control, "control setup dry-run")

    gateway = run(
        SETUP_GATEWAY,
        "--tailscale-address",
        "100.64.0.1",
        "--port",
        "7411",
        "--dry-run",
    )
    require("gateway identity: moos-gateway:moos-gateway", gateway, "gateway identity")
    require("listen: 100.64.0.1:7411 (Tailscale-only)", gateway, "safe listener")
    require("root:moos-gateway, mode 0640", gateway, "device store ownership")
    require("remote grants: status only", gateway, "remote authorization")
    require("host mutation: none (dry-run)", gateway, "gateway dry-run")

    service_unit = (REPO_ROOT / "systemd/moosd.service").read_text()
    socket_unit = (REPO_ROOT / "systemd/moosd.socket").read_text()
    require("User=root", service_unit, "required systemd broker identity")
    require("Group=moos-control", service_unit, "restricted service group")
    require("CapabilityBoundingSet=", service_unit, "empty capability set")
    require("ProtectSystem=strict", service_unit, "read-only system tree")
    require("ListenStream=/run/moos/moosd.sock", socket_unit, "local-only socket")
    require("SocketGroup=moos-control", socket_unit, "client authorization group")

    gateway_unit = (REPO_ROOT / "systemd/moos-gateway.service").read_text()
    gateway_runner = (REPO_ROOT / "scripts/moos-gateway.py").read_text()
    require("User=moos-gateway", gateway_unit, "unprivileged gateway user")
    require("SupplementaryGroups=moos-control", gateway_unit, "local API access")
    require("RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6", gateway_unit, "network allowlist")
    require("RestrictNetworkInterfaces=tailscale0", gateway_unit, "Tailscale interface lock")
    require("IPAddressDeny=any", gateway_unit, "default network deny")
    require("IPAddressAllow=100.64.0.0/10", gateway_unit, "Tailscale IPv4 allowlist")
    require("CapabilityBoundingSet=", gateway_unit, "empty gateway capabilities")
    require("CPUQuota=50%", gateway_unit, "gateway CPU limit")
    require("validate_process_groups()", gateway_runner, "runtime group validation")

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
    run(STAGE, "--id", "a/escape", "--dry-run", expected=2)
    run(RUN_INSTANCE, "--id", "a/escape", "--dry-run", expected=2)
    run(RUN_INSTANCE, "--id", "luna", "--network", "bridge", "--dry-run", expected=2)
    run(RUN_INSTANCE, "--id", "luna", "--memory", "2049M", "--dry-run", expected=2)
    run(RUN_INSTANCE, "--id", "luna", "--cpus", "3", "--dry-run", expected=2)
    run(RUN_INSTANCE, "--id", "luna", "--io-read", "unlimited", "--dry-run", expected=2)
    run(
        SETUP_GATEWAY,
        "--tailscale-address",
        "0.0.0.0",
        "--dry-run",
        expected=2,
    )
    run(
        SETUP_GATEWAY,
        "--tailscale-address",
        "100.64.0.1",
        "--port",
        "0",
        "--dry-run",
        expected=2,
    )

    print("MOOS Phase 3.1 policy test: PASS")
    print("  dedicated nologin runtime account plan: ok")
    print("  private Instance staging plan: ok")
    print("  systemd CPU/RAM/PID/I/O policy: ok")
    print("  socket-activated moosd access, restart, and logging policy: ok")
    print("  Tailscale-only authenticated Gateway policy: ok")
    print("  invalid IDs, limits, network, and I/O values: rejected")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, OSError) as error:
        print("MOOS Phase 3.1 policy test: FAIL", file=sys.stderr)
        print(f"  {error}", file=sys.stderr)
        sys.exit(1)
