#!/usr/bin/env python3
"""Verify the public MOOS Protocol-v1 contract and transport-neutral helpers."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "host"))

from moos_protocol import (  # noqa: E402
    CONTROL_OPERATIONS,
    MAX_FRAME_BYTES,
    PROTOCOL_VERSION,
    ProtocolError,
    TerminalClose,
    TerminalInput,
    encode_frame,
    make_control_request,
    make_terminal_error,
    make_terminal_output,
    parse_control_request,
    parse_terminal_frame,
)


def expect_error(function, code):
    try:
        function()
    except ProtocolError as error:
        assert error.code == code, (error.code, code)
    else:
        raise AssertionError(f"expected ProtocolError {code}")


def main():
    assert PROTOCOL_VERSION == 1
    assert CONTROL_OPERATIONS == {
        "status",
        "personal.status",
        "personal.start",
        "personal.stop",
        "personal.terminal.open",
    }
    assert parse_control_request(make_control_request("status")).operation == "status"
    expect_error(lambda: make_control_request(7), "invalid_request")
    expect_error(lambda: make_control_request("exec"), "unknown_operation")
    expect_error(lambda: parse_control_request({"protocolVersion": 2, "operation": "status"}), "unsupported_protocol")
    expect_error(lambda: parse_control_request({"protocolVersion": 1, "operation": "status", "path": "/etc"}), "invalid_request")
    expect_error(lambda: parse_control_request({"protocolVersion": 1, "operation": "exec"}), "unknown_operation")

    assert parse_terminal_frame({"type": "input", "data": "moos-info\n"}) == TerminalInput("moos-info\n")
    assert isinstance(parse_terminal_frame({"type": "close"}), TerminalClose)
    expect_error(lambda: parse_terminal_frame([]), "invalid_frame")
    expect_error(lambda: parse_terminal_frame({"type": "input", "data": 7}), "invalid_frame")
    expect_error(lambda: parse_terminal_frame({"type": "close", "extra": True}), "invalid_frame")

    assert make_terminal_output("hello") == {"type": "output", "data": "hello"}
    assert make_terminal_error("invalid_frame") == {"type": "error", "code": "invalid_frame"}
    assert len(encode_frame({"value": "x" * (MAX_FRAME_BYTES - 20)})) <= MAX_FRAME_BYTES + 1
    try:
        encode_frame({"value": "x" * MAX_FRAME_BYTES})
    except ValueError:
        pass
    else:
        raise AssertionError("oversized frames must be rejected")

    protocol_doc = (REPO_ROOT / "PROTOCOL.md").read_text()
    for required in ("Protocol v1", "personal.terminal.open", "not_running", "No Host `/exec`"):
        assert required in protocol_doc, required

    print("MOOS Protocol v1 contract test: PASS")
    print("  typed control and terminal frames: ok")
    print("  version/field/operation validation: ok")
    print("  bounded encoding and structured errors: ok")
    print("  transport-neutral protocol documentation: ok")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, OSError) as error:
        print("MOOS Protocol v1 contract test: FAIL", file=sys.stderr)
        print(f"  {error}", file=sys.stderr)
        sys.exit(1)
