#!/usr/bin/env python3
"""Operator diagnosis, bounded probes and interaction without Host mutations."""
from __future__ import annotations

import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import socket
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("moos_operator", str(ROOT / "scripts/moos"))
spec = importlib.util.spec_from_loader(loader.name, loader)
moos = importlib.util.module_from_spec(spec)
sys.modules[loader.name] = moos
loader.exec_module(moos)


class FixtureProbe:
    def __init__(self):
        self.supported = True
        self.limits = True
        self.missing = set()
        self.files = {}
        self.account = "present"
        self.release = "present"
        self.fingerprint = "a" * 64
        self.state = "running"
        self.gateway = "active"
        self.tail = "connected"
        self.tail_calls = 0
        self.trusted_requirements = {}
        self.trusted_parent_requirements = {}

    def host_supported(self): return self.supported
    def controllers(self): return self.limits
    def dependency(self, name): return name not in self.missing
    def trusted_file(self, path, *, required_mode=0, required_parent_mode=0):
        self.trusted_requirements[path] = required_mode
        self.trusted_parent_requirements[path] = required_parent_mode
        return self.files.get(path, "present")
    def runtime_account(self): return self.account
    def admin_release(self): return self.release
    def public_fingerprint(self): return self.fingerprint
    def personal(self, path): return self.state
    def service(self, name): return self.gateway
    def tailscale(self):
        self.tail_calls += 1
        return self.tail


def collect(probe, **kwargs):
    return {item.id: item for item in moos.collect_checks(probe, Path("/unused"), **kwargs)}


def options(command="doctor", **kwargs):
    return argparse.Namespace(command=command, explain=None, remote=False,
                              non_interactive=True, socket=Path("/unused"),
                              expect_fingerprint=None, report=False, verbose=False, no_color=False, **kwargs)


