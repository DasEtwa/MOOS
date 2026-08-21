"""Host lifecycle adapter for the single managed Personal MOOS runtime.

The adapter delegates the fixed launch policy to ``run-instance.sh`` and
connects only to Personal's fixed QEMU serial socket. It never constructs
arbitrary QEMU arguments or exposes a host command interface.
"""

from __future__ import annotations

import logging
import pwd
import socket
import stat
import struct
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable


PERSONAL_ID = "personal"
UNIT_NAME = "moos-instance-personal.service"
DEFAULT_CONSOLE_SOCKET = Path("/run/moos-instances/personal/console.sock")
RUNTIME_USER = "moos-runtime"
LOG = logging.getLogger(__name__)


class RuntimeErrorBase(Exception):
    """Base class for safe Personal runtime control failures."""


class AlreadyRunning(RuntimeErrorBase):
    pass


class NotRunning(RuntimeErrorBase):
    pass


class TerminalBusy(RuntimeErrorBase):
    pass


class UnsupportedOperation(RuntimeErrorBase):
    pass


class RuntimeState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RuntimeStatus:
    identity: str
    state: RuntimeState
    result: str | None = None


@dataclass(frozen=True)
class SerialConnection:
    """Guest-facing connection description without leaking a host socket path."""

    kind: str
    available: bool
    interactive: bool


class TerminalChannel:
    """One client connection to QEMU's reconnectable serial socket."""

    def __init__(self, connection: socket.socket, release: Callable[[], None]) -> None:
        self._connection = connection
        self._release = release
        self._closed = False

    def fileno(self) -> int:
        return self._connection.fileno()

    def read(self, size: int = 4096) -> bytes:
        return self._connection.recv(size)

    def write(self, data: bytes) -> None:
        self._connection.sendall(data)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._connection.close()
        finally:
            self._release()


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


