"""Bounded newline-delimited JSON framing for the local MOOS protocol."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


MAX_FRAME_BYTES = 16 * 1024


@dataclass(frozen=True)
class FrameResult:
    """One decoded object frame or one recoverable framing error."""

    frame: dict[str, Any] | None = None
    error: str | None = None


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
