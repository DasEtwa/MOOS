"""Small host-side lifecycle adapter for the single Personal MOOS runtime.

This module deliberately delegates launch policy to the existing managed
``run-instance.sh`` path. It does not construct QEMU arguments, expose host
paths, or execute arbitrary commands.
"""

from __future__ import annotations

import os
import pty
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable


PERSONAL_ID = "personal"
UNIT_NAME = "moos-instance-personal.service"


class RuntimeErrorBase(Exception):
    """Base class for safe Personal runtime control failures."""


class AlreadyRunning(RuntimeErrorBase):
    pass


class NotRunning(RuntimeErrorBase):
    pass


class UnsupportedOperation(RuntimeErrorBase):
    pass


class RuntimeState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RuntimeStatus:
    identity: str
    state: RuntimeState
    result: str | None = None


@dataclass(frozen=True)
class SerialConnection:
    """Guest-facing connection description without leaking a host PTY path."""

    kind: str
    available: bool
    interactive: bool


class TerminalChannel:
    """A retained PTY master used only for guest serial-console bytes."""

    def __init__(self, master_fd: int) -> None:
        self.master_fd = master_fd

    def fileno(self) -> int:
        return self.master_fd

    def read(self, size: int = 4096) -> bytes:
        return os.read(self.master_fd, size)

    def write(self, data: bytes) -> None:
        os.write(self.master_fd, data)

    def close(self) -> None:
        try:
            os.close(self.master_fd)
        except OSError:
            pass


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


class PersonalRuntime:
    """Control Personal through the already-existing managed host boundary."""

    def __init__(
        self,
        repo_root: Path,
        *,
        runner: CommandRunner | None = None,
        popen: Callable[..., subprocess.Popen[str]] | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.run_instance = self.repo_root / "scripts" / "run-instance.sh"
        self._runner = runner or subprocess.run
        self._popen = popen or subprocess.Popen
        self._terminal: TerminalChannel | None = None

    def _systemctl_state(self) -> RuntimeStatus:
        result = self._runner(
            ["systemctl", "show", UNIT_NAME, "--property=ActiveState", "--value"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return RuntimeStatus(PERSONAL_ID, RuntimeState.STOPPED)

        active = result.stdout.strip()
        state = {
            "inactive": RuntimeState.STOPPED,
            "deactivating": RuntimeState.STOPPED,
            "activating": RuntimeState.STARTING,
            "active": RuntimeState.RUNNING,
            "failed": RuntimeState.FAILED,
        }.get(active, RuntimeState.UNKNOWN)
        return RuntimeStatus(PERSONAL_ID, state)

    def status(self) -> RuntimeStatus:
        return self._systemctl_state()

    def start(self) -> RuntimeStatus:
        current = self.status()
        if current.state in {RuntimeState.STARTING, RuntimeState.RUNNING}:
            raise AlreadyRunning(PERSONAL_ID)
        if not self.run_instance.is_file():
            raise RuntimeErrorBase(f"missing managed runner: {self.run_instance}")

        master_fd, slave_fd = pty.openpty()
        try:
            self._popen(
                [str(self.run_instance), "--id", PERSONAL_ID],
                cwd=self.repo_root,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,
                close_fds=True,
                text=True,
            )
        except BaseException:
            os.close(master_fd)
            raise
        finally:
            os.close(slave_fd)
        self._terminal = TerminalChannel(master_fd)
        return RuntimeStatus(PERSONAL_ID, RuntimeState.STARTING)

    def stop(self) -> RuntimeStatus:
        current = self.status()
        if current.state == RuntimeState.STOPPED:
            raise NotRunning(PERSONAL_ID)
        result = self._runner(
            ["systemctl", "stop", UNIT_NAME], capture_output=True, text=True, check=False
        )
        if self._terminal is not None:
            self._terminal.close()
            self._terminal = None
        if result.returncode != 0:
            raise RuntimeErrorBase(result.stderr.strip() or "failed to stop Personal")
        return RuntimeStatus(PERSONAL_ID, RuntimeState.STOPPED)

    def reboot(self) -> None:
        raise UnsupportedOperation(
            "Personal reboot is not exposed until a guest terminal channel exists"
        )

    def serial_connection(self) -> SerialConnection:
        status = self.status()
        return SerialConnection(
            kind="serial-console",
            available=status.state == RuntimeState.RUNNING and self._terminal is not None,
            interactive=self._terminal is not None,
        )

    def open_terminal(self) -> TerminalChannel:
        if self.status().state != RuntimeState.RUNNING or self._terminal is None:
            raise NotRunning(PERSONAL_ID)
        return self._terminal
