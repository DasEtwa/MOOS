#!/usr/bin/env python3
"""Regression checks for the privileged control-plane installer."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "scripts" / "setup-control-plane.sh"
SOURCES = (
    "host/moos_protocol.py",
    "host/moos_runtime.py",
    "host/moosd.py",
    "scripts/moos",
    "scripts/moosd.py",
    "scripts/run-instance.sh",
    "systemd/moosd.service",
    "systemd/moosd.socket",
    "HOST_GUEST_ISOLATION.md",
)


def copy_source(destination: Path) -> None:
    for relative in SOURCES:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, target)


def dry_run(source: Path, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(INSTALLER), "--source-root", str(source), "--dry-run"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def expect_rejected(source: Path, needle: str) -> None:
    result = dry_run(source)
    assert result.returncode != 0, result.stdout
    assert needle in result.stderr, result.stderr


def main() -> int:
    installer = INSTALLER.read_text(encoding="utf-8")
    for required in (
        "python3 -I -",
        "systemd-run",
        '--uid="$VALIDATION_USER"',
        "control-releases",
        'mv -Tf "$CURRENT_TEMP" "$CURRENT_LINK"',
        "rollback()",
        "stop_if_loaded()",
        "os.O_EXCL",
        'trap cleanup EXIT HUP INT TERM',
    ):
        assert required in installer, required
    rollback_match = re.search(r"\nrollback\(\) \{(?P<body>.*?)\n\}", installer, re.DOTALL)
    assert rollback_match is not None
    rollback = rollback_match.group("body")
    for key in ("service", "socket", "documentation", "current", "daemon", "runner", "client"):
        assert f" {key}\n" in rollback, key
    for state_restore in (
        "systemctl daemon-reload",
        "systemctl enable moosd.socket",
        "systemctl disable moosd.socket",
        "systemctl start moosd.socket",
        "systemctl start moosd.service",
    ):
        assert state_restore in rollback, state_restore
    assert re.search(
        r'if \[ "\$SUCCESS" -ne 1 \].*\[ "\$ACTIVATING" -eq 1 \].*rollback',
        installer,
        re.DOTALL,
    )
    for atomic_target in (
        '"$UNIT_ROOT/moosd.service"',
        '"$UNIT_ROOT/moosd.socket"',
        '"$CURRENT_LINK"',
        '"$LIBEXEC_ROOT/moosd"',
        '"$LIBEXEC_ROOT/run-instance.sh"',
        "/usr/bin/moos",
    ):
        assert re.search(r"mv -Tf [^\n]+ " + re.escape(atomic_target), installer), atomic_target

    with tempfile.TemporaryDirectory(prefix="moos-control-test-") as temporary:
        root = Path(temporary)
        clean = root / "clean"
        copy_source(clean)
        result = dry_run(clean)
        assert result.returncode == 0, result.stderr
        for expected in (
            "root-owned, versioned releases",
            "staged code runs sandboxed",
            "atomic release pointer with transactional rollback",
            "host mutation: none",
        ):
            assert expected in result.stdout, result.stdout

        hostile = root / "hostile"
        hostile.mkdir()
        marker = root / "sitecustomize-ran"
        (hostile / "sitecustomize.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
            encoding="utf-8",
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(hostile)
        result = dry_run(clean, env=environment)
        assert result.returncode == 0, result.stderr
        assert not marker.exists(), "installer imported hostile sitecustomize"

        symlinked = root / "symlinked"
        copy_source(symlinked)
        (symlinked / "scripts/moos").unlink()
        (symlinked / "scripts/moos").symlink_to(REPO_ROOT / "scripts/moos")
        expect_rejected(symlinked, "unsafe or unreadable source file")

        hardlinked = root / "hardlinked"
        copy_source(hardlinked)
        os.link(hardlinked / "host/moosd.py", hardlinked / "host/moosd.extra")
        expect_rejected(hardlinked, "singly-linked")

        malformed = root / "malformed"
        copy_source(malformed)
        (malformed / "scripts/moosd.py").write_text("def broken(:\n", encoding="utf-8")
        expect_rejected(malformed, "invalid Python source")

        changed_unit = root / "changed-unit"
        copy_source(changed_unit)
        with (changed_unit / "systemd/moosd.service").open("a", encoding="utf-8") as handle:
            handle.write("\n# unreviewed privileged change\n")
        expect_rejected(changed_unit, "unexpected privileged unit content")

        release = root / "release"
        release.mkdir()
        shutil.copy2(REPO_ROOT / "host/moos_protocol.py", release / "moos_protocol.py")
        shutil.copy2(REPO_ROOT / "host/moos_runtime.py", release / "moos_runtime.py")
        shutil.copy2(REPO_ROOT / "host/moosd.py", release / "moosd.py")
        shutil.copy2(REPO_ROOT / "scripts/moos", release / "moos")
        shutil.copy2(REPO_ROOT / "scripts/moosd.py", release / "moosd")
        shutil.copy2(REPO_ROOT / "scripts/run-instance.sh", release / "run-instance.sh")
        for executable in ("moos", "moosd", "run-instance.sh"):
            (release / executable).chmod(0o755)
        assert subprocess.run(
            [str(release / "moosd"), "--version"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip() == "MOOS control daemon 1"
        subprocess.run(
            [
                str(release / "moosd"),
                "--runner",
                str(release / "run-instance.sh"),
                "--check-config",
            ],
            check=True,
        )
        subprocess.run([str(release / "moos"), "--help"], capture_output=True, check=True)

    print("MOOS control-plane installer test: PASS")
    print("  checkout inputs: no-follow, single-link, syntax and unit hash checked")
    print("  release: self-contained, staged validation and transactional activation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