class DiagnosisTests(unittest.TestCase):
    def setUp(self):
        self.probe = FixtureProbe()

    def test_healthy_observations_and_explicit_limits(self):
        checks = collect(self.probe, expected_fingerprint="a" * 64)
        self.assertFalse(any(c.state in {"issue", "unknown"} for c in checks.values()))
        self.assertEqual(checks["fingerprint"].state, "ok")
        self.assertEqual(checks["devices"].state, "info")
        self.assertIn("not audited", checks["staging"].summary)
        self.assertIn("not a signature", checks["release"].next_step)
        self.assertIn("outside the checkout", checks["helper"].next_step)

    def test_personal_states_and_permissions(self):
        for state in ("starting", "running", "stopping", "stopped", "failed", "unknown", "denied", "unavailable"):
            with self.subTest(state=state):
                self.probe.state = state
                checks = collect(self.probe)
                if state in {"unknown", "denied", "unavailable"}:
                    self.assertNotEqual(checks["control"].state, "ok")
                    self.assertNotIn("personal", checks)
                else:
                    self.assertEqual(checks["control"].state, "ok")
                    self.assertEqual(checks["personal"].state, "issue" if state == "failed" else "ok")
        self.probe.state = "stopped"
        self.assertIn("does not prove", collect(self.probe)["personal"].next_step)

    def test_missing_and_malformed_host(self):
        self.probe.supported = False
        self.probe.limits = False
        self.probe.missing = {"bwrap", "systemd-run"}
        self.probe.account = "unsafe"
        self.probe.release = "unsafe"
        checks = collect(self.probe)
        for key in ("host", "limits", "dependencies", "account", "release"):
            self.assertEqual(checks[key].state, "issue")
            self.assertTrue(checks[key].next_step)

    def test_runtime_and_installer_executability_are_required_for_readiness(self):
        checks = collect(self.probe)
        self.assertEqual(self.probe.trusted_requirements["/usr/lib/moos/run-qemu.sh"], 0o555)
        self.assertEqual(self.probe.trusted_requirements["/usr/libexec/moos/moos-admin-installer"], 0o555)
        self.assertEqual(self.probe.trusted_requirements["/etc/moos/trust/admin-release.pem"], 0)
        self.assertEqual(self.probe.trusted_parent_requirements["/usr/lib/moos/run-qemu.sh"], 0o111)
        self.assertEqual(
            self.probe.trusted_parent_requirements["/usr/libexec/moos/moos-admin-installer"], 0)
        self.assertEqual(checks["runtime"].state, "ok")
        self.assertEqual(checks["helper"].state, "info")

        for path, check_id in (
            ("/usr/lib/moos/run-qemu.sh", "runtime"),
            ("/usr/libexec/moos/moos-admin-installer", "helper"),
        ):
            with self.subTest(path=path):
                self.probe.files[path] = "unsafe"
                self.assertNotIn(collect(self.probe)[check_id].state, {"ok", "info"})
                self.probe.files.pop(path)

    def test_invalid_runtime_account_contract_never_reports_ready(self):
        for account, expected in (("unsafe", "issue"), ("unknown", "unknown"), ("missing", "issue")):
            with self.subTest(account=account):
                self.probe.account = account
                self.assertEqual(collect(self.probe)["account"].state, expected)
        self.probe.account = "present"
        self.assertEqual(collect(self.probe)["account"].state, "ok")

    def test_trust_never_inferred_or_replaced(self):
        key = "/etc/moos/trust/admin-release.pem"
        for state in ("missing", "unsafe", "unknown"):
            self.probe.files[key] = state
            checks = collect(self.probe, expected_fingerprint="a" * 64)
            self.assertNotEqual(checks["trust"].state, "ok")
            self.assertNotIn("fingerprint", checks)
        self.probe.files.clear()
        self.assertEqual(collect(self.probe)["fingerprint"].state, "info")
        checks = collect(self.probe, expected_fingerprint="b" * 64)
        self.assertEqual(checks["fingerprint"].state, "issue")
        self.assertIn("do not replace", checks["fingerprint"].next_step)
        self.probe.fingerprint = None
        self.assertEqual(collect(self.probe)["fingerprint"].state, "unknown")

    def test_optional_remote_and_configured_failure(self):
        self.probe.gateway = "missing"
        self.probe.tail = "unavailable"
        checks = collect(self.probe)
        self.assertNotIn("tailscale", checks)
        self.assertEqual(self.probe.tail_calls, 0)
        self.assertEqual(collect(self.probe, remote=True)["tailscale"].state, "issue")
        for gateway in ("inactive", "failed", "activating", "deactivating", "unknown"):
            self.probe.gateway = gateway
            self.assertNotEqual(collect(self.probe)["gateway"].state, "ok")
        for tail in ("unavailable", "disconnected", "unknown", "unhealthy"):
            self.probe.tail = tail
            self.assertNotEqual(collect(self.probe)["tailscale"].state, "ok")

    def test_report_contains_only_curated_facts(self):
        args = options()
        args.report = True
        output = io.StringIO()
        with patch.object(moos, "HostProbe", return_value=self.probe), contextlib.redirect_stdout(output):
            self.assertEqual(moos.diagnose(args), 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["schemaVersion"], 1)
        self.assertFalse(report["attentionNeeded"])
        self.assertNotIn("a" * 64, output.getvalue())
        self.assertNotIn("/unused", output.getvalue())
        self.probe.state = "denied"
        with patch.object(moos, "HostProbe", return_value=self.probe), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(moos.diagnose(args), 1)

    def test_setup_explains_trust_and_prioritizes_it(self):
        self.probe.files["/etc/moos/trust/admin-release.pem"] = "missing"
        self.probe.state = "denied"
        output = io.StringIO()
        with patch.object(moos, "HostProbe", return_value=self.probe), contextlib.redirect_stdout(output):
            self.assertEqual(moos.diagnose(options("setup")), 1)
        self.assertIn("[BLOCKED] Trusted Host setup", output.getvalue())
        self.assertIn("setup --explain trust", output.getvalue())
        self.assertNotIn("independently provisioned", output.getvalue())
        self.assertIn("private key", moos.GUIDES["trust"])
        self.assertNotIn("Choose [1]", output.getvalue())

    def test_choice_default_remote_explain_invalid_and_eof(self):
        for answers, remote in (([""], False), (["1"], False), (["2"], True), (["bad", "3", "2"], True)):
            iterator = iter(answers)
            lines = []
            self.assertEqual(moos.choose_remote(lambda _: next(iterator), lines.append), remote)
            if "3" in answers:
                self.assertIn("Remote status — optional", "\n".join(lines))
        def eof(_): raise EOFError()
        self.assertFalse(moos.choose_remote(eof, lambda _: None))

    def test_setup_does_not_prompt_for_existing_gateway(self):
        args = options("setup")
        args.non_interactive = False
        with patch.object(moos, "HostProbe", return_value=self.probe), \
                patch.object(sys.stdin, "isatty", return_value=True), \
                patch.object(sys.stdout, "isatty", return_value=True), \
                patch.object(moos, "choose_remote", side_effect=AssertionError("unexpected prompt")), \
                patch("builtins.print"):
            self.assertEqual(moos.diagnose(args), 0)


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.probe = FixtureProbe()

    def items(self):
        return {item.id: item for item in moos.health_items(list(collect(self.probe).values()))}

    def render(self, **kwargs):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            moos.render_checks(list(collect(self.probe).values()), **kwargs)
        return output.getvalue()

    def test_severity_reflects_impact_and_does_not_certify_trust(self):
        items = self.items()
        for key in ("host", "runtime", "control", "personal", "gateway", "tailscale"):
            self.assertEqual(items[key].severity, "ready")
        self.assertEqual(items["trust"].severity, "info")
        self.assertEqual(items["devices"].severity, "info")
        self.assertEqual(items["runtime"].title, "Runtime basics")
        self.assertEqual(items["runtime"].message, "Required local components are present.")
        self.assertEqual(items["devices"].title, "Device pairing")
        self.assertIn("Not verified by this local check.", items["devices"].message)
        self.assertIn("Confirm remote access from a paired iPhone.", items["devices"].message)
        self.probe.state = "denied"
        self.assertEqual(self.items()["control"].severity, "attention")
        self.probe.state = "unknown"
        self.assertEqual(self.items()["control"].severity, "attention")
        self.probe.gateway = "failed"
        self.probe.tail = "disconnected"
        self.assertEqual(self.items()["gateway"].severity, "attention")
        self.assertEqual(self.items()["tailscale"].severity, "attention")
        self.probe.supported = False
        self.assertEqual(self.items()["host"].severity, "blocked")
        self.probe.account = "unsafe"
        self.assertEqual(self.items()["runtime"].severity, "blocked")
        self.probe.account = "unknown"
        self.assertEqual(self.items()["runtime"].severity, "attention")

    def test_stopped_and_transitions_are_not_failures(self):
        for state, expected in (("stopped", "info"), ("starting", "attention"),
                                ("stopping", "attention"), ("failed", "blocked")):
            self.probe.state = state
            item = self.items()["personal"]
            self.assertEqual(item.severity, expected)
            self.assertTrue(item.action)
        self.probe.state = "stopped"
        self.assertIn("next start has not been checked", self.items()["personal"].message)

    def test_grouped_trust_and_one_action_per_shared_procedure(self):
        self.probe.files = {
            "/usr/libexec/moos/moos-admin-installer": "missing",
            "/etc/moos/trust/admin-release.pem": "missing",
        }
        self.probe.release = "missing"
        self.probe.state = "denied"
        self.probe.account = "missing"
        text = self.render()
        self.assertEqual(text.count("[BLOCKED] Trusted Host setup"), 1)
        self.assertEqual(text.count("Next: moos setup --explain trust"), 1)
        self.assertEqual(text.count("Next: moos setup --explain local"), 1)
        self.assertIn("Trusted MOOS installer: missing", text)
        self.assertIn("Public release key: missing", text)
        self.assertIn("Host installation: needs a safety check or setup", text)
        for jargon in ("moos-control", "administrator release", "systemd", "Unix socket", "runtime identity"):
            self.assertNotIn(jargon, text)

    def test_mismatch_stop_and_individual_details_survive_grouping(self):
        checks = list(collect(self.probe, expected_fingerprint="b" * 64).values())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            moos.render_checks(checks, verbose=True)
        text = output.getvalue()
        self.assertIn("Stop installing updates; do not replace the key.", text)
        self.assertIn("[issue] fingerprint: Release trust fingerprint MISMATCH", text)
        self.assertIn("Next: moos setup --explain trust", text)
        self.assertNotIn("b" * 64, text)
        self.assertNotIn("a" * 64, text)

    def test_all_attention_items_have_an_action(self):
        self.probe.supported = False
        self.probe.account = "unsafe"
        self.probe.state = "failed"
        self.probe.release = "unknown"
        self.probe.gateway = "unknown"
        self.probe.tail = "unavailable"
        for item in self.items().values():
            if item.severity in {"attention", "blocked"}:
                self.assertTrue(item.action, item.id)

    def test_output_style_always_has_text_labels(self):
        self.probe.state = "denied"
        self.probe.release = "missing"
        plain = self.render()
        fancy = self.render(symbols=True)
        for label in ("[READY]", "[NEEDS ATTENTION]", "[BLOCKED]", "[INFO]"):
            self.assertIn(label, plain)
            self.assertIn(label, fancy)
        self.assertNotIn("\x1b", plain + fancy)
        self.assertNotIn("🟢", plain)
        self.assertIn("🟢", fancy)
        self.assertIn("🟡", fancy)
        self.assertIn("🔴", fancy)

    def test_noninteractive_no_color_and_dumb_terminals(self):
        class Terminal:
            encoding = "utf-8"
            def isatty(self): return True
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(moos.terminal_symbols(Terminal()))
            self.assertFalse(moos.terminal_symbols(io.StringIO()))
            for name, value in (("NO_COLOR", ""), ("CI", "true"), ("TERM", "dumb")):
                with patch.dict(os.environ, {name: value}):
                    self.assertFalse(moos.terminal_symbols(Terminal()))
            Terminal.encoding = "ascii"
            self.assertFalse(moos.terminal_symbols(Terminal()))
        args = options("setup")
        args.no_color = True
        output = io.StringIO()
        with patch.object(moos, "HostProbe", return_value=self.probe), \
                patch.object(moos, "checkout_cli_differs", return_value=False), \
                patch.object(moos, "terminal_symbols", return_value=True), \
                contextlib.redirect_stdout(output):
            self.assertEqual(moos.diagnose(args), 0)
        self.assertNotIn("🌱", output.getvalue())
        self.assertNotIn("🟢", output.getvalue())

    def test_report_is_exactly_observations_and_does_not_probe_cli(self):
        checks = moos.collect_checks(self.probe, Path("/unused"))
        expected = {"schemaVersion": 1, "attentionNeeded": False,
                    "checks": [moos.asdict(check) for check in checks]}
        args = options()
        args.report = True
        output = io.StringIO()
        with patch.object(moos, "HostProbe", return_value=self.probe), \
                patch.object(moos, "checkout_cli_differs", side_effect=AssertionError("report must not inspect CLI")), \
                contextlib.redirect_stdout(output):
            self.assertEqual(moos.diagnose(args), 0)
        self.assertEqual(json.loads(output.getvalue()), expected)
        self.assertNotIn("[READY]", output.getvalue())
        self.assertNotIn("severity", output.getvalue())
        self.assertEqual(checks, moos.collect_checks(self.probe, Path("/unused")))

    def test_checkout_notice_and_commands_are_consistent(self):
        self.probe.release = "missing"
        output = io.StringIO()
        with patch.object(moos, "HostProbe", return_value=self.probe), \
                patch.object(moos, "checkout_cli_differs", return_value=True), \
                contextlib.redirect_stdout(output):
            self.assertEqual(moos.diagnose(options("setup")), 1)
        text = output.getvalue()
        self.assertIn("differs from this checkout", text)
        self.assertNotRegex(text, r"\bolder\b")
        self.assertNotIn("Next: moos ", text)
        self.assertIn("Next: ./scripts/moos setup --explain trust", text)
        args = options("setup")
        args.explain = "trust"
        with patch.object(moos, "checkout_cli_differs", return_value=True), contextlib.redirect_stdout(output):
            self.assertEqual(moos.diagnose(args), 0)
        self.assertIn("./scripts/moos doctor --expect-fingerprint", output.getvalue())

    def test_transitions_and_cli_notice_preserve_existing_exit_codes(self):
        for state in ("starting", "stopping", "stopped"):
            self.probe.state = state
            output = io.StringIO()
            with patch.object(moos, "HostProbe", return_value=self.probe), \
                    patch.object(moos, "checkout_cli_differs", return_value=True), \
                    contextlib.redirect_stdout(output):
                self.assertEqual(moos.diagnose(options()), 0)
            self.assertIn("[NEEDS ATTENTION]", output.getvalue())
        self.probe.state = "denied"
        with patch.object(moos, "HostProbe", return_value=self.probe), \
                patch.object(moos, "checkout_cli_differs", return_value=False), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(moos.diagnose(options()), 1)

    def test_cli_comparison_is_bounded_read_only_and_has_no_age_guess(self):
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "moos"
            sentinel = Path(temporary) / "must-not-exist"
            candidate.write_text("#!/bin/sh\ntouch " + str(sentinel))
            candidate.chmod(0o755)
            with patch.dict(os.environ, {"PATH": temporary}):
                self.assertTrue(moos.checkout_cli_differs())
                self.assertFalse(sentinel.exists())
                candidate.write_bytes((ROOT / "scripts/moos").read_bytes())
                self.assertFalse(moos.checkout_cli_differs())
                candidate.unlink()
                candidate.symlink_to(ROOT / "scripts/moos")
                self.assertFalse(moos.checkout_cli_differs())
                candidate.unlink()
                self.assertFalse(moos.checkout_cli_differs())
            candidate.write_bytes(b"x" * (1024 * 1024 + 1))
            self.assertIsNone(moos.cli_digest(candidate))
            candidate.unlink()
            os.mkfifo(candidate)
            self.assertIsNone(moos.cli_digest(candidate))
            candidate.unlink()
            candidate.symlink_to(candidate)
            self.assertIsNone(moos.cli_digest(candidate))
        with patch.object(moos, "__file__", "/usr/lib/moos/control-current/moos"), \
                patch.object(moos.shutil, "which", side_effect=AssertionError("installed CLI must not compare checkout")):
            self.assertFalse(moos.checkout_cli_differs())
        with patch.object(os, "open", side_effect=PermissionError):
            self.assertIsNone(moos.cli_digest(ROOT / "scripts/moos"))


