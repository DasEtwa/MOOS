#!/usr/bin/env python3
"""Validate Rust Gateway artifacts and least-privilege integration boundaries."""

import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
GATEWAY = REPO_ROOT / "target" / "release" / "moos-gateway"
DEVICE_ADMIN = REPO_ROOT / "target" / "release" / "moos-gateway-device"
SETUP = REPO_ROOT / "scripts" / "setup-gateway.sh"


def run(command, *arguments, expected=0):
    result = subprocess.run(
        [str(command), *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != expected:
        raise AssertionError(
            f"{Path(command).name} returned {result.returncode}, expected {expected}: "
            f"{result.stdout}{result.stderr}"
        )
    return result.stdout + result.stderr


def require(needle, value, description):
    if needle not in value:
        raise AssertionError(f"missing {description}: {needle!r}")


def check_flock_serializes_concurrent_invocations():
    flock = shutil.which("flock")
    if flock is None:
        raise AssertionError("flock is required for the installer lock test")
    with tempfile.TemporaryDirectory() as temporary:
        lock = Path(temporary) / "gateway-install.lock"
        holder = subprocess.Popen(
            [flock, "-n", str(lock), "sleep", "1"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline and not lock.exists():
                time.sleep(0.01)
            if holder.poll() is not None:
                raise AssertionError("lock holder exited before acquiring the lock")
            contender = subprocess.run(
                [flock, "-w", "0.1", str(lock), "true"],
                capture_output=True,
                check=False,
            )
            if contender.returncode == 0:
                raise AssertionError("concurrent flock invocation was not serialized")
        finally:
            holder.terminate()
            holder.wait(timeout=2)


def main():
    for binary in (GATEWAY, DEVICE_ADMIN):
        if binary.read_bytes()[:4] != b"\x7fELF":
            raise AssertionError(f"release output is not a Linux ELF binary: {binary}")
        metadata = binary.stat()
        if stat.S_IMODE(metadata.st_mode) != 0o755 or metadata.st_nlink != 1:
            raise AssertionError(f"release artifact mode/link policy failed: {binary}")

    gateway_version = run(GATEWAY, "--version").strip()
    device_version = run(DEVICE_ADMIN, "--version").strip()
    require("moos-gateway ", gateway_version, "Gateway version identity")
    version = gateway_version.removeprefix("moos-gateway ")
    if device_version != f"moos-gateway-device {version}":
        raise AssertionError("release binary versions do not match")

    for address in ("100.64.0.0", "100.127.255.255", "fd7a:115c:a1e0::1"):
        run(
            GATEWAY,
            "validate-address",
            "--listen-address",
            address,
            "--port",
            "7411",
        )
    for address in ("0.0.0.0", "127.0.0.1", "192.168.1.4", "tailscale0"):
        run(
            GATEWAY,
            "validate-address",
            "--listen-address",
            address,
            "--port",
            "7411",
            expected=2,
        )

    dry_run = run(
        SETUP,
        "--tailscale-address",
        "100.64.0.1",
        "--port",
        "7411",
        "--dry-run",
    )
    require("Tailscale-only", dry_run, "Tailscale-only install plan")
    require("remote grants: status only", dry_run, "status-only authorization")
    require("prebuilt Rust artifacts", dry_run, "Rust runtime plan")
    require("root-owned staging", dry_run, "privileged staging boundary")
    require("Cargo hardlinks", dry_run, "normal Cargo artifact support")
    require("host mutation: none", dry_run, "non-mutating dry run")

    with tempfile.TemporaryDirectory() as temporary:
        binary_root = Path(temporary)
        for name, source in (
            ("moos-gateway", GATEWAY),
            ("moos-gateway-device", DEVICE_ADMIN),
        ):
            binary = binary_root / name
            shutil.copy2(source, binary)
            binary.chmod(0o775)
            (binary_root / f"{name}.cargo-deps").hardlink_to(binary)
        run(
            SETUP,
            "--tailscale-address",
            "100.64.0.1",
            "--binary-dir",
            str(binary_root),
            "--dry-run",
        )

    with tempfile.TemporaryDirectory() as temporary:
        binary_root = Path(temporary)
        for name in ("moos-gateway", "moos-gateway-device"):
            binary = binary_root / name
            binary.write_text("not an executable\n", encoding="utf-8")
            binary.chmod(0o755)
        run(
            SETUP,
            "--tailscale-address",
            "100.64.0.1",
            "--binary-dir",
            str(binary_root),
            "--dry-run",
            expected=1,
        )

    service = (REPO_ROOT / "systemd" / "moos-gateway.service").read_text(
        encoding="utf-8"
    )
    for policy in (
        "User=moos-gateway",
        "SupplementaryGroups=moos-control",
        "ExecStart=/usr/lib/moos/moos-gateway",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
        "RestrictNetworkInterfaces=tailscale0",
        "IPAddressDeny=any",
        "IPAddressAllow=100.64.0.0/10",
        "IPAddressAllow=fd7a:115c:a1e0::/48",
        "CapabilityBoundingSet=",
        "NoNewPrivileges=yes",
    ):
        require(policy, service, "systemd Gateway boundary")

    platform = (
        REPO_ROOT / "host" / "moos-gateway" / "src" / "platform_linux.rs"
    ).read_text(encoding="utf-8")
    require("BindToDevice", platform, "tailscale0 socket binding")
    if "getifaddrs" in platform or "AF_NETLINK" in platform:
        raise AssertionError("Gateway interface validation requires forbidden Netlink")

    setup = SETUP.read_text(encoding="utf-8")
    if "cargo build" in setup or "python3" in setup:
        raise AssertionError("root installer builds code or retains a Python runtime path")
    require("INSTALL_LOCK='/run/moos/gateway-install.lock'", setup, "global installer lock path")
    require("flock -w \"$INSTALL_LOCK_TIMEOUT\" 9", setup, "bounded global installer lock")
    lock_position = setup.index("flock -w \"$INSTALL_LOCK_TIMEOUT\" 9")
    mutation_position = setup.index("useradd --system")
    if lock_position > mutation_position:
        raise AssertionError("Gateway installer acquires its lock after mutating account state")
    for lock_policy in (
        "trusted control-plane runtime directory has unsafe ownership or mode",
        "Gateway installer lock has unsafe ownership, mode, or link count",
        "another Gateway setup is active (lock timeout",
    ):
        require(lock_policy, setup, "safe concurrent installer policy")
    check_flock_serializes_concurrent_invocations()
    require(
        "existing gateway device store has unsafe ownership or mode",
        setup,
        "strict existing-store validation",
    )
    if 'chown root:"$GATEWAY_GROUP" "$STATE_ROOT/devices.json"' in setup:
        raise AssertionError("installer silently repairs an existing secret store")
    root_boundary = setup.index('[ "$(id -u)" -eq 0 ]')
    before_root_boundary = setup[:root_boundary]
    if (
        "--version" in before_root_boundary
        or "validate-address" in before_root_boundary
        or '\n"$BINARY_ROOT/' in before_root_boundary
    ):
        raise AssertionError("installer executes checkout artifacts before root-owned staging")
    for policy in (
        "run_staged_validation",
        "PrivatePIDs=yes",
        'InaccessiblePaths=$STATE_ROOT -/run/moos',
        '"$STAGED_GATEWAY" check-config',
        "systemd unit does not match the reviewed policy",
        "systemctl restart moos-gateway.service || rollback_failed=1",
        "systemctl stop moos-gateway.service || rollback_failed=1",
        "rollback files were preserved for recovery",
    ):
        require(policy, setup, "staged installer validation")
    for retired in (
        REPO_ROOT / "host" / "moos_gateway.py",
        REPO_ROOT / "host" / "moos_gateway_auth.py",
        REPO_ROOT / "scripts" / "moos-gateway.py",
        REPO_ROOT / "scripts" / "moos-gateway-device.py",
    ):
        if retired.exists():
            raise AssertionError(f"retired Python Gateway path still exists: {retired}")

    print("MOOS Rust Gateway integration test: PASS")
    print("  matching release artifacts and strict installer validation: ok")
    print("  Tailscale-only systemd sandbox and socket binding: ok")
    print("  global installer lock serializes concurrent invocations: ok")
    print("  installer consumes prebuilt binaries and has rollback path: ok")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, OSError, ValueError) as error:
        print("MOOS Rust Gateway integration test: FAIL", file=sys.stderr)
        print(f"  {error}", file=sys.stderr)
        sys.exit(1)
