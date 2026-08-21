#!/usr/bin/env python3
"""Boot the release image and prove password-based root login is locked."""

from __future__ import annotations

import re
import sys

import qemu_smoke


def main() -> int:
    session = None
    try:
        qemu_smoke.TEST_NETWORK_MODE = "none"
        session = qemu_smoke.QemuSession()
        boot = session.read_until(r"login:\s*", 60)
        qemu_smoke.require(r"Linux version 6\.18\.43", boot, "Linux 6.18.43 boot")

        session.send("root\n")
        password_prompt = session.read_until(r"Password:\s*", 10)
        session.send("\n")
        denial = session.read_until(r"login:\s*", 10)
        transcript = password_prompt + denial
        if re.search(r"\n#\s*$", transcript, re.MULTILINE):
            raise AssertionError("release image opened a root shell")
        qemu_smoke.require(r"login:\s*", denial, "login prompt after rejection")

        print("MOOS release QEMU smoke test: PASS")
        print("  Linux 6.18.43 boot: ok")
        print("  blank-password root login: rejected")
        return 0
    except (AssertionError, OSError, RuntimeError, TimeoutError) as error:
        print("MOOS release QEMU smoke test: FAIL", file=sys.stderr)
        print("  " + str(error), file=sys.stderr)
        if session is not None:
            print("  console tail:", file=sys.stderr)
            print(session.output[-3000:], file=sys.stderr)
        return 1
    finally:
        if session is not None:
            session.close()


if __name__ == "__main__":
    raise SystemExit(main())
