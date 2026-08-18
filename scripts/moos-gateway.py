#!/usr/bin/env python3
"""Run the authenticated Tailscale MOOS Gateway."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_ROOT = REPO_ROOT / "host"
if not MODULE_ROOT.is_dir():
    MODULE_ROOT = Path("/usr/lib/moos")
sys.path.insert(0, str(MODULE_ROOT))

from moos_gateway import (  # noqa: E402
    serve,
    validate_process_groups,
    validate_tailscale_interface_address,
)
from moos_gateway_auth import DeviceStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-address", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument(
        "--devices", type=Path, default=Path("/var/lib/moos-gateway/devices.json")
    )
    parser.add_argument(
        "--moosd-socket", type=Path, default=Path("/run/moos/moosd.sock")
    )
    args = parser.parse_args()
    try:
        listen_address = validate_tailscale_interface_address(args.listen_address)
    except ValueError as error:
        parser.error(str(error))
    if not 1 <= args.port <= 65535:
        parser.error("--port must be from 1 to 65535")
    try:
        validate_process_groups()
    except (KeyError, ValueError) as error:
        parser.error(str(error))

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    serve(
        listen_address,
        args.port,
        DeviceStore(args.devices, expected_uid=0, expected_gid=os.getgid()),
        args.moosd_socket,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
