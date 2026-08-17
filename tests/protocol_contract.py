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
    RUNTIME_STATES,
    ControlFailure,
    ControlSuccess,
    FrameDecoder,
    ProtocolError,
    TerminalClose,
    TerminalError,
    TerminalInput,
    TerminalOutput,
    encode_frame,
    make_control_error,
    make_control_request,
    make_control_success,
    make_terminal_close,
    make_terminal_error,
    make_terminal_input,
    make_terminal_output,
    parse_control_request,
    parse_control_response,
    parse_terminal_frame,
    parse_terminal_response,
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
    for invalid_version in (2, True, 1.0, "1", None):
        expect_error(
            lambda value=invalid_version: parse_control_request(
                {"protocolVersion": value, "operation": "status"}
            ),
            "unsupported_protocol",
        )
    expect_error(lambda: parse_control_request({"protocolVersion": 1, "operation": "status", "path": "/etc"}), "invalid_request")
    expect_error(lambda: parse_control_request({"protocolVersion": 1, "operation": "exec"}), "unknown_operation")

    for state in RUNTIME_STATES:
        frame = make_control_success(
            "personal.status",
            {"personal": {"identity": "personal", "state": state}},
        )
        response = parse_control_response(
            frame | {"future": True}, expected_operation="personal.status"
        )
        assert isinstance(response, ControlSuccess)
        assert response.data.personal.state == state
        assert response.data.personal.result is None

    success = {
        "protocolVersion": 1,
        "ok": True,
        "operation": "status",
        "data": {
            "personal": {
                "identity": "personal",
                "state": "running",
                "result": "success",
                "futureStatus": 1,
            },
            "futureData": True,
        },
        "events": [{"futureEvent": True}],
        "futureTopLevel": True,
    }
    response = parse_control_response(success, expected_operation="status")
    assert isinstance(response, ControlSuccess)
    assert response.data.as_dict()["personal"]["state"] == "running"
    expect_error(
        lambda: parse_control_response(success, expected_operation="personal.status"),
        "invalid_response",
    )
    for invalid_version in (2, True, 1.0, "1", None):
        expect_error(
            lambda value=invalid_version: parse_control_response(
                success | {"protocolVersion": value}
            ),
            "unsupported_protocol",
        )
    expect_error(
        lambda: parse_control_response(success | {"ok": "yes"}),
        "invalid_response",
    )
    expect_error(
        lambda: parse_control_response(success | {"data": {}}),
        "invalid_response",
    )
    expect_error(
        lambda: parse_control_response(success | {"events": {}}),
        "invalid_response",
    )
    bad_state = success | {
        "data": {"personal": {"identity": "personal", "state": "booting"}}
    }
    expect_error(lambda: parse_control_response(bad_state), "invalid_response")

    failure = parse_control_response(
        make_control_error("future_error", "A future failure")
    )
    assert failure == ControlFailure("future_error", "A future failure")
    expect_error(
        lambda: parse_control_response(
            {"protocolVersion": 1, "ok": False, "error": {"code": "broken"}}
        ),
        "invalid_response",
    )

    assert parse_terminal_frame({"type": "input", "data": "moos-info\n"}) == TerminalInput("moos-info\n")
    assert isinstance(parse_terminal_frame({"type": "close"}), TerminalClose)
    assert make_terminal_input("moos-info\n") == {"type": "input", "data": "moos-info\n"}
    assert make_terminal_close() == {"type": "close"}
    expect_error(lambda: parse_terminal_frame([]), "invalid_frame")
    expect_error(lambda: parse_terminal_frame({"type": "input", "data": 7}), "invalid_frame")
    expect_error(lambda: parse_terminal_frame({"type": "close", "extra": True}), "invalid_frame")

    assert make_terminal_output("hello") == {"type": "output", "data": "hello"}
    assert make_terminal_error("invalid_frame") == {"type": "error", "code": "invalid_frame"}
    assert parse_terminal_response({"type": "output", "data": "hello"}) == TerminalOutput("hello")
    assert parse_terminal_response({"type": "error", "code": "future_error"}) == TerminalError("future_error")
    assert parse_terminal_response(
        {"type": "output", "data": "hello", "future": True}
    ) == TerminalOutput("hello")
    expect_error(lambda: parse_terminal_response({"type": "output", "data": 7}), "invalid_frame")

    empty_size = len(encode_frame({"value": ""})) - 1
    exact_frame = encode_frame({"value": "x" * (MAX_FRAME_BYTES - empty_size)})
    assert len(exact_frame) == MAX_FRAME_BYTES + 1
    try:
        encode_frame({"value": "x" * (MAX_FRAME_BYTES - empty_size + 1)})
    except ValueError:
        pass
    else:
        raise AssertionError("oversized frames must be rejected")

    decoder = FrameDecoder()
    assert decoder.feed(b'{"protocolVersion":1') == []
    assert decoder.finish().error == "truncated_frame"

    protocol_doc = (REPO_ROOT / "PROTOCOL.md").read_text()
    for required in ("Protocol v1", "personal.terminal.open", "not_running", "No Host `/exec`"):
        assert required in protocol_doc, required

    print("MOOS Protocol v1 contract test: PASS")
    print("  typed request, response, and terminal frames: ok")
    print("  strict version/type/field/operation validation: ok")
    print("  compatibility, exact size boundary, and truncated framing: ok")
    print("  transport-neutral protocol documentation: ok")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, OSError) as error:
        print("MOOS Protocol v1 contract test: FAIL", file=sys.stderr)
        print(f"  {error}", file=sys.stderr)
        sys.exit(1)
