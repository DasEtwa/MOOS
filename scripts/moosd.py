#!/usr/bin/env python3
"""Run the local MOOS control daemon."""

import argparse
import logging
import os
import socket
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_ROOT = REPO_ROOT / "host"
if not MODULE_ROOT.is_dir():
    MODULE_ROOT = Path("/usr/lib/moos")
sys.path.insert(0, str(MODULE_ROOT))

from moos_runtime import PersonalRuntime  # noqa: E402
from moosd import MoosdService, serve  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=Path, default=Path("/run/moos/moosd.sock"))
    parser.add_argument("--runner", type=Path)
    parser.add_argument(
        "--systemd-socket",
        action="store_true",
        help="accept connections from the systemd socket passed as fd 3",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    listener = None
    if args.systemd_socket:
        if os.environ.get("LISTEN_PID") != str(os.getpid()) or os.environ.get(
            "LISTEN_FDS"
        ) != "1":
            parser.error("exactly one systemd socket is required")
        listener = socket.socket(fileno=3)

    runtime = PersonalRuntime(REPO_ROOT, run_instance=args.runner)
    serve(args.socket, MoosdService(runtime), listener=listener)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
