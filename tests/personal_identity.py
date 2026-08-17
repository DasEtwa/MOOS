#!/usr/bin/env python3
"""Check the stable Personal MOOS identity contract."""

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL = REPO_ROOT / "scripts" / "personal-instance.sh"


def run(*arguments, expected=0):
    result = subprocess.run(
        [str(TOOL), *arguments], cwd=REPO_ROOT,
        capture_output=True, text=True, check=False,
    )
    if result.returncode != expected:
        raise AssertionError(
            f"{TOOL.name} returned {result.returncode}, expected {expected}: "
            f"{result.stdout}{result.stderr}"
        )
    return result.stdout


def main():
    assert run("id") == "personal\n"
    assert run("display-name") == "Personal MOOS\n"
    assert run("role") == "personal\n"
    assert run("all") == (
        "id: personal\n"
        "display-name: Personal MOOS\n"
        "role: personal\n"
    )
    run("unknown", expected=2)
    print("MOOS Personal identity test: PASS")
    print("  stable id: personal")
    print("  display name remains separate: ok")
    print("  no multi-instance enumeration or lifecycle: ok")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, OSError) as error:
        print("MOOS Personal identity test: FAIL", file=sys.stderr)
        print(f"  {error}", file=sys.stderr)
        sys.exit(1)
