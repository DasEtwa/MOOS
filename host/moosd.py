"""Local, typed Unix-socket control service for the Personal MOOS runtime."""

from __future__ import annotations

import logging
import os
import select
import socket
import stat
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from moos_protocol import FrameDecoder, FrameResult, encode_frame
from moos_runtime import (
    AlreadyRunning,
    NotRunning,
    PersonalRuntime,
    RuntimeErrorBase,
    TerminalBusy,
    TerminalChannel,
)


PROTOCOL_VERSION = 1
READ_BYTES = 4096
TERMINAL_READ_BYTES = 2048
LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class DispatchResult:
    response: dict[str, Any]
    terminal: TerminalChannel | None = None


class MoosdService:
    """Translate a small allowlist of validated requests to runtime operations."""

    def __init__(self, runtime: PersonalRuntime) -> None:
        self.runtime = runtime

    def dispatch(self, request: dict[str, Any]) -> DispatchResult:
        if request.get("protocolVersion") != PROTOCOL_VERSION:
            return DispatchResult(
                self._error("unsupported_protocol", "protocolVersion must be 1")
            )
        if set(request) - {"protocolVersion", "operation"}:
            return DispatchResult(self._error("invalid_request", "unknown request field"))
        operation = request.get("operation")
        if not isinstance(operation, str):
            return DispatchResult(
                self._error("invalid_request", "operation must be a string")
            )

        try:
            if operation == "status":
                status = self.runtime.status()
                return DispatchResult(self._ok(operation, {"personal": asdict(status)}))
            if operation == "personal.status":
                return DispatchResult(
                    self._ok(operation, {"personal": asdict(self.runtime.status())})
                )
            if operation == "personal.start":
                return DispatchResult(
                    self._ok(operation, {"personal": asdict(self.runtime.start())})
                )
            if operation == "personal.stop":
                return DispatchResult(
                    self._ok(operation, {"personal": asdict(self.runtime.stop())})
                )
            if operation == "personal.terminal.open":
                terminal = self.runtime.open_terminal()
                return DispatchResult(
                    self._ok(operation, {"channel": "serial-console"}), terminal
                )
            return DispatchResult(
                self._error("unknown_operation", "operation is not supported")
            )
        except AlreadyRunning:
            return DispatchResult(
                self._error("already_running", "Personal is already running")
            )
        except NotRunning:
            return DispatchResult(self._error("not_running", "Personal is not running"))
        except TerminalBusy:
            return DispatchResult(
                self._error("terminal_busy", "Personal terminal is already in use")
            )
        except RuntimeErrorBase as error:
            return DispatchResult(self._error("runtime_error", str(error)))

    @staticmethod
    def _ok(operation: str, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "ok": True,
            "operation": operation,
            "data": data,
            "events": [],
        }

    @staticmethod
    def _error(code: str, message: str) -> dict[str, Any]:
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "ok": False,
            "error": {"code": code, "message": message},
        }


def serve(
    socket_path: Path,
    service: MoosdService,
    *,
    listener: socket.socket | None = None,
) -> None:
    """Serve bounded NDJSON frames on a direct or systemd-owned Unix socket."""

    owns_listener = listener is None
    if listener is None:
        socket_path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        if socket_path.exists() or socket_path.is_symlink():
            mode = socket_path.lstat().st_mode
            if not stat.S_ISSOCK(mode):
                raise RuntimeError(f"refusing to replace non-socket path: {socket_path}")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                try:
                    probe.connect(str(socket_path))
                except ConnectionRefusedError:
                    pass
                else:
                    raise RuntimeError(f"control socket is already in use: {socket_path}")
            socket_path.unlink()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(socket_path))
        os.chmod(socket_path, 0o660)
        listener.listen(16)

    try:
        while True:
            connection, _ = listener.accept()
            worker = threading.Thread(
                target=_handle_client,
                args=(connection, service),
                daemon=True,
                name="moosd-client",
            )
            worker.start()
    finally:
        if owns_listener:
            listener.close()
            socket_path.unlink(missing_ok=True)


def _request_error(result: FrameResult) -> dict[str, Any]:
    if result.error == "frame_too_large":
        return MoosdService._error("request_too_large", "request is too large")
    return MoosdService._error("invalid_request", "request must be one JSON object")


def _send(connection: socket.socket, frame: dict[str, Any]) -> bool:
    try:
        connection.sendall(encode_frame(frame))
        return True
    except (OSError, ValueError):
        return False


def _handle_client(connection: socket.socket, service: MoosdService) -> None:
    decoder = FrameDecoder()
    with connection:
        try:
            while True:
                data = connection.recv(READ_BYTES)
                if not data:
                    return
                results = decoder.feed(data)
                for index, result in enumerate(results):
                    if result.error is not None:
                        if not _send(connection, _request_error(result)):
                            return
                        continue
                    if result.frame is None:
                        continue
                    dispatched = service.dispatch(result.frame)
                    if not _send(connection, dispatched.response):
                        if dispatched.terminal is not None:
                            dispatched.terminal.close()
                        return
                    if dispatched.terminal is not None:
                        _serve_terminal(
                            connection,
                            dispatched.terminal,
                            decoder,
                            results[index + 1 :],
                        )
                        return
        except OSError:
            return
        except Exception:
            LOG.exception("unexpected client handler failure")


def _terminal_error(connection: socket.socket, code: str) -> bool:
    return _send(connection, {"type": "error", "code": code})


def _handle_terminal_results(
    connection: socket.socket,
    channel: TerminalChannel,
    results: list[FrameResult],
) -> bool:
    for result in results:
        if result.error is not None or result.frame is None:
            if not _terminal_error(connection, result.error or "invalid_frame"):
                return False
            continue
        frame = result.frame
        frame_type = frame.get("type")
        if frame_type == "input" and isinstance(frame.get("data"), str):
            try:
                channel.write(frame["data"].encode("utf-8"))
            except OSError:
                _terminal_error(connection, "terminal_unavailable")
                return False
        elif frame_type == "close" and set(frame) == {"type"}:
            return False
        elif not _terminal_error(connection, "invalid_frame"):
            return False
    return True


def _serve_terminal(
    connection: socket.socket,
    channel: TerminalChannel,
    decoder: FrameDecoder | None = None,
    initial_results: list[FrameResult] | None = None,
) -> None:
    """Bridge framed client input to one reconnectable guest console connection."""

    decoder = decoder or FrameDecoder()
    try:
        if initial_results and not _handle_terminal_results(
            connection, channel, initial_results
        ):
            return
        while True:
            readable, _, _ = select.select([connection, channel], [], [], 0.25)
            if connection in readable:
                data = connection.recv(READ_BYTES)
                if not data:
                    return
                if not _handle_terminal_results(connection, channel, decoder.feed(data)):
                    return
            if channel in readable:
                output = channel.read(TERMINAL_READ_BYTES)
                if not output:
                    return
                frame = {
                    "type": "output",
                    "data": output.decode("utf-8", errors="replace"),
                }
                if not _send(connection, frame):
                    return
    except OSError:
        return
    finally:
        channel.close()
