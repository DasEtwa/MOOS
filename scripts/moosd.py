#!/usr/bin/env python3
"""Run the local MOOS control daemon."""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "host"))

from moos_runtime import PersonalRuntime  # noqa: E402
from moosd import MoosdService, serve  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=Path, default=Path("/run/moos/moosd.sock"))
    args = parser.parse_args()
    serve(args.socket, MoosdService(PersonalRuntime(REPO_ROOT)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
