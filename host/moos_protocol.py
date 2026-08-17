"""Bounded newline-delimited JSON framing for the local MOOS protocol."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol


PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 16 * 1024
CONTROL_OPERATIONS = frozenset(
    {
        "status",
        "personal.status",
        "personal.start",
        "personal.stop",
        "personal.terminal.open",
    }
)


class ProtocolError(ValueError):
    """A client-visible protocol validation failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ByteTransport(Protocol):
    """Minimal byte transport required by the versioned framing layer."""

    def recv(self, size: int) -> bytes:
        ...

    def sendall(self, data: bytes) -> None:
        ...

    def fileno(self) -> int:
        ...


@dataclass(frozen=True)
class ControlRequest:
    operation: str


@dataclass(frozen=True)
class TerminalInput:
    data: str


@dataclass(frozen=True)
class TerminalClose:
    pass


@dataclass(frozen=True)
class FrameResult:
    """One decoded object frame or one recoverable framing error."""

    frame: dict[str, Any] | None = None
    error: str | None = None


def make_control_request(operation: str) -> dict[str, Any]:
    """Create a Protocol-v1 control request for a supported operation."""

    if not isinstance(operation, str):
        raise ProtocolError("invalid_request", "operation must be a string")
    if operation not in CONTROL_OPERATIONS:
        raise ProtocolError("unknown_operation", "operation is not supported")
    return {"protocolVersion": PROTOCOL_VERSION, "operation": operation}


def parse_control_request(frame: dict[str, Any]) -> ControlRequest:
    """Validate and type a control-plane request without exposing host details."""

    if not isinstance(frame, dict):
        raise ProtocolError("invalid_request", "request must be one JSON object")
    if frame.get("protocolVersion") != PROTOCOL_VERSION:
        raise ProtocolError("unsupported_protocol", "protocolVersion must be 1")
    if set(frame) - {"protocolVersion", "operation"}:
        raise ProtocolError("invalid_request", "unknown request field")
    operation = frame.get("operation")
    if not isinstance(operation, str):
        raise ProtocolError("invalid_request", "operation must be a string")
    if operation not in CONTROL_OPERATIONS:
        raise ProtocolError("unknown_operation", "operation is not supported")
    return ControlRequest(operation)


def parse_terminal_frame(frame: dict[str, Any]) -> TerminalInput | TerminalClose:
    """Validate a client-to-server terminal frame."""

    if not isinstance(frame, dict):
        raise ProtocolError("invalid_frame", "terminal frame must be an object")
    frame_type = frame.get("type")
    if frame_type == "input":
        if set(frame) != {"type", "data"} or not isinstance(frame.get("data"), str):
            raise ProtocolError("invalid_frame", "input frame requires only string data")
        return TerminalInput(frame["data"])
    if frame_type == "close" and set(frame) == {"type"}:
        return TerminalClose()
    raise ProtocolError("invalid_frame", "unsupported terminal frame")


def make_terminal_output(data: str) -> dict[str, str]:
    if not isinstance(data, str):
        raise TypeError("terminal output must be text")
    return {"type": "output", "data": data}


def make_terminal_error(code: str) -> dict[str, str]:
    return {"type": "error", "code": code}


class FrameDecoder:
    """Incrementally decode bounded NDJSON objects from arbitrary byte chunks."""

    def __init__(self, max_frame_bytes: int = MAX_FRAME_BYTES) -> None:
        if max_frame_bytes < 1:
            raise ValueError("max_frame_bytes must be positive")
        self.max_frame_bytes = max_frame_bytes
        self._buffer = bytearray()
        self._discarding_oversize = False

    def feed(self, data: bytes) -> list[FrameResult]:
        if not isinstance(data, bytes):
            raise TypeError("framing input must be bytes")

        results: list[FrameResult] = []
        offset = 0
        while offset < len(data):
            if self._discarding_oversize:
                newline = data.find(b"\n", offset)
                if newline < 0:
                    return results
                self._discarding_oversize = False
                offset = newline + 1
                continue

            newline = data.find(b"\n", offset)
            if newline < 0:
                self._buffer.extend(data[offset:])
                if len(self._buffer) > self.max_frame_bytes:
                    self._buffer.clear()
                    self._discarding_oversize = True
                    results.append(FrameResult(error="frame_too_large"))
                return results

            self._buffer.extend(data[offset:newline])
            offset = newline + 1
            if len(self._buffer) > self.max_frame_bytes:
                results.append(FrameResult(error="frame_too_large"))
                self._buffer.clear()
                continue

            encoded = bytes(self._buffer)
            self._buffer.clear()
            try:
                value = json.loads(encoded.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                results.append(FrameResult(error="invalid_json"))
                continue
            if not isinstance(value, dict):
                results.append(FrameResult(error="invalid_type"))
                continue
            results.append(FrameResult(frame=value))
        return results

    def finish(self) -> FrameResult | None:
        """Report a truncated final frame when EOF arrives mid-frame."""

        if self._discarding_oversize:
            self._discarding_oversize = False
            return None
        if self._buffer:
            self._buffer.clear()
            return FrameResult(error="truncated_frame")
        return None


def encode_frame(frame: dict[str, Any], max_frame_bytes: int = MAX_FRAME_BYTES) -> bytes:
    """Encode one object as a bounded, compact NDJSON frame."""

    if not isinstance(frame, dict):
        raise TypeError("protocol frames must be objects")
    encoded = json.dumps(frame, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    if len(encoded) > max_frame_bytes:
        raise ValueError("encoded frame exceeds maximum size")
    return encoded + b"\n"