class PersonalRuntime:
    """Control Personal through the fixed, systemd-managed host boundary."""

    def __init__(
        self,
        repo_root: Path,
        *,
        runner: CommandRunner | None = None,
        run_instance: Path | None = None,
        console_socket: Path = DEFAULT_CONSOLE_SOCKET,
        expected_console_uid: int | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.run_instance = (
            run_instance.resolve()
            if run_instance is not None
            else self.repo_root / "scripts" / "run-instance.sh"
        )
        self.console_socket = console_socket
        self.expected_console_uid = expected_console_uid
        self._runner = runner or subprocess.run
        self._terminal_lock = threading.Lock()

    def _run(self, argv: list[str]) -> subprocess.CompletedProcess[str] | None:
        try:
            return self._runner(
                argv,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            LOG.exception("host lifecycle command could not be executed")
            return None

    def _systemctl_state(self) -> RuntimeStatus:
        completed = self._run(
            [
                "systemctl",
                "show",
                UNIT_NAME,
                "--property=LoadState",
                "--property=ActiveState",
                "--property=SubState",
                "--property=Result",
            ]
        )
        if completed is None or completed.returncode != 0:
            return RuntimeStatus(PERSONAL_ID, RuntimeState.UNKNOWN, "status-unavailable")

        properties: dict[str, str] = {}
        for line in completed.stdout.splitlines():
            key, separator, value = line.partition("=")
            if separator:
                properties[key] = value

        load = properties.get("LoadState")
        active = properties.get("ActiveState")
        substate = properties.get("SubState")
        result = properties.get("Result") or None

        if load == "not-found":
            return RuntimeStatus(PERSONAL_ID, RuntimeState.STOPPED)
        if load != "loaded" or active is None or substate is None:
            return RuntimeStatus(PERSONAL_ID, RuntimeState.UNKNOWN, result)
        if active == "failed" or (
            active == "inactive" and result not in {None, "success"}
        ):
            return RuntimeStatus(PERSONAL_ID, RuntimeState.FAILED, result)
        if active == "activating" or substate in {
            "start",
            "start-pre",
            "start-post",
            "auto-restart",
        }:
            return RuntimeStatus(PERSONAL_ID, RuntimeState.STARTING, result)
        if active == "deactivating" or substate in {"stop", "stop-sigterm", "stop-sigkill"}:
            return RuntimeStatus(PERSONAL_ID, RuntimeState.STOPPING, result)
        if active == "active" and substate == "running":
            return RuntimeStatus(PERSONAL_ID, RuntimeState.RUNNING, result)
        if active == "inactive" and substate == "dead":
            return RuntimeStatus(PERSONAL_ID, RuntimeState.STOPPED, result)
        return RuntimeStatus(PERSONAL_ID, RuntimeState.UNKNOWN, result)

    def status(self) -> RuntimeStatus:
        return self._systemctl_state()

    def start(self) -> RuntimeStatus:
        current = self.status()
        if current.state in {
            RuntimeState.STARTING,
            RuntimeState.RUNNING,
            RuntimeState.STOPPING,
        }:
            raise AlreadyRunning(PERSONAL_ID)
        if current.state == RuntimeState.UNKNOWN:
            raise RuntimeErrorBase("Personal state is unavailable")
        if not self.run_instance.is_file():
            raise RuntimeErrorBase("managed Personal runner is unavailable")

        if current.state == RuntimeState.FAILED:
            reset = self._run(["systemctl", "reset-failed", UNIT_NAME])
            if reset is None or reset.returncode != 0:
                raise RuntimeErrorBase("failed Personal state could not be reset")

        completed = self._run([str(self.run_instance), "--id", PERSONAL_ID])
        if completed is None or completed.returncode != 0:
            if completed is not None:
                LOG.error(
                    "managed Personal launch failed with status %s: %s",
                    completed.returncode,
                    completed.stderr.strip(),
                )
            raise RuntimeErrorBase("failed to start Personal")

        deadline = time.monotonic() + 3.0
        while True:
            status = self.status()
            if status.state == RuntimeState.RUNNING and self._console_exists():
                return status
            if status.state in {
                RuntimeState.FAILED,
                RuntimeState.STOPPED,
                RuntimeState.UNKNOWN,
            }:
                raise RuntimeErrorBase("Personal did not enter a running state")
            if time.monotonic() >= deadline:
                raise RuntimeErrorBase("Personal console did not become ready")
            time.sleep(0.05)

    def stop(self) -> RuntimeStatus:
        current = self.status()
        if current.state == RuntimeState.STOPPED:
            raise NotRunning(PERSONAL_ID)
        completed = self._run(["systemctl", "stop", UNIT_NAME])
        if completed is None or completed.returncode != 0:
            if completed is not None:
                LOG.error(
                    "managed Personal stop failed with status %s: %s",
                    completed.returncode,
                    completed.stderr.strip(),
                )
            raise RuntimeErrorBase("failed to stop Personal")

        status = self.status()
        if status.state not in {RuntimeState.STOPPED, RuntimeState.FAILED}:
            raise RuntimeErrorBase("Personal did not stop cleanly")
        return status

    def reboot(self) -> None:
        raise UnsupportedOperation("Personal reboot is not exposed")

    def _console_uid(self) -> int:
        if self.expected_console_uid is not None:
            return self.expected_console_uid
        try:
            return pwd.getpwnam(RUNTIME_USER).pw_uid
        except KeyError as error:
            raise RuntimeErrorBase("Personal console owner is unavailable") from error

    def _console_exists(self) -> bool:
        try:
            status = self.console_socket.lstat()
            return (
                stat.S_ISSOCK(status.st_mode)
                and status.st_nlink == 1
                and status.st_uid == self._console_uid()
            )
        except (OSError, RuntimeErrorBase):
            return False

    def _validate_console_peer(self, connection: socket.socket) -> None:
        try:
            credentials = connection.getsockopt(
                socket.SOL_SOCKET,
                socket.SO_PEERCRED,
                struct.calcsize("3i"),
            )
            _, peer_uid, _ = struct.unpack("3i", credentials)
        except (AttributeError, OSError, struct.error) as error:
            raise RuntimeErrorBase("Personal console identity is unavailable") from error
        if peer_uid != self._console_uid():
            raise RuntimeErrorBase("Personal console has an unexpected peer identity")

    def serial_connection(self) -> SerialConnection:
        available = self.status().state == RuntimeState.RUNNING and self._console_exists()
        return SerialConnection(
            kind="serial-console",
            available=available,
            interactive=available,
        )

    def open_terminal(self) -> TerminalChannel:
        if self.status().state != RuntimeState.RUNNING:
            raise NotRunning(PERSONAL_ID)
        if not self._console_exists():
            raise RuntimeErrorBase("Personal console is unavailable")
        if not self._terminal_lock.acquire(blocking=False):
            raise TerminalBusy("Personal terminal is already in use")

        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        deadline = time.monotonic() + 2.0
        try:
            while True:
                try:
                    connection.connect(str(self.console_socket))
                    self._validate_console_peer(connection)
                    break
                except (FileNotFoundError, ConnectionRefusedError):
                    if time.monotonic() >= deadline:
                        raise RuntimeErrorBase("Personal console is unavailable")
                    time.sleep(0.05)
                except OSError as error:
                    raise RuntimeErrorBase("Personal console is unavailable") from error
            return TerminalChannel(connection, self._terminal_lock.release)
        except BaseException:
            connection.close()
            self._terminal_lock.release()
            raise
