#!/usr/bin/env python3
"""Manage individually authorized MOOS Gateway devices."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_ROOT = REPO_ROOT / "host"
if not MODULE_ROOT.is_dir():
    MODULE_ROOT = Path("/usr/lib/moos")
sys.path.insert(0, str(MODULE_ROOT))

from moos_gateway_auth import (  # noqa: E402
    DeviceAdminStore,
    pairing_code,
)


DEFAULT_STORE = Path("/var/lib/moos-gateway/devices.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--devices", type=Path, default=DEFAULT_STORE)
    subparsers = parser.add_subparsers(dest="command", required=True)

    add = subparsers.add_parser("add", help="pair a new device")
    add.add_argument("--name", required=True)
    add.add_argument("--allow", action="append", choices=("status",), required=True)

    subparsers.add_parser("list", help="list devices without credentials")

    revoke = subparsers.add_parser("revoke", help="revoke one device")
    revoke.add_argument("device_id")

    rotate = subparsers.add_parser("rotate", help="rotate one device key")
    rotate.add_argument("device_id")

    permissions = subparsers.add_parser("permissions", help="replace grants")
    permissions.add_argument("device_id")
    permissions.add_argument(
        "--allow", action="append", choices=("status",), required=True
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if os.geteuid() != 0:
        print("error: device administration must run as root", file=sys.stderr)
        return 1
    store = DeviceAdminStore(args.devices)
    try:
        if args.command == "add":
            device = store.add(args.name, frozenset(args.allow))
            print(f"device ID: {device.device_id}")
            print(f"pairing code (shown once): {pairing_code(device)}")
            return 0
        if args.command == "list":
            for device in store.load().values():
                state = "revoked" if device.revoked else "authorized"
                grants = ",".join(sorted(device.permissions))
                print(f"{device.device_id}\t{state}\t{grants}\t{device.name}")
            return 0
        if args.command == "revoke":
            device = store.revoke(args.device_id)
            print(f"revoked: {device.device_id}")
            return 0
        if args.command == "rotate":
            device = store.rotate(args.device_id)
            print(f"rotated: {device.device_id}")
            print(f"pairing code (shown once): {pairing_code(device)}")
            return 0
        if args.command == "permissions":
            device = store.set_permissions(args.device_id, frozenset(args.allow))
            print(f"updated: {device.device_id}")
            return 0
    except (KeyError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
