#!/usr/bin/env python3
"""Render the dependency-free MOOS AltSource icon as a deterministic PNG."""

from __future__ import annotations

import argparse
import os
import struct
import tempfile
import zlib
from pathlib import Path


WIDTH = 1024
HEIGHT = 1024
BACKGROUND = (7, 10, 8)
ACCENT = (55, 230, 138)
MUTED_ACCENT = (20, 87, 54)


def _distance_squared_to_segment(
    x: float,
    y: float,
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
) -> float:
    delta_x = end_x - start_x
    delta_y = end_y - start_y
    length_squared = delta_x * delta_x + delta_y * delta_y
    projection = ((x - start_x) * delta_x + (y - start_y) * delta_y) / length_squared
    projection = max(0.0, min(1.0, projection))
    closest_x = start_x + projection * delta_x
    closest_y = start_y + projection * delta_y
    return (x - closest_x) ** 2 + (y - closest_y) ** 2


def render_pixels() -> bytes:
    rows = bytearray()
    line_radius_squared = 38 * 38
    for y in range(HEIGHT):
        rows.append(0)
        for x in range(WIDTH):
            color = BACKGROUND

            inside_outer = 72 <= x < 952 and 72 <= y < 952
            inside_inner = 88 <= x < 936 and 88 <= y < 936
            if inside_outer and not inside_inner:
                color = MUTED_ACCENT

            vertical = (
                (258 <= x < 334 or 690 <= x < 766)
                and 282 <= y < 742
            )
            left_diagonal = _distance_squared_to_segment(
                x, y, 314, 318, 512, 566
            ) <= line_radius_squared
            right_diagonal = _distance_squared_to_segment(
                x, y, 710, 318, 512, 566
            ) <= line_radius_squared
            if vertical or left_diagonal or right_diagonal:
                color = ACCENT

            rows.extend(color)
    return bytes(rows)


def _chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(kind)
    checksum = zlib.crc32(payload, checksum)
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", checksum & 0xFFFFFFFF)
    )


def render_png() -> bytes:
    header = struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(render_pixels(), level=9))
        + _chunk(b"IEND", b"")
    )


def write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    write_atomic(args.output, render_png())
    print(f"Generated deterministic MOOS distribution icon: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
