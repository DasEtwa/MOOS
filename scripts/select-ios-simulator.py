#!/usr/bin/env python3
"""Select a compatible installed iPhone simulator for MOOS CI."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Callable


MINIMUM_IOS = (17, 0)
PREFERRED_IOS = (18, 5)
PREFERRED_DEVICE = "iPhone 16"
SIMCTL_LIST_TIMEOUT = 120
SIMCTL_CREATE_TIMEOUT = 60
UUID = re.compile(r"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}")
RUNTIME_ID = re.compile(r"com\.apple\.CoreSimulator\.SimRuntime\.iOS-[A-Za-z0-9.-]+")
DEVICE_TYPE_ID = re.compile(r"com\.apple\.CoreSimulator\.SimDeviceType\.[A-Za-z0-9.-]+")


class SimulatorError(RuntimeError):
    """No safe compatible simulator selection can be made."""


@dataclass(frozen=True)
class Runtime:
    identifier: str
    name: str
    version: tuple[int, ...]


@dataclass(frozen=True)
class Selection:
    udid: str
    device_name: str
    runtime_name: str
    created: bool = False


def version_tuple(value: Any) -> tuple[int, ...] | None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", value):
        return None
    return tuple(int(part) for part in value.split("."))


def natural_key(value: str) -> tuple[tuple[int, Any], ...]:
    return tuple(
        (1, int(part)) if part.isdigit() else (0, part.casefold())
        for part in re.split(r"([0-9]+)", value)
    )


def compatible_runtimes(payload: dict[str, Any]) -> list[Runtime]:
    runtimes = []
    for item in payload.get("runtimes", []):
        if not isinstance(item, dict) or item.get("isAvailable") is not True:
            continue
        identifier = item.get("identifier")
        name = item.get("name")
        version = version_tuple(item.get("version"))
        if (not isinstance(identifier, str) or not RUNTIME_ID.fullmatch(identifier)
                or not isinstance(name, str) or not name.startswith("iOS ")
                or version is None or version < MINIMUM_IOS):
            continue
        runtimes.append(Runtime(identifier, name, version))
    return sorted(runtimes, key=lambda item: (item.version, item.identifier), reverse=True)


def choose_existing(runtimes: list[Runtime], payload: dict[str, Any]) -> Selection | None:
    candidates = []
    devices = payload.get("devices", {})
    if not isinstance(devices, dict):
        return None
    for runtime in runtimes:
        for device in devices.get(runtime.identifier, []):
            if not isinstance(device, dict) or device.get("isAvailable") is not True:
                continue
            name = device.get("name")
            udid = device.get("udid")
            device_type = device.get("deviceTypeIdentifier", "")
            if (not isinstance(name, str) or not name.startswith("iPhone")
                    or not isinstance(device_type, str) or ".iPhone-" not in device_type
                    or not isinstance(udid, str) or not UUID.fullmatch(udid)):
                continue
            exact = name == PREFERRED_DEVICE and runtime.version == PREFERRED_IOS
            candidates.append((exact, runtime.version, name == PREFERRED_DEVICE,
                               natural_key(name), udid, name, runtime.name))
    if not candidates:
        return None
    selected = max(candidates)
    return Selection(selected[4], selected[5], selected[6])


def compatible_device_types(payload: dict[str, Any]) -> list[tuple[str, str]]:
    devices = []
    for item in payload.get("devicetypes", []):
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        identifier = item.get("identifier")
        family = item.get("productFamily")
        if (not isinstance(name, str) or not name.startswith("iPhone")
                or family not in (None, "iPhone")
                or not isinstance(identifier, str) or not DEVICE_TYPE_ID.fullmatch(identifier)):
            continue
        devices.append((name, identifier))
    return sorted(
        devices,
        key=lambda item: (item[0] == PREFERRED_DEVICE, natural_key(item[0]), item[1]),
        reverse=True,
    )


def create_simulator(
    runtimes: list[Runtime],
    device_types: list[tuple[str, str]],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> Selection:
    preferred_runtimes = sorted(
        runtimes,
        key=lambda item: (item.version == PREFERRED_IOS, item.version, item.identifier),
        reverse=True,
    )
    failures = []
    for runtime in preferred_runtimes:
        for device_name, device_type in device_types:
            try:
                result = runner(
                    ["xcrun", "simctl", "create", f"MOOS CI {device_name}",
                     device_type, runtime.identifier],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=SIMCTL_CREATE_TIMEOUT,
                )
            except subprocess.TimeoutExpired:
                failures.append(f"{device_name}/{runtime.name}: creation timed out")
                continue
            udid = result.stdout.strip()
            if result.returncode == 0 and UUID.fullmatch(udid):
                return Selection(udid, device_name, runtime.name, created=True)
            detail = result.stderr.strip().replace("\n", " ")[:240]
            failures.append(f"{device_name}/{runtime.name}: {detail or 'creation failed'}")
    suffix = "; ".join(failures[:3])
    raise SimulatorError("installed runtimes and iPhone types could not create a simulator"
                         + (f": {suffix}" if suffix else ""))


def simctl_json(*arguments: str) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["xcrun", "simctl", *arguments, "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=SIMCTL_LIST_TIMEOUT,
        )
    except subprocess.TimeoutExpired as error:
        raise SimulatorError(f"simctl {' '.join(arguments)} timed out") from error
    if result.returncode != 0:
        detail = result.stderr.strip().replace("\n", " ")[:400]
        raise SimulatorError(f"simctl {' '.join(arguments)} failed: {detail}")
    if len(result.stdout) > 2 * 1024 * 1024:
        raise SimulatorError("simctl JSON exceeded 2 MiB")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise SimulatorError(f"simctl {' '.join(arguments)} returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise SimulatorError(f"simctl {' '.join(arguments)} returned a non-object")
    return payload


def main() -> int:
    try:
        runtimes = compatible_runtimes(simctl_json("list", "runtimes"))
        if not runtimes:
            raise SimulatorError("no available installed iOS Simulator runtime supports iOS 17.0+")
        selection = choose_existing(runtimes, simctl_json("list", "devices", "available"))
        if selection is None:
            device_types = compatible_device_types(simctl_json("list", "devicetypes"))
            if not device_types:
                raise SimulatorError("no installed iPhone simulator device type is available")
            selection = create_simulator(runtimes, device_types)
        origin = "created from installed runtime" if selection.created else "already available"
        print(
            f"Selected iOS Simulator: {selection.device_name}, {selection.runtime_name}, "
            f"UDID {selection.udid} ({origin})",
            file=sys.stderr,
        )
        print(selection.udid)
        return 0
    except (OSError, SimulatorError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
