#!/usr/bin/env python3
"""Boot MOOS in QEMU and verify the Phase 1 console baseline."""

import os
import pty
import re
import select
import signal
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "scripts" / "run-qemu.sh"
ANSI_ESCAPE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))"
)


class QemuSession:
    def __init__(self):
        self.pid, self.fd = pty.fork()
        self.output = ""
        self.exit_status = None

        if self.pid == 0:
            os.chdir(REPO_ROOT)
            os.execv(str(RUNNER), [str(RUNNER), "--serial-only"])

    def _read_available(self):
        chunk = os.read(self.fd, 4096).decode("utf-8", errors="replace")
        self.output += ANSI_ESCAPE.sub("", chunk).replace("\r", "")

    def read_until(self, pattern, timeout):
        expression = re.compile(pattern, re.MULTILINE)
        start = len(self.output)
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            current = self.output[start:]
            if expression.search(current):
                return current

            remaining = deadline - time.monotonic()
            ready, _, _ = select.select([self.fd], [], [], min(remaining, 0.25))
            if not ready:
                continue

            try:
                self._read_available()
            except OSError as error:
                raise RuntimeError("QEMU closed its console") from error

        raise TimeoutError("timed out waiting for " + expression.pattern)

    def send(self, text):
        os.write(self.fd, text.encode("utf-8"))

    def wait_for_exit(self, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            waited_pid, status = os.waitpid(self.pid, os.WNOHANG)
            if waited_pid == self.pid:
                self.exit_status = os.waitstatus_to_exitcode(status)
                return self.exit_status
            time.sleep(0.05)

        raise TimeoutError("QEMU did not exit after guest poweroff")

    def close(self):
        if self.exit_status is None:
            try:
                self.send("\x01x")
            except OSError:
                pass

            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                waited_pid, status = os.waitpid(self.pid, os.WNOHANG)
                if waited_pid == self.pid:
                    self.exit_status = os.waitstatus_to_exitcode(status)
                    break
                time.sleep(0.05)

            if self.exit_status is None:
                try:
                    os.kill(self.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                _, status = os.waitpid(self.pid, 0)
                self.exit_status = os.waitstatus_to_exitcode(status)

        try:
            os.close(self.fd)
        except OSError:
            pass


def require(pattern, text, description):
    if not re.search(pattern, text, re.MULTILINE):
        raise AssertionError("missing " + description)


def main():
    session = None
    try:
        session = QemuSession()
        login_output = session.read_until(r"login:\s*", 45)

        session.send("root\n")
        shell_output = session.read_until(r"\n#\s*$", 15)
        banner_output = login_output + shell_output
        require(r"tiny\. green\. alive\.", banner_output, "MOOS login banner")

        session.send(
            r"""printf 'MOOS_SMOKE_KERNEL=%s\n' "$(uname -r)"
printf 'MOOS_SMOKE_BUSYBOX=%s\n' "$(busybox | head -1)"
printf 'MOOS_SMOKE_RAM=%s\n' "$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
printf 'MOOS_SMOKE_UPTIME=%s\n' "$(awk '{print $1}' /proc/uptime)"
printf 'MOOS_SMOKE_IP=%s\n' "$(ip -4 addr show scope global | awk '/inet / {sub("/.*", "", $2); print $2; exit}')"
printf 'MOOS_SMOKE_ROOTFS=%s\n' "$(df -h / | awk 'NR == 2 {print $3 "/" $4}')"
test -r /proc/uptime && echo MOOS_SMOKE_PROC=ok
test -d /sys && echo MOOS_SMOKE_SYS=ok
test -w /tmp && echo MOOS_SMOKE_TMP=ok
echo MOOS_SMOKE_DONE
"""
        )
        smoke_output = session.read_until(r"^MOOS_SMOKE_DONE$", 15)

        require(r"^MOOS_SMOKE_KERNEL=\S+$", smoke_output, "kernel information")
        require(r"^MOOS_SMOKE_BUSYBOX=BusyBox .+$", smoke_output, "BusyBox information")
        require(r"^MOOS_SMOKE_RAM=[0-9]+$", smoke_output, "RAM information")
        require(r"^MOOS_SMOKE_UPTIME=[0-9.]+$", smoke_output, "uptime information")
        require(
            r"^MOOS_SMOKE_IP=(?!127\.0\.0\.1$)(?:[0-9]{1,3}\.){3}[0-9]{1,3}$",
            smoke_output,
            "non-loopback IPv4 address",
        )
        require(r"^MOOS_SMOKE_ROOTFS=\S+/\S+$", smoke_output, "rootfs information")
        require(r"^MOOS_SMOKE_PROC=ok$", smoke_output, "/proc")
        require(r"^MOOS_SMOKE_SYS=ok$", smoke_output, "/sys")
        require(r"^MOOS_SMOKE_TMP=ok$", smoke_output, "writable /tmp")

        session.send("reboot\n")
        reboot_login = session.read_until(r"login:\s*", 45)
        session.send("root\n")
        reboot_shell = session.read_until(r"\n#\s*$", 15)
        require(
            r"tiny\. green\. alive\.",
            reboot_login + reboot_shell,
            "MOOS login banner after reboot",
        )

        session.send("poweroff\n")
        exit_status = session.wait_for_exit(15)
        if exit_status != 0:
            raise AssertionError("QEMU exited with status " + str(exit_status))

        print("MOOS Phase 1 smoke test: PASS")
        print("  boot/login/banner: ok")
        print("  kernel/BusyBox/RAM/uptime: ok")
        print("  networking and rootfs: ok")
        print("  /proc, /sys, writable /tmp: ok")
        print("  guest reboot, poweroff, and QEMU exit: ok")
        return 0
    except (AssertionError, OSError, RuntimeError, TimeoutError) as error:
        print("MOOS Phase 1 smoke test: FAIL", file=sys.stderr)
        print("  " + str(error), file=sys.stderr)
        if session is not None:
            print("  console tail:", file=sys.stderr)
            print(session.output[-3000:], file=sys.stderr)
        return 1
    finally:
        if session is not None:
            session.close()


if __name__ == "__main__":
    sys.exit(main())
