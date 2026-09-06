#!/usr/bin/env python3
"""Test the local CLI against a temporary typed moosd socket."""

import ast
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "host"))

from moos_runtime import RuntimeState, RuntimeStatus  # noqa: E402
from moos_protocol import encode_frame  # noqa: E402
from moosd import MoosdService, serve  # noqa: E402


class FakeRuntime:
    def status(self):
        return RuntimeStatus("personal", RuntimeState.RUNNING)

    def start(self):
        return RuntimeStatus("personal", RuntimeState.STARTING)

    def stop(self):
        return RuntimeStatus("personal", RuntimeState.STOPPED)

    def open_terminal(self):
        return FakeChannel()


class FakeChannel:
    def __init__(self):
        self.fd, self.write_fd = os.pipe()

    def fileno(self):
        return self.fd

    def read(self, size=4096):
        return b""

    def write(self, data):
        return os.write(self.write_fd, data)

    def close(self):
        os.close(self.fd)
        os.close(self.write_fd)


def run_cli(socket_path, *arguments, input_text=None):
    return subprocess.run(
        [str(REPO_ROOT / "scripts/moos"), "--socket", str(socket_path), *arguments],
        cwd=REPO_ROOT, capture_output=True, text=True, input=input_text, check=False,
    )


def run_scripted_response(socket_path, response_bytes, *arguments, input_text=None):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(socket_path))
        listener.listen(1)

        def send_response():
            connection, _ = listener.accept()
            with connection:
                connection.recv(4096)
                connection.sendall(response_bytes)

        sender = threading.Thread(target=send_response, daemon=True)
        sender.start()
        result = run_cli(socket_path, *arguments, input_text=input_text)
        sender.join(timeout=2)
        return result