class ProbeTests(unittest.TestCase):
    def test_tailscale_json(self):
        healthy = {"BackendState": "Running", "TailscaleIPs": ["100.64.0.1", "fd7a:115c:a1e0::1"], "Health": []}
        self.assertEqual(moos.tailscale_state(json.dumps(healthy)), "connected")
        for backend in ("Stopped", "NeedsLogin", "NeedsMachineAuth"):
            self.assertEqual(moos.tailscale_state(json.dumps({"BackendState": backend})), "disconnected")
        for invalid in (b"bad", b"[]", b"null", b'{}', b'"secret"', b"\xff", b"[" * 2000):
            self.assertEqual(moos.tailscale_state(invalid), "unknown")
        for replacement in ({"TailscaleIPs": []}, {"TailscaleIPs": ["8.8.8.8"]}, {"Health": ["SECRET"]}, {"Health": "bad"}):
            self.assertEqual(moos.tailscale_state(json.dumps(healthy | replacement)), "unhealthy")
        self.assertEqual(moos.tailscale_state(json.dumps(healthy | {"TailscaleIPs": [True]})), "unknown")

    def test_service_output_and_missing_tailscale(self):
        probe = moos.HostProbe()
        for value, expected in ((None, "unknown"), ((1, b"SECRET"), "unknown"),
                                ((0, b"bad"), "unknown"), ((0, b"LoadState=not-found\n"), "missing"),
                                ((0, b"LoadState=loaded\nActiveState=active\n"), "active")):
            with patch.object(probe, "command", return_value=value):
                self.assertEqual(probe.service("moos-gateway.service"), expected)
        with patch.object(probe, "dependency", return_value=False), patch.object(probe, "command") as command:
            self.assertEqual(probe.tailscale(), "unavailable")
            command.assert_not_called()

    def test_fixed_search_path_and_output_bounds(self):
        probe = moos.HostProbe()
        with patch.dict(os.environ, {"PATH": "/untrusted", "PYTHONPATH": "/untrusted"}):
            result = probe.command(["python3", "-c", "print('ok')"])
            self.assertEqual(result, (0, b"ok\n"))
        self.assertIsNone(probe.command(["no-such-moos-command"]))
        self.assertIsNone(probe.command(["python3", "-c", "import sys; sys.stdout.write('x'*300000)"]))
        started = time.monotonic()
        self.assertIsNone(probe.command(["python3", "-c", "import time; time.sleep(10)"]))
        self.assertLess(time.monotonic() - started, 6)

    def test_unsafe_and_unreadable_metadata(self):
        probe = moos.HostProbe()
        directory = os.stat_result((stat.S_IFDIR | 0o755, 0, 0, 1, 0, 0, 0, 0, 0, 0))
        def metadata(mode, uid=0, links=1):
            return os.stat_result((mode, 0, 0, links, uid, 0, 0, 0, 0, 0))
        for value, expected in ((metadata(stat.S_IFREG | 0o644), "present"),
                                (metadata(stat.S_IFLNK | 0o777), "unsafe"),
                                (metadata(stat.S_IFIFO | 0o644), "unsafe"),
                                (metadata(stat.S_IFREG | 0o666), "unsafe"),
                                (metadata(stat.S_IFREG | 0o644, uid=1000), "unsafe"),
                                (metadata(stat.S_IFREG | 0o644, links=2), "unsafe")):
            with patch.object(Path, "lstat", side_effect=lambda path: value if str(path) == "/key" else directory, autospec=True):
                self.assertEqual(probe.trusted_file("/key"), expected)
        for mode, expected in ((0o755, "present"), (0o555, "present"),
                               (0o711, "unsafe"), (0o744, "unsafe"),
                               (0o644, "unsafe")):
            with self.subTest(mode=oct(mode)), patch.object(
                    Path, "lstat",
                    side_effect=lambda path, mode=mode: metadata(stat.S_IFREG | mode)
                    if str(path) == "/program" else directory,
                    autospec=True):
                self.assertEqual(probe.trusted_file("/program", required_mode=0o555), expected)
        unsearchable = os.stat_result((stat.S_IFDIR | 0o700, 0, 0, 1, 0, 0, 0, 0, 0, 0))
        runtime_path = "/usr/lib/moos/run-qemu.sh"
        with patch.object(
                Path, "lstat", autospec=True,
                side_effect=lambda path: metadata(stat.S_IFREG | 0o755)
                if str(path) == runtime_path else
                unsearchable if str(path) == "/usr/lib/moos" else directory):
            self.assertEqual(
                probe.trusted_file(runtime_path, required_mode=0o555,
                                   required_parent_mode=0o111),
                "unsafe",
            )
        with patch.object(Path, "lstat", side_effect=PermissionError):
            self.assertEqual(probe.trusted_file("/key"), "unknown")
        with patch.object(Path, "lstat", side_effect=FileNotFoundError):
            self.assertEqual(probe.trusted_file("/key"), "missing")

    def test_runtime_account(self):
        probe = moos.HostProbe()
        valid = b"moos-runtime:x:123:123::/var/lib/moos:/usr/sbin/nologin\n"
        cases = (
            (valid, b"moos-runtime\n", b"moos-runtime\n", b"moos-runtime L 01/01/2026 0 99999 7 -1\n", "present"),
            (valid.replace(b"/usr/sbin", b"/sbin"), b"moos-runtime", b"moos-runtime", b"moos-runtime LK", "present"),
            (valid.replace(b"/var/lib/moos", b"/srv/moos"), b"moos-runtime", b"moos-runtime", b"moos-runtime L", "unsafe"),
            (valid.replace(b"/usr/sbin/nologin", b"/usr/bin/nologin"), b"moos-runtime", b"moos-runtime", b"moos-runtime L", "unsafe"),
            (valid, b"wrong-group", b"wrong-group", b"moos-runtime L", "unsafe"),
            (valid, b"moos-runtime", b"moos-runtime extra", b"moos-runtime L", "unsafe"),
            (valid, b"moos-runtime", b"moos-runtime", b"moos-runtime P", "unsafe"),
        )
        for fields, primary, groups, password, expected in cases:
            with self.subTest(expected=expected, fields=fields, groups=groups, password=password), \
                    patch.object(probe, "command", side_effect=[
                        (0, fields), (0, primary), (0, groups), (0, password)
                    ]) as command:
                self.assertEqual(probe.runtime_account(), expected)
                expected_commands = [["getent", "passwd", "moos-runtime"]]
                if b"/var/lib/moos" in fields and fields.rstrip().endswith(
                        (b"/usr/sbin/nologin", b"/sbin/nologin")):
                    expected_commands.extend([
                        ["id", "-gn", "moos-runtime"],
                        ["id", "-nG", "moos-runtime"],
                        ["passwd", "-S", "moos-runtime"],
                    ])
                self.assertEqual([call.args[0] for call in command.call_args_list], expected_commands)

        for result in ((2, b""), (1, b"denied")):
            with patch.object(probe, "command", return_value=result):
                self.assertEqual(probe.runtime_account(), "missing" if result[0] == 2 else "unknown")
        with patch.object(probe, "command", side_effect=[(0, b"broken")]):
            self.assertEqual(probe.runtime_account(), "unknown")
        with patch.object(probe, "command", side_effect=[(0, valid), (0, b"moos-runtime"),
                                                          (0, b"moos-runtime"), (1, b"")]):
            self.assertEqual(probe.runtime_account(), "unknown")

    def test_public_key_parser_with_public_verification_fixtures(self):
        public_fixtures = (
            (
                ROOT / "tests/fixtures/nist-rsa-pkcs1v15-sha256-public.pub",
                "0607c60011ab63c817425c970f53feb081f8fb8ff9ca267be4e9f6f281a26e73",
            ),
            (
                ROOT / "tests/fixtures/nist-ecdsa-p256-public.pub",
                "c3ecbb68212fa81cef8143c8aef5b2a4a0de1c89b79d0e77090cef64112f629a",
            ),
        )
        probe = moos.HostProbe()
        original_open, original_fstat = os.open, os.fstat
        def root_fstat(fd):
            result = list(original_fstat(fd))
            result[4] = 0
            return os.stat_result(result)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "public.pem"
            for fixture, expected in public_fixtures:
                with self.subTest(fixture=fixture.name):
                    path.write_bytes(fixture.read_bytes())
                    path.chmod(0o644)
                    with patch.object(probe, "trusted_file", return_value="present"), \
                            patch.object(os, "open", side_effect=lambda name, flags: original_open(path, flags)), \
                            patch.object(os, "fstat", side_effect=root_fstat):
                        self.assertEqual(probe.public_fingerprint(), expected)
            rsa_public = public_fixtures[0][0].read_bytes()
            for data, expected in ((b"-----BEGIN PRIVATE KEY-----\ninvalid\n", None),
                                   (b"broken", None),
                                   (b"-----BEGIN PUBLIC KEY-----\nbad", None),
                                   (rsa_public + b"x" * 17000, None)):
                path.write_bytes(data)
                path.chmod(0o644)
                with patch.object(probe, "trusted_file", return_value="present"), \
                        patch.object(os, "open", side_effect=lambda name, flags: original_open(path, flags)), \
                        patch.object(os, "fstat", side_effect=root_fstat):
                    self.assertEqual(probe.public_fingerprint(), expected)
            link = Path(temporary) / "link"
            link.symlink_to(path)
            with patch.object(probe, "trusted_file", return_value="present"), \
                    patch.object(os, "open", side_effect=lambda name, flags: original_open(link, flags)):
                self.assertIsNone(probe.public_fingerprint())
        with patch.object(probe, "trusted_file", return_value="unknown"), patch.object(os, "open") as opening:
            self.assertIsNone(probe.public_fingerprint())
            opening.assert_not_called()

    def test_admin_pointer_metadata(self):
        probe = moos.HostProbe()
        directory = os.stat_result((stat.S_IFDIR | 0o755, 0, 0, 1, 0, 0, 0, 0, 0, 0))
        link = os.stat_result((stat.S_IFLNK | 0o777, 0, 0, 1, 0, 0, 0, 0, 0, 0))
        pointer = Path("/usr/lib/moos/admin-current")
        target = "/usr/lib/moos/admin-releases/" + "a" * 64
        for destination, expected in ((target, "present"), ("admin-releases/" + "a" * 64, "present"),
                                      ("/tmp/" + "a" * 64, "unsafe"),
                                      ("/usr/lib/moos/admin-releases/invalid", "unsafe")):
            with patch.object(Path, "is_symlink", return_value=True), \
                    patch.object(Path, "lstat", autospec=True, side_effect=lambda path: link if path == pointer else directory), \
                    patch.object(os, "readlink", return_value=destination):
                self.assertEqual(probe.admin_release(), expected)
        unsafe = os.stat_result((stat.S_IFDIR | 0o777, 0, 0, 1, 1000, 0, 0, 0, 0, 0))
        with patch.object(Path, "is_symlink", return_value=True), \
                patch.object(Path, "lstat", autospec=True, side_effect=lambda path: link if path == pointer else unsafe), \
                patch.object(os, "readlink", return_value=target):
            self.assertEqual(probe.admin_release(), "unsafe")
        with patch.object(Path, "is_symlink", return_value=False), patch.object(Path, "exists", return_value=False):
            self.assertEqual(probe.admin_release(), "missing")
        with patch.object(Path, "is_symlink", side_effect=PermissionError):
            self.assertEqual(probe.admin_release(), "unknown")

    def test_control_errors_are_redacted(self):
        probe = moos.HostProbe()
        for error, expected in ((PermissionError("SECRET"), "denied"), (FileNotFoundError("SECRET"), "unavailable"),
                                (ValueError("SECRET"), "unknown"), (TimeoutError("SECRET"), "unknown")):
            with patch.object(moos, "request", side_effect=error):
                self.assertEqual(probe.personal(Path("/secret")), expected)

    def test_control_request_has_deadline(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "socket"
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(path))
                listener.listen(1)
                def stall():
                    connection, _ = listener.accept()
                    with connection:
                        connection.recv(4096)
                        connection.recv(4096)  # EOF when bounded client closes
                thread = threading.Thread(target=stall, daemon=True)
                thread.start()
                start = time.monotonic()
                self.assertEqual(moos.HostProbe().personal(path), "unknown")
                self.assertLess(time.monotonic() - start, 7)
                thread.join(1)


if __name__ == "__main__":
    unittest.main()
