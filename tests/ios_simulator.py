#!/usr/bin/env python3
"""Regression tests for deterministic installed iOS simulator selection."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "select-ios-simulator.py"
spec = importlib.util.spec_from_file_location("select_ios_simulator", SCRIPT)
selector = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = selector
spec.loader.exec_module(selector)

RUNTIME_18_5 = "com.apple.CoreSimulator.SimRuntime.iOS-18-5"
RUNTIME_18_6 = "com.apple.CoreSimulator.SimRuntime.iOS-18-6"
RUNTIME_16_4 = "com.apple.CoreSimulator.SimRuntime.iOS-16-4"
IPHONE_16 = "com.apple.CoreSimulator.SimDeviceType.iPhone-16"
IPHONE_15 = "com.apple.CoreSimulator.SimDeviceType.iPhone-15"
UDID_16 = "11111111-1111-1111-1111-111111111111"
UDID_15 = "22222222-2222-2222-2222-222222222222"


def runtime(identifier, name, version, available=True):
    return {"identifier": identifier, "name": name, "version": version,
            "isAvailable": available}


def device(name, udid, device_type, available=True):
    return {"name": name, "udid": udid, "deviceTypeIdentifier": device_type,
            "isAvailable": available}


class SimulatorSelectionTests(unittest.TestCase):
    def setUp(self):
        self.runtimes_payload = {"runtimes": [
            runtime(RUNTIME_18_6, "iOS 18.6", "18.6"),
            runtime(RUNTIME_18_5, "iOS 18.5", "18.5"),
            runtime(RUNTIME_16_4, "iOS 16.4", "16.4"),
        ]}

    def test_prefers_original_target_when_it_is_available(self):
        runtimes = selector.compatible_runtimes(self.runtimes_payload)
        payload = {"devices": {
            RUNTIME_18_6: [device("iPhone 15", UDID_15, IPHONE_15)],
            RUNTIME_18_5: [device("iPhone 16", UDID_16, IPHONE_16)],
        }}
        selected = selector.choose_existing(runtimes, payload)
        self.assertEqual(selected, selector.Selection(UDID_16, "iPhone 16", "iOS 18.5"))

    def test_fallback_uses_newest_compatible_available_iphone(self):
        runtimes = selector.compatible_runtimes(self.runtimes_payload)
        payload = {"devices": {
            RUNTIME_18_6: [device("iPad Pro", UDID_16, "com.apple.CoreSimulator.SimDeviceType.iPad-Pro"),
                             device("iPhone 15", UDID_15, IPHONE_15)],
            RUNTIME_18_5: [device("iPhone 16", UDID_16, IPHONE_16, available=False)],
            RUNTIME_16_4: [device("iPhone 16", UDID_16, IPHONE_16)],
        }}
        selected = selector.choose_existing(runtimes, payload)
        self.assertEqual(selected, selector.Selection(UDID_15, "iPhone 15", "iOS 18.6"))

    def test_unavailable_old_or_malformed_entries_are_not_selected(self):
        payload = {"runtimes": [
            runtime(RUNTIME_18_5, "iOS 18.5", "18.5", available=False),
            runtime(RUNTIME_16_4, "iOS 16.4", "16.4"),
            runtime("unsafe", "iOS 99.0", "99.0"),
        ]}
        self.assertEqual(selector.compatible_runtimes(payload), [])
        runtimes = selector.compatible_runtimes(self.runtimes_payload)
        devices = {"devices": {RUNTIME_18_5: [
            device("iPhone 16", "not-a-udid", IPHONE_16),
        ]}}
        self.assertIsNone(selector.choose_existing(runtimes, devices))

    def test_creates_from_installed_runtime_without_downloading(self):
        runtimes = selector.compatible_runtimes(self.runtimes_payload)
        device_types = selector.compatible_device_types({"devicetypes": [
            {"name": "iPhone 15", "identifier": IPHONE_15, "productFamily": "iPhone"},
            {"name": "iPhone 16", "identifier": IPHONE_16, "productFamily": "iPhone"},
        ]})
        calls = []

        def create(arguments, **kwargs):
            calls.append((arguments, kwargs))
            return subprocess.CompletedProcess(arguments, 0, stdout=UDID_16 + "\n", stderr="")

        selected = selector.create_simulator(runtimes, device_types, runner=create)
        self.assertEqual(selected, selector.Selection(UDID_16, "iPhone 16", "iOS 18.5", True))
        self.assertEqual(calls[0][0], [
            "xcrun", "simctl", "create", "MOOS CI iPhone 16", IPHONE_16, RUNTIME_18_5,
        ])
        self.assertFalse(calls[0][1]["check"])
        self.assertNotIn("download", " ".join(calls[0][0]).lower())

    def test_creation_failure_is_not_treated_as_success(self):
        runtimes = selector.compatible_runtimes(
            {"runtimes": [runtime(RUNTIME_18_5, "iOS 18.5", "18.5")]}
        )
        types = [("iPhone 16", IPHONE_16)]

        def fail(arguments, **kwargs):
            return subprocess.CompletedProcess(arguments, 1, stdout="", stderr="incompatible")

        with self.assertRaises(selector.SimulatorError):
            selector.create_simulator(runtimes, types, runner=fail)


if __name__ == "__main__":
    unittest.main()
