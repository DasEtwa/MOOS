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
RUNTIME_STATES = frozenset(
    {"starting", "running", "stopping", "stopped", "failed", "unknown"}
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
class PublicRuntimeStatus:
    identity: str
    state: str
    result: str | None = None


@dataclass(frozen=True)
class PersonalData:
    personal: PublicRuntimeStatus

    def as_dict(self) -> dict[str, Any]:
        return {
            "personal": {
                "identity": self.personal.identity,
                "state": self.personal.state,
                "result": self.personal.result,
            }
        }


@dataclass(frozen=True)
class TerminalOpenData:
    channel: str

    def as_dict(self) -> dict[str, Any]:
        return {"channel": self.channel}


@dataclass(frozen=True)
class ControlSuccess:
    operation: str
    data: PersonalData | TerminalOpenData
    events: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ControlFailure:
    code: str
    message: str


@dataclass(frozen=True)
class TerminalInput:
    data: str


@dataclass(frozen=True)
class TerminalClose:
    pass


@dataclass(frozen=True)
class TerminalOutput:
    data: str


@dataclass(frozen=True)
class TerminalError:
    code: str


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


def _require_protocol_version(frame: dict[str, Any], error_code: str) -> None:
    version = frame.get("protocolVersion")
    if type(version) is not int or version != PROTOCOL_VERSION:
        raise ProtocolError(error_code, "protocolVersion must be integer 1")


def parse_control_request(frame: object) -> ControlRequest:
    """Validate and type a control-plane request without exposing host details."""

    if not isinstance(frame, dict):
        raise ProtocolError("invalid_request", "request must be one JSON object")
    _require_protocol_version(frame, "unsupported_protocol")
    if set(frame) - {"protocolVersion", "operation"}:
        raise ProtocolError("invalid_request", "unknown request field")
    operation = frame.get("operation")
    if not isinstance(operation, str):
        raise ProtocolError("invalid_request", "operation must be a string")
    if operation not in CONTROL_OPERATIONS:
        raise ProtocolError("unknown_operation", "operation is not supported")
    return ControlRequest(operation)


def _parse_success_data(
    operation: str, data: object
) -> PersonalData | TerminalOpenData:
    if not isinstance(data, dict):
        raise ProtocolError("invalid_response", "response data must be an object")
    if operation == "personal.terminal.open":
        if data.get("channel") != "serial-console":
            raise ProtocolError("invalid_response", "invalid terminal channel")
        return TerminalOpenData("serial-console")

    personal = data.get("personal")
    if not isinstance(personal, dict):
        raise ProtocolError("invalid_response", "response requires Personal status")
    identity = personal.get("identity")
    state = personal.get("state")
    result = personal.get("result")
    if identity != "personal":
        raise ProtocolError("invalid_response", "invalid Personal identity")
    if not isinstance(state, str) or state not in RUNTIME_STATES:
        raise ProtocolError("invalid_response", "invalid Personal state")
    if result is not None and not isinstance(result, str):
        raise ProtocolError("invalid_response", "invalid Personal result")
    return PersonalData(PublicRuntimeStatus(identity, state, result))


def parse_control_response(
    frame: object, *, expected_operation: str | None = None
) -> ControlSuccess | ControlFailure:
    """Validate and type a Protocol-v1 response from an untrusted transport."""

    if not isinstance(frame, dict):
        raise ProtocolError("invalid_response", "response must be one JSON object")
    _require_protocol_version(frame, "unsupported_protocol")
    ok = frame.get("ok")
    if type(ok) is not bool:
        raise ProtocolError("invalid_response", "response ok must be boolean")

    if not ok:
        error = frame.get("error")
        if not isinstance(error, dict):
            raise ProtocolError("invalid_response", "response requires an error object")
        code = error.get("code")
        message = error.get("message")
        if not isinstance(code, str) or not code:
            raise ProtocolError("invalid_response", "error code must be text")
        if not isinstance(message, str) or not message:
            raise ProtocolError("invalid_response", "error message must be text")
        return ControlFailure(code, message)

    operation = frame.get("operation")
    if not isinstance(operation, str) or operation not in CONTROL_OPERATIONS:
        raise ProtocolError("invalid_response", "invalid response operation")
    if expected_operation is not None and operation != expected_operation:
        raise ProtocolError("invalid_response", "response operation does not match request")
    events = frame.get("events")
    if not isinstance(events, list) or not all(
        isinstance(event, dict) for event in events
    ):
        raise ProtocolError("invalid_response", "response events must be object entries")
    return ControlSuccess(
        operation,
        _parse_success_data(operation, frame.get("data")),
        tuple(events),
    )


def make_control_success(
    operation: str,
    data: dict[str, Any],
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create and internally validate one successful control response."""

    frame = {
        "protocolVersion": PROTOCOL_VERSION,
        "ok": True,
        "operation": operation,
        "data": data,
        "events": [] if events is None else events,
    }
    parse_control_response(frame, expected_operation=operation)
    return frame


def make_control_error(code: str, message: str) -> dict[str, Any]:
    """Create and internally validate one failed control response."""

    frame = {
        "protocolVersion": PROTOCOL_VERSION,
        "ok": False,
        "error": {"code": code, "message": message},
    }
    parse_control_response(frame)
    return frame


def parse_terminal_frame(frame: object) -> TerminalInput | TerminalClose:
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


def make_terminal_input(data: str) -> dict[str, str]:
    if not isinstance(data, str):
        raise TypeError("terminal input must be text")
    return {"type": "input", "data": data}


def make_terminal_close() -> dict[str, str]:
    return {"type": "close"}


def make_terminal_output(data: str) -> dict[str, str]:
    if not isinstance(data, str):
        raise TypeError("terminal output must be text")
    return {"type": "output", "data": data}


def make_terminal_error(code: str) -> dict[str, str]:
    if not isinstance(code, str) or not code:
        raise TypeError("terminal error code must be text")
    return {"type": "error", "code": code}


def parse_terminal_response(frame: object) -> TerminalOutput | TerminalError:
    """Validate and type a server-to-client terminal frame."""

    if not isinstance(frame, dict):
        raise ProtocolError("invalid_frame", "terminal frame must be an object")
    frame_type = frame.get("type")
    if frame_type == "output":
        if not isinstance(frame.get("data"), str):
            raise ProtocolError("invalid_frame", "output frame requires string data")
        return TerminalOutput(frame["data"])
    if frame_type == "error":
        code = frame.get("code")
        if not isinstance(code, str) or not code:
            raise ProtocolError("invalid_frame", "terminal error code must be text")
        return TerminalError(code)
    raise ProtocolError("invalid_frame", "unsupported terminal response frame")


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