def main():
    with tempfile.TemporaryDirectory() as temporary:
        socket_path = Path(temporary) / "moosd.sock"
        thread = threading.Thread(target=serve, args=(socket_path, MoosdService(FakeRuntime())), daemon=True)
        thread.start()
        deadline = time.monotonic() + 2
        while not socket_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)

        status = run_cli(socket_path, "status")
        assert status.returncode == 0, status.stderr
        assert '"identity": "personal"' in status.stdout

        start = run_cli(socket_path, "personal", "start")
        assert start.returncode == 0, start.stderr
        stop = run_cli(socket_path, "personal", "stop")
        assert stop.returncode == 0, stop.stderr

        terminal = run_cli(socket_path, "personal", "terminal", input_text="")
        assert terminal.returncode == 0, terminal.stderr

        for command, expected in (("start", "starting"), ("stop", "stopped")):
            alias = run_cli(socket_path, command)
            assert alias.returncode == 0, alias.stderr
            assert alias.stdout.strip() == f"Personal MOOS: {expected}"
        shell_alias = run_cli(socket_path, "shell", input_text="")
        assert shell_alias.returncode == 0, shell_alias.stderr
        human = run_cli(socket_path, "status", "--human")
        assert human.returncode == 0 and human.stdout.strip() == "Personal MOOS: running"
        explicit_status = run_cli(socket_path, "personal", "status")
        assert json.loads(explicit_status.stdout)["personal"]["state"] == "running"
        for arguments in (("help",), (), ("--help",)):
            help_result = run_cli(socket_path, *arguments)
            assert help_result.returncode == 0
            for command in ("setup", "doctor", "start", "stop", "shell"):
                assert "moos " + command in help_result.stdout
        for arguments in (("doctor", "--fix"), ("status", "--report"),
                          ("help", "start"), ("status", "--remote"),
                          ("doctor", "--expect-fingerprint", "bad"), ("setup", "start"),
                          ("doctor", "--verbose", "--report"), ("status", "--verbose")):
            invalid_args = run_cli(socket_path, *arguments)
            assert invalid_args.returncode == 2
        no_color_help = run_cli(socket_path, "help", "--no-color")
        assert no_color_help.returncode == 0
        assert "\x1b" not in no_color_help.stdout
        for command in ("start", "stop", "shell"):
            unavailable = run_cli(Path(temporary) / "absent.sock", command, input_text="")
            assert unavailable.returncode == 1
            assert "[NEEDS ATTENTION]" in unavailable.stderr
            assert "absent.sock" not in unavailable.stderr
            assert "moos doctor" in unavailable.stderr
        for topic in ("trust", "local", "remote"):
            guide = run_cli(socket_path, "setup", "--explain", topic)
            assert guide.returncode == 0 and len(guide.stdout) > 100

        direct_qemu = REPO_ROOT / "scripts" / "run-qemu.sh"
        # Diagnosis may inspect launcher metadata, but its subprocess probes
        # must stay read-only; lifecycle aliases are exercised above via moosd.
        tree = ast.parse(Path(REPO_ROOT / "scripts/moos").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "command":
                assert isinstance(node.args[0], ast.List)
                verb = ast.literal_eval(node.args[0].elts[0])
                assert verb in {"systemctl", "getent", "id", "tailscale"}, verb
                action = ast.literal_eval(node.args[0].elts[1])
                assert (verb, action) in {("systemctl", "show"), ("getent", "passwd"),
                                          ("id", "-G"), ("tailscale", "status")}
        assert direct_qemu.exists()

        fragmented_path = Path(temporary) / "fragmented.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(fragmented_path))
            listener.listen(1)

            def send_fragmented_response():
                connection, _ = listener.accept()
                with connection:
                    connection.recv(4096)
                    frame = encode_frame(
                        {
                            "protocolVersion": 1,
                            "ok": True,
                            "operation": "status",
                            "data": {
                                "personal": {
                                    "identity": "personal",
                                    "state": "running",
                                    "result": "success",
                                }
                            },
                            "events": [],
                        }
                    )
                    for byte in frame:
                        connection.sendall(bytes([byte]))

            sender = threading.Thread(target=send_fragmented_response, daemon=True)
            sender.start()
            fragmented = run_cli(fragmented_path, "status")
            assert fragmented.returncode == 0, fragmented.stderr

        combined_path = Path(temporary) / "combined.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(combined_path))
            listener.listen(1)

            def send_combined_terminal_frames():
                connection, _ = listener.accept()
                with connection:
                    connection.recv(4096)
                    connection.sendall(
                        encode_frame(
                            {
                                "protocolVersion": 1,
                                "ok": True,
                                "operation": "personal.terminal.open",
                                "data": {"channel": "serial-console"},
                                "events": [],
                            }
                        )
                        + encode_frame({"type": "output", "data": "FIRST_OUTPUT\n"})
                    )
                    connection.recv(4096)

            sender = threading.Thread(
                target=send_combined_terminal_frames, daemon=True
            )
            sender.start()
            combined = run_cli(combined_path, "personal", "terminal", input_text="")
            assert combined.returncode == 0, combined.stderr
            assert "FIRST_OUTPUT" in combined.stdout

        valid_status = {
            "protocolVersion": 1,
            "ok": True,
            "operation": "status",
            "data": {
                "personal": {
                    "identity": "personal",
                    "state": "running",
                    "result": None,
                }
            },
            "events": [],
        }
        future_path = Path(temporary) / "future-fields.sock"
        future = run_scripted_response(
            future_path,
            encode_frame(valid_status | {"future": {"ignored": True}}),
            "status",
        )
        assert future.returncode == 0, future.stderr

        invalid_responses = (
            valid_status | {"protocolVersion": True},
            valid_status | {"protocolVersion": 1.0},
            valid_status | {"ok": "yes"},
            {key: value for key, value in valid_status.items() if key != "data"},
        )
        for index, invalid_response in enumerate(invalid_responses):
            invalid_path = Path(temporary) / f"invalid-{index}.sock"
            invalid = run_scripted_response(
                invalid_path, encode_frame(invalid_response), "status"
            )
            assert invalid.returncode == 1, (invalid.stdout, invalid.stderr)
            assert "daemon connection failed" in invalid.stderr

        unknown_error_path = Path(temporary) / "unknown-error.sock"
        unknown_error = run_scripted_response(
            unknown_error_path,
            encode_frame(
                {
                    "protocolVersion": 1,
                    "ok": False,
                    "error": {"code": "future_error", "message": "Future failure"},
                }
            ),
            "status",
        )
        assert unknown_error.returncode == 1
        assert "future_error: Future failure" in unknown_error.stderr

        terminal_version_path = Path(temporary) / "terminal-version.sock"
        bad_terminal_ack = run_scripted_response(
            terminal_version_path,
            encode_frame(
                {
                    "protocolVersion": 2,
                    "ok": True,
                    "operation": "personal.terminal.open",
                    "data": {"channel": "serial-console"},
                    "events": [],
                }
            ),
            "personal",
            "terminal",
            input_text="",
        )
        assert bad_terminal_ack.returncode == 1
        assert "protocolVersion must be integer 1" in bad_terminal_ack.stderr

        truncated_path = Path(temporary) / "truncated.sock"
        truncated = run_scripted_response(
            truncated_path, b'{"protocolVersion":1', "status"
        )
        assert truncated.returncode == 1
        assert "truncated protocol frame" in truncated.stderr

    print("MOOS local CLI test: PASS")
    print("  status/start/stop use moosd: ok")
    print("  terminal command opens the daemon channel: ok")
    print("  fragmented response and combined terminal ACK/output: ok")
    print("  malformed/versioned/truncated daemon responses are rejected: ok")
    print("  unknown response fields and error codes remain compatible: ok")
    print("  CLI contains no QEMU lifecycle logic: ok")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, OSError) as error:
        print("MOOS local CLI test: FAIL", file=sys.stderr)
        print(f"  {error}", file=sys.stderr)
        sys.exit(1)
