"""Local, typed Unix-socket control service for the Personal MOOS runtime."""

from __future__ import annotations

import json
import os
import select
import socket
from dataclasses import asdict
from pathlib import Path
from typing import Any

from moos_runtime import (
    AlreadyRunning,
    NotRunning,
    PersonalRuntime,
    RuntimeErrorBase,
)


PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 16 * 1024


class MoosdService:
    """Translate a small allowlist of local requests to runtime operations."""

    def __init__(self, runtime: PersonalRuntime) -> None:
        self.runtime = runtime

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        if request.get("protocolVersion") != PROTOCOL_VERSION:
            return self._error("unsupported_protocol", "protocolVersion must be 1")
        if set(request) - {"protocolVersion", "operation"}:
            return self._error("invalid_request", "unknown request field")
        operation = request.get("operation")
        if not isinstance(operation, str):
            return self._error("invalid_request", "operation must be a string")

        try:
            if operation == "status":
                status = self.runtime.status()
                return self._ok(operation, {"personal": asdict(status)})
            if operation == "personal.status":
                return self._ok(operation, {"personal": asdict(self.runtime.status())})
            if operation == "personal.start":
                return self._ok(operation, {"personal": asdict(self.runtime.start())})
            if operation == "personal.stop":
                return self._ok(operation, {"personal": asdict(self.runtime.stop())})
            if operation == "personal.terminal.open":
                self.runtime.open_terminal()
                return self._ok(operation, {"channel": "serial-console"})
            return self._error("unknown_operation", "operation is not supported")
        except AlreadyRunning:
            return self._error("already_running", "Personal is already running")
        except NotRunning:
            return self._error("not_running", "Personal is not running")
        except RuntimeErrorBase as error:
            return self._error("runtime_error", str(error))

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


def serve(socket_path: Path, service: MoosdService) -> None:
    """Serve newline-delimited JSON requests on a local Unix stream socket."""

    socket_path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(socket_path))
        os.chmod(socket_path, 0o660)
        listener.listen(8)
        try:
            while True:
                connection, _ = listener.accept()
                with connection:
                    request_bytes = connection.recv(MAX_REQUEST_BYTES + 1)
                    if len(request_bytes) > MAX_REQUEST_BYTES:
                        response = MoosdService._error("request_too_large", "request is too large")
                    else:
                        response = _decode_and_handle(request_bytes, service)
                    connection.sendall((json.dumps(response, separators=(",", ":")) + "\n").encode())
                    if response.get("ok") and response.get("operation") == "personal.terminal.open":
                        _serve_terminal(connection, service)
        finally:
            socket_path.unlink(missing_ok=True)


def _decode_and_handle(request_bytes: bytes, service: MoosdService) -> dict[str, Any]:
    try:
        request = json.loads(request_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return MoosdService._error("invalid_request", "request must be valid JSON")
    if not isinstance(request, dict):
        return MoosdService._error("invalid_request", "request must be an object")
    return service.handle(request)


def _serve_terminal(connection: socket.socket, service: MoosdService) -> None:
    channel = service.runtime.open_terminal()
    pending = b""
    while True:
        readable, _, _ = select.select([connection, channel], [], [], 0.25)
        if connection in readable:
            data = connection.recv(4096)
            if not data:
                return
            pending += data
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                try:
                    frame = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    connection.sendall(b'{"type":"error","code":"invalid_frame"}\n')
                    continue
                if frame.get("type") == "input" and isinstance(frame.get("data"), str):
                    channel.write(frame["data"].encode("utf-8"))
                elif frame.get("type") == "close":
                    return
                else:
                    try:
                        connection.sendall(b'{"type":"error","code":"invalid_frame"}\n')
                    except OSError:
                        return
        if channel in readable:
            try:
                output = channel.read()
            except OSError:
                return
            if not output:
                return
            frame = {"type": "output", "data": output.decode("utf-8", errors="replace")}
            try:
                connection.sendall((json.dumps(frame, separators=(",", ":")) + "\n").encode("utf-8"))
            except OSError:
                return
