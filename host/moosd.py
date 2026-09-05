"""Local, typed Unix-socket control service for the Personal MOOS runtime."""

from __future__ import annotations

import codecs
import logging
import os
import pwd
import select
import socket
import stat
import struct
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from moos_protocol import (
    ByteTransport,
    FrameDecoder,
    FrameResult,
    ProtocolError,
    TerminalClose,
    TerminalInput,
    encode_frame,
    make_control_error,
    make_control_success,
    make_terminal_error,
    make_terminal_output,
    parse_control_request,
    parse_terminal_frame,
)
from moos_runtime import (
    AlreadyRunning,
    NotRunning,
    PersonalRuntime,
    RuntimeErrorBase,
    TerminalBusy,
    TerminalChannel,
)


READ_BYTES = 4096
TERMINAL_READ_BYTES = 2048
LOG = logging.getLogger(__name__)
MAX_CLIENTS = 32
MAX_CLIENTS_PER_UID = 4
CLIENT_IDLE_TIMEOUT = 20.0

GATEWAY_USER = "moos-gateway"
GATEWAY_UID_PATH = Path("/etc/moos/gateway.uid")
GATEWAY_OPERATIONS = frozenset({"status"})


@dataclass(frozen=True)
class DispatchResult:
    response: dict[str, Any]
    terminal: TerminalChannel | None = None


class MoosdService:
    """Translate a small allowlist of validated requests to runtime operations."""

    def __init__(self, runtime: PersonalRuntime) -> None:
        self.runtime = runtime

    def dispatch(
        self,
        request: dict[str, Any],
        *,
        allowed_operations: frozenset[str] | None = None,
    ) -> DispatchResult:
        try:
            operation = parse_control_request(request).operation
            if allowed_operations is not None and operation not in allowed_operations:
                return DispatchResult(
                    self._error("forbidden", "Operation is not permitted")
                )
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
        except ProtocolError as error:
            return DispatchResult(self._error(error.code, error.message))
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
        return make_control_success(operation, data)

    @staticmethod
    def _error(code: str, message: str) -> dict[str, Any]:
        return make_control_error(code, message)


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

    clients: dict[int, int] = {}
    clients_lock = threading.Lock()

    def handle_bounded(connection: socket.socket, uid: int) -> None:
        try:
            _handle_client(connection, service)
        finally:
            with clients_lock:
                clients[uid] -= 1
                if not clients[uid]:
                    del clients[uid]

    try:
        while True:
            connection, _ = listener.accept()
            try:
                uid = _peer_uid(connection)
            except OSError:
                connection.close()
                continue
            with clients_lock:
                if (
                    sum(clients.values()) >= MAX_CLIENTS
                    or clients.get(uid, 0) >= MAX_CLIENTS_PER_UID
                ):
                    connection.close()
                    continue
                clients[uid] = clients.get(uid, 0) + 1
            worker = threading.Thread(
                target=handle_bounded,
                args=(connection, uid),
                daemon=True,
                name="moosd-client",
            )
            try:
                worker.start()
            except RuntimeError:
                connection.close()
                with clients_lock:
                    clients[uid] -= 1
                    if not clients[uid]:
                        del clients[uid]
    finally:
        if owns_listener:
            listener.close()
            socket_path.unlink(missing_ok=True)


def _request_error(result: FrameResult) -> dict[str, Any]:
    if result.error == "frame_too_large":
        return MoosdService._error("request_too_large", "request is too large")
    return MoosdService._error("invalid_request", "request must be one JSON object")


def _send(connection: ByteTransport, frame: dict[str, Any]) -> bool:
    try:
        connection.sendall(encode_frame(frame))
        return True
    except (OSError, ValueError):
        return False


def _peer_uid(connection: socket.socket) -> int:
    credentials = connection.getsockopt(
        socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
    )
    _, uid, _ = struct.unpack("3i", credentials)
    return uid


def _gateway_uids() -> frozenset[int]:
    uids: set[int] = set()
    try:
        uids.add(pwd.getpwnam(GATEWAY_USER).pw_uid)
    except KeyError:
        pass
    try:
        configured = GATEWAY_UID_PATH.read_text(encoding="ascii").strip()
        if configured.isascii() and configured.isdigit() and int(configured) > 0:
            uids.add(int(configured))
    except OSError:
        pass
    return frozenset(uids)


def _handle_client(connection: socket.socket, service: MoosdService) -> None:
    decoder = FrameDecoder()
    with connection:
        try:
            connection.settimeout(CLIENT_IDLE_TIMEOUT)
            peer_uid = _peer_uid(connection)
            gateway_uids = _gateway_uids()
            allowed_operations = (
                GATEWAY_OPERATIONS
                if peer_uid in gateway_uids
                else None
            )
            while True:
                data = connection.recv(READ_BYTES)
                if not data:
                    final = decoder.finish()
                    if final is not None:
                        _send(connection, _request_error(final))
                    return
                results = decoder.feed(data)
                for index, result in enumerate(results):
                    if result.error is not None:
                        if not _send(connection, _request_error(result)):
                            return
                        continue
                    if result.frame is None:
                        continue
                    dispatched = service.dispatch(
                        result.frame,
                        allowed_operations=allowed_operations,
                    )
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
    return _send(connection, make_terminal_error(code))


def _terminal_protocol_error(code: str) -> str:
    if code == "frame_too_large":
        return "frame_too_large"
    return "invalid_frame"


def _handle_terminal_results(
    connection: socket.socket,
    channel: TerminalChannel,
    results: list[FrameResult],
) -> bool:
    for result in results:
        if result.error is not None or result.frame is None:
            if not _terminal_error(
                connection, _terminal_protocol_error(result.error or "invalid_frame")
            ):
                return False
            continue
        try:
            terminal_frame = parse_terminal_frame(result.frame)
        except ProtocolError as error:
            if not _terminal_error(connection, error.code):
                return False
            continue
        if isinstance(terminal_frame, TerminalInput):
            try:
                channel.write(terminal_frame.data.encode("utf-8"))
            except OSError:
                _terminal_error(connection, "terminal_unavailable")
                return False
        elif isinstance(terminal_frame, TerminalClose):
            return False
        else:
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
    output_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
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
                    final = decoder.finish()
                    if final is not None:
                        _handle_terminal_results(connection, channel, [final])
                    return
                if not _handle_terminal_results(connection, channel, decoder.feed(data)):
                    return
            if channel in readable:
                output = channel.read(TERMINAL_READ_BYTES)
                text = output_decoder.decode(output, final=not output)
                if text and not _send(
                    connection,
                    make_terminal_output(text),
                ):
                    return
                if not output:
                    return
    except OSError:
        return
    finally:
        channel.close()
