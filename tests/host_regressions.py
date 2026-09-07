#!/usr/bin/env python3
"""Unprivileged behavioral regressions for the host audit fixes."""
import configparser
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import pty
import socket
import subprocess
import sys
import tempfile
import termios
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'host'))
import moosd
from moos_protocol import FrameDecoder

loader = importlib.machinery.SourceFileLoader('moos_cli', str(ROOT / 'scripts/moos'))
spec = importlib.util.spec_from_loader(loader.name, loader)
cli = importlib.util.module_from_spec(spec)
sys.modules[loader.name] = cli
loader.exec_module(cli)


class HostRegressions(unittest.TestCase):
    def test_real_unit_policy(self):
        for name in ('moosd', 'moos-gateway'):
            unit = configparser.ConfigParser(strict=False, interpolation=None)
            unit.read(ROOT / f'systemd/{name}.service')
            service = unit['Service']
            self.assertEqual(service['TasksMax'], '64')
            self.assertEqual(service['MemoryMax'], '128M')
            self.assertEqual(service['CPUQuota'], '50%')
            if name == 'moos-gateway':
                self.assertEqual(service.getboolean('PrivatePIDs'), True)

    def test_idle_client_disconnects(self):
        server, client = socket.socketpair()
        with client, patch.object(moosd, 'CLIENT_IDLE_TIMEOUT', 0.04):
            worker = threading.Thread(target=moosd._handle_client, args=(server, object()))
            worker.start()
            client.settimeout(1)
            self.assertEqual(client.recv(1), b'')
            worker.join(1)
            self.assertFalse(worker.is_alive())

    def test_connection_limit_and_slot_recovery(self):
        pairs = [socket.socketpair() for _ in range(6)]
        resume = threading.Event()
        accepted = threading.Event()

        class Listener:
            index = 0
            def accept(self):
                if self.index == 5:
                    resume.wait(2)
                if self.index == 6:
                    accepted.set()
                    raise StopIteration
                connection = pairs[self.index][0]
                self.index += 1
                return connection, None

        def run():
            try:
                moosd.serve(Path('/unused'), object(), listener=Listener())
            except StopIteration:
                pass

        with patch.object(moosd, 'CLIENT_IDLE_TIMEOUT', 0.2):
            worker = threading.Thread(target=run)
            worker.start()
            try:
                pairs[4][1].settimeout(1)
                self.assertEqual(pairs[4][1].recv(1), b'')
                for _, client in pairs[:4]:
                    client.settimeout(1)
                    self.assertEqual(client.recv(1), b'')
                resume.set()
                self.assertTrue(accepted.wait(1))
                pairs[5][1].settimeout(0.03)
                with self.assertRaises(socket.timeout):
                    pairs[5][1].recv(1)
                pairs[5][1].settimeout(1)
                self.assertEqual(pairs[5][1].recv(1), b'')
            finally:
                resume.set()
                worker.join(2)
                for server, client in pairs:
                    server.close()
                    client.close()

    def test_atomic_store_initialization_failure(self):
        source = (ROOT / 'scripts/setup-gateway.sh').read_text()
        start = source.index('    DEVICE_TEMP=$(mktemp')
        snippet = source[start:source.index('\nelse', start)]
        # Run the actual initialization sequence against temporary storage;
        # ownership changes are stubbed because this test never needs root.
        for fail_move in (True, False):
            with self.subTest(fail_move=fail_move), tempfile.TemporaryDirectory() as temporary:
                script = 'set -eu\nSTATE_ROOT=$1\nGATEWAY_GROUP=unused\nchown() { :; }\n'
                if fail_move:
                    script += 'mv() { return 1; }\n'
                result = subprocess.run(['sh', '-c', script + snippet, 'test', temporary], capture_output=True)
                active = Path(temporary) / 'devices.json'
                if fail_move:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(active.exists())
                else:
                    import json
                    self.assertEqual(result.returncode, 0)
                    self.assertEqual(json.loads(active.read_text()), {'devices': [], 'schemaVersion': 1})
                self.assertFalse(list(Path(temporary).glob('.devices-init.*')))

    def test_terminal_split_utf8(self):
        server, client = socket.socketpair()
        guest, writer = socket.socketpair()

        class Channel:
            def fileno(self): return guest.fileno()
            def read(self, size): return guest.recv(size)
            def close(self): guest.close()

        with client, writer:
            worker = threading.Thread(target=moosd._serve_terminal, args=(server, Channel()))
            worker.start()
            for byte in 'Grüße 🌱'.encode():
                writer.sendall(bytes([byte]))
                time.sleep(0.002)
            writer.shutdown(socket.SHUT_WR)
            decoder = FrameDecoder()
            output = ''
            client.settimeout(1)
            while output != 'Grüße 🌱':
                for result in decoder.feed(client.recv(4096)):
                    self.assertIsNone(result.error)
                    output += result.frame['data']
            worker.join(1)
            self.assertFalse(worker.is_alive())
        server.close()

    def test_raw_terminal_restored_on_failure(self):
        master, slave = pty.openpty()
        try:
            with os.fdopen(os.dup(slave), 'r') as stdin, patch.object(cli.sys, 'stdin', stdin):
                before = termios.tcgetattr(slave)
                with self.assertRaises(ValueError):
                    with cli.raw_terminal():
                        active = termios.tcgetattr(slave)
                        self.assertFalse(active[3] & (termios.ECHO | termios.ICANON | termios.ISIG))
                        raise ValueError('disconnect')
                self.assertEqual(termios.tcgetattr(slave), before)
        finally:
            os.close(master)
            os.close(slave)

    def test_runtime_activation_rolls_back_each_move_failure(self):
        source = (ROOT / 'scripts/stage-instance.sh').read_text()
        cleanup = source[source.index("exchange_runtime() {"):source.index('if [ ! -x "$STAGED_QEMU" ]')]
        activation = source[source.index('if [ -n "$QEMU_STAGE_DIR" ]; then\n    if [ -e'):source.index('\necho "MOOS Instance staged:')]
        for failure in (1, 2, 3, 0, 4):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                runtime = root / 'runtime'
                runtime.mkdir()
                (runtime / 'qemu-host').mkdir()
                (runtime / 'qemu-host/old').touch()
                (runtime / '.qemu-host-previous.stale').mkdir()
                (runtime / 'stage').mkdir()
                (runtime / 'stage/new').touch()
                (root / 'instance-stage').mkdir()
                script = '''set -eu
STATE_ROOT=$1
RUNTIME_QEMU_ROOT="$STATE_ROOT/runtime/qemu-host"
INSTANCE_DIR="$STATE_ROOT/instance"
FAIL_MOVE=$2
''' + cleanup.replace('exchange_runtime() {', 'real_exchange_runtime() {', 1) + '''
QEMU_STAGE_DIR="$STATE_ROOT/runtime/stage"
INSTANCE_STAGE_DIR="$STATE_ROOT/instance-stage"
MOVES=0
exchange_runtime() {
    MOVES=$((MOVES + 1))
    if [ "$MOVES" -eq "$FAIL_MOVE" ]; then return 1; fi
    real_exchange_runtime "$@"
    if [ "$FAIL_MOVE" -eq 4 ]; then kill -KILL "$$"; fi
}
mv() {
    MOVES=$((MOVES + 1))
    if [ "$MOVES" -eq "$FAIL_MOVE" ]; then return 1; fi
    command mv "$@"
}
''' + activation
                result = subprocess.run(['sh', '-c', script, 'test', str(root), str(failure)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0 if failure == 0 else (-9 if failure == 4 else 1), result.stderr)
                self.assertTrue((runtime / 'qemu-host' / ('new' if failure in (0, 4) else 'old')).exists())
                if failure == 4:
                    self.assertTrue(any(path.joinpath('old').exists() for path in runtime.glob('.qemu-host-previous.*')))
                if failure == 0:
                    self.assertEqual(len(list(runtime.glob('.qemu-host-previous.*'))), 1)

    def test_privileged_environments_and_action_pins(self):
        import re
        for name in ('setup-control-plane', 'setup-gateway', 'setup-runtime-user', 'stage-instance', 'run-instance'):
            text = (ROOT / f'scripts/{name}.sh').read_text()
            prefix = text[:text.index('if [ "$(id -u)"')]
            self.assertIn("PATH='/usr/sbin:/usr/bin:/sbin:/bin'", prefix)
            self.assertIn('unset CDPATH ENV BASH_ENV PYTHONHOME PYTHONPATH', prefix)
        for path in (ROOT / '.github/workflows').glob('*.yml'):
            for ref in re.findall(r'uses:\s+([^\s]+)', path.read_text()):
                if not ref.startswith('./'):
                    self.assertRegex(ref, r'^[\w-]+/[\w-]+@[a-f0-9]{40}$')

    def test_release_verification_tests_do_not_create_or_use_private_keys(self):
        for relative in ('tests/admin_release.py', 'tests/operator_ux.py'):
            source = (ROOT / relative).read_text()
            self.assertNotIn('"genpkey"', source, relative)
            self.assertNotRegex(source, r'["\x27]-sign["\x27]', relative)


if __name__ == '__main__':
    unittest.main()
