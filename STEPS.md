# MOOS execution roadmap

This file is the execution source of truth. Each slice produces one observable
capability, is tested before the next slice starts, and records its actual
verification. The existing Linux/runtime foundation remains protected while
the mobile MVP is built around one Personal MOOS system.

## Verified foundation

Complete before this roadmap: Buildroot x86_64 Linux 6.18.7, BusyBox 1.38.0,
QEMU boot/login/networking, MOOS utilities, reboot/poweroff smoke tests,
rootless Bubblewrap isolation, deny-by-default networking, dedicated
`moos-runtime`, staged runtime files, and systemd/cgroup-v2 CPU/RAM/swap/task/I/O
limits. The blank root password remains development-only.

## Slice M1 — Personal MOOS identity

Goal: Define the existing primary runtime as the mobile-facing Personal MOOS.

Prerequisites: Verified Phase 3 runtime foundation.

Changes:

- Add one stable internal identifier: `personal`.
- Keep `Personal MOOS` as a separate display name.
- Expose identity through a small host-side tool and tracked config.
- Do not add a database, instance registry, or lifecycle behavior.

Tests: shell syntax, identity contract test, existing isolation and QEMU tests.

Completion criteria: Host tooling identifies Personal consistently without
temporary names such as `test-phase31`.

Do not do yet: multi-instance management, `moosd`, remote APIs, or iOS code.

Status: Complete
Implemented commit: `2c99d8010a7d22cce8bbf75222fcc689b2848814`
Actual verification: `sh -n scripts/*.sh`; `python3 tests/personal_identity.py`;
`python3 tests/runtime_isolation.py`; `python3 tests/qemu_launcher.py`; and
`python3 tests/qemu_smoke.py` all passed. `git diff --check` passed.
Blockers: none
Deviations: none

## Slice M2 — Personal host runtime control library

Goal: Reuse the existing safe runtime path through a single host-side control
implementation for Personal.

Prerequisites: M1 complete.

Changes: status, start, stop, optional reboot, safe serial connection metadata,
duplicate-start rejection, and stale-state handling. No duplicated QEMU launch
logic; no network API.

Tests: unit tests for transitions and invalid state plus QEMU integration where
available; all existing isolation tests remain green.

Completion criteria: Personal can be started/stopped/status-checked by one
library while the existing sandbox and cgroup path remains the implementation.

Do not do yet: general instance manager, daemon, authentication, or Tailscale.

Status: Complete
Implemented commit: `feat: add personal runtime control library`
Actual verification: `python3 tests/runtime_control.py`; `python3 tests/personal_identity.py`;
`python3 tests/runtime_isolation.py`; `python3 tests/qemu_launcher.py`;
`python3 tests/qemu_smoke.py`; `python3 -m py_compile ...`; and `git diff --check`
all passed. The adapter delegates launch to `scripts/run-instance.sh`, does not
expose host PTY paths, and keeps reboot unavailable until M5.
Blockers: none
Deviations: none

## Slice M3 — Local `moosd` MVP

Goal: Run a minimal local host daemon, preferably Rust only if its dependency
cost is justified.

Prerequisites: M2 complete.

Changes: Unix-domain socket, host status, Personal state, start/stop, and a
future-ready event shape. No remote listener.

Tests: daemon startup, socket permissions, typed requests, invalid requests,
and lifecycle integration.

Completion criteria: local clients can control Personal through `moosd` without
direct QEMU lifecycle logic.

Do not do yet: accounts, Flatpak, streaming, agents, cloud services.

Status: Complete
Implemented commit: `feat: add local moosd MVP`
Actual verification: `python3 tests/moosd_protocol.py`; `python3 tests/runtime_control.py`;
`python3 tests/personal_identity.py`; `python3 tests/runtime_isolation.py`;
`python3 tests/qemu_launcher.py`; `python3 tests/qemu_smoke.py`;
`python3 -m py_compile ...`; shell syntax, and `git diff --check` all passed.
The daemon is Unix-socket-only, uses typed status/start/stop operations, and
rejects arbitrary `exec` and unknown request fields.
Blockers: none
Deviations: none

## Slice M4 — Local MOOS CLI

Goal: Provide `moos status`, `moos personal status/start/stop` and a terminal
placeholder through the daemon only.

Prerequisites: M3 complete.

Changes: small CLI client; no QEMU implementation in the CLI.

Tests: command contract, daemon integration, permission/error behavior.

Completion criteria: normal local operation uses the same control surface as
future mobile clients.

Do not do yet: remote transport or UI logic.

Status: Complete
Implemented commit: `feat: add local moos CLI`
Actual verification: `python3 tests/moos_cli.py`; `python3 tests/moosd_protocol.py`;
`python3 tests/runtime_control.py`; `python3 tests/personal_identity.py`;
`python3 tests/runtime_isolation.py`; `python3 tests/qemu_launcher.py`;
`python3 tests/qemu_smoke.py`; Python compilation, shell syntax, and
`git diff --check` all passed. The CLI has no QEMU lifecycle implementation.
Blockers: none
Deviations: none

## Slice M5 — Terminal bridge

Goal: Bridge a bidirectional terminal channel from `moosd` to the Personal
guest's existing serial console.

Prerequisites: M4 complete.

Changes: bounded incremental NDJSON framing; typed open/input/output/close
lifecycle; systemd-owned reconnectable QEMU serial socket; accurate structured
systemd status; synchronous/reaped launcher handoff; confirmed-stop semantics;
and a socket-activated, group-restricted local `moosd` service model. Client or
daemon disconnect does not stop the guest; input is delivered only to the
guest channel.

Tests: local interactive terminal, `moos-info`, disconnect/reconnect, cleanup,
and negative host-command tests.

Completion criteria: `MOOS Linux` and `# moos-info` work through the local bridge.

Do not do yet: graphical streaming or arbitrary host shell execution.

Status: Complete
Implemented commits: `feat: bridge Personal terminal through moosd`;
`fix: harden M5 terminal and runtime lifecycle`
Actual verification on 2026-08-17: Python compilation, shell syntax,
`git diff --check`, protocol, CLI, identity, runtime-control, isolation-policy,
launcher, and QEMU smoke tests pass. The real unprivileged QEMU bridge test
booted MOOS, ran `moos-info`, terminated/reaped `moosd` while QEMU remained
running, started a new daemon, reconnected, ran `moos-info` again, and powered
off cleanly. Regression coverage includes fragmented/concatenated frames,
combined terminal ACK/output, malformed/oversized/non-object JSON, failed
launch, failed stop, all structured states, and process-memory-independent
console reconnect. The administrator-executed managed integration test then
passed with an actual systemd-managed Personal start and login, real
`moos-info`, daemon restart while the guest remained running, successful
terminal reconnect, managed stop, and process reaping.
Blockers: none
Deviations: the original in-memory PTY design was replaced by a fixed private
QEMU Unix serial socket so reconnect does not require restarting the VM.

## Slice M6 — Versioned protocol

Goal: Define protocol version 1 around only implemented capabilities.

Prerequisites: M5 complete.

Changes: typed status/start/stop/terminal messages, structured errors, no
internal PIDs/paths/unit names, and transport abstraction.

Tests: protocol conformance, malformed messages, compatibility and size limits.

Completion criteria: a non-UI client can use the documented protocol.

Do not do yet: public exposure or final mobile UI assumptions.

Status: Complete
Implemented commits: `feat: define MOOS Protocol v1`;
`fix: enforce M6 protocol contracts`
Actual verification: `python3 tests/protocol_contract.py`; `python3 tests/moosd_protocol.py`;
`python3 tests/moos_cli.py`; `python3 tests/runtime_control.py`;
`python3 tests/personal_identity.py`; `python3 tests/runtime_isolation.py`;
`python3 tests/qemu_launcher.py`; `python3 tests/qemu_smoke.py`;
`python3 tests/qemu_terminal_bridge.py`; Python compilation, shell syntax, and
`git diff --check` all passed. Protocol v1 is documented in `PROTOCOL.md`,
centralizes framing/version/typed validation, and does not expose a remote
transport or Host internals. Follow-up hardening added strict integer-version
checks, typed operation-specific response validation in daemon and CLI,
structured truncated-frame handling at socket EOF, response compatibility
checks, and exact frame-size boundary coverage.
Installed-control-plane acceptance on 2026-08-17 also passed from the current
checkout: `sudo ./scripts/setup-control-plane.sh --source-root "$PWD"`,
`sudo systemctl restart moosd.socket`, and `sudo python3
tests/managed_personal.py`. The real systemd-managed Personal start/login,
framed `moos-info`, daemon restart/reconnect, managed stop, and process reaping
all passed. The socket remains enabled/active and the socket-activated daemon is
inactive with `Result=success` when idle.
Blockers: none
Deviations: M6 formalizes the Protocol v1 contract already exercised by the
hardened M5 local transport; it does not open a new network transport.

## Slice M7 — Authentication and pairing MVP

Goal: Pair one device securely without committed credentials.

Prerequisites: M6 complete.

Changes: random device credential/key, host-side authorized-device store,
revocation, and manual/QR-ready pairing representation.

Tests: authentication, unauthorized requests, revocation, rotation, and secret
handling. Tailscale membership is not treated as authorization.

Completion criteria: one device can be authorized and revoked safely.

Do not do yet: Google/Gmail login or global account infrastructure.

Status: Planned

## Slice M8 — Tailscale host transport

Goal: Reach the same authenticated protocol through the Kubuntu Host's
Tailscale interface.

Prerequisites: M7 complete.

Changes: conservative host binding, no guest Tailscale, no public router ports,
local Unix-socket access preserved, firewall expectations documented.

Tests: remote authenticated status and terminal with `moos-info`; unavailable
transport and unauthorized-client behavior.

Completion criteria: iPhone-reachable host transport works without changing the
guest's network model.

Do not do yet: public WAN exposure or transport-specific client logic.

Status: Planned

## Slice I1 — Native iOS project foundation

Goal: Add a native Swift/SwiftUI MOOS shell with replaceable presentation and
mock-only client state.

Prerequisites: M6 protocol concepts. M7/M8 are not required for local UI work.

Changes: independent `ios/` Xcode project, client-facing Host/Personal/
Connection/App models, mock state service, minimal MOOS Home, and placeholder
Terminal/Files/Settings destinations.

Tests: source-boundary checks, Xcode project validation, unit tests and an
unsigned simulator build where Xcode is available.

Completion criteria: the project opens/builds without embedding Host runtime
internals or implementing transport.

Do not do yet: authentication, remote networking, streaming, or final visual
navigation decisions.

Status: Complete
Implemented commit: `feat: add native iOS project foundation`
Actual verification: `python3 tests/ios_client.py`,
`python3 tests/protocol_contract.py`, `python3 tests/runtime_isolation.py`,
Python compilation, shared-scheme XML validation, and `git diff --check` passed.
The Linux development host does not have Swift or Xcode, so the unsigned
simulator build is intentionally deferred to the macOS CI added in I4.
Blockers: none
Deviations: none

## Slice I2 — Native shell components

Goal: Demonstrate the local MOOS desktop experience using cheap, reusable
SwiftUI components and mock data.

Prerequisites: I1 complete.

Changes: widgets, app grid, MOOS tap menu, replaceable radial-menu prototype,
and bottom system bar with non-destructive placeholders.

Tests: component/model unit tests, source-boundary checks, and an unsigned
simulator build where Xcode is available.

Completion criteria: Home visibly demonstrates desktop, widgets, apps, radial
navigation, and system controls without streaming Linux UI.

Status: Complete
Implemented commit: `feat: build native MOOS shell prototype`
Actual verification: `python3 tests/ios_client.py`,
`python3 tests/protocol_contract.py`, `python3 tests/runtime_isolation.py`,
Python compilation, shared-scheme XML validation, and `git diff --check` passed.
The Linux development host has no Swift/Xcode toolchain, so simulator build and
visual inspection remain deferred to I4 macOS CI and a later Apple device.
Blockers: none
Deviations: power and running-app actions intentionally display mock notices;
they do not cross a service boundary.

## Slice I3 — Cache and resilient state boundaries

Goal: Keep the local shell useful while remote state is unavailable or stale.

Prerequisites: I2 complete.

Changes: explicit local/cached/live data classes, local cache abstractions,
mock/live service boundaries, reconnect/offline presentation, locally advanced
uptime, and version-ready icon metadata.

Tests: cache/service/state unit tests, source-boundary checks, and an unsigned
simulator build where Xcode is available.

Completion criteria: cached metadata remains visible during disconnects and
live state can later be event-driven without changing SwiftUI views.

Status: Complete
Implemented commit: `feat: add resilient iOS state boundaries`
Actual verification: `python3 tests/ios_client.py`,
`python3 tests/protocol_contract.py`, `python3 tests/runtime_isolation.py`,
Python compilation, shared-scheme XML validation, and `git diff --check` passed.
Swift unit tests cover cache round trips, Protocol v1 request encoding, retained
offline metadata, local preferences, and synchronized uptime; execution awaits
the I4 macOS runner because Swift/Xcode is unavailable on this Linux host.
Blockers: none
Deviations: the live boundary is intentionally protocol-only; no M7/M8
transport or authentication implementation was added.

## Slice I4 — GitHub Actions iOS build

Goal: Verify the native shell on a GitHub-hosted macOS/Xcode runner without
Apple signing credentials.

Prerequisites: I3 complete.

Changes: dependency-free workflow, unsigned simulator build/test, and build
documentation.

Tests: workflow/static validation locally and GitHub Actions build after the
commits are pushed.

Completion criteria: CI is configured to build and test the project with code
signing disabled and no secrets.

Status: Planned

## Deferred mobile integration — after M7/M8

- Add an authenticated Protocol v1 client only after M7 defines pairing,
  credential storage, authorization, and revocation.
- Add the Tailscale-carried remote transport only after M8 exposes a reviewed
  authenticated endpoint; never proxy the current local socket directly.
- Connect a native Terminal UI to the real Personal terminal only after those
  boundaries pass real-device tests. Client disconnect must not stop Personal.
- Review the native shell experiment before committing to radial navigation,
  streaming, or broader application behavior.

## Future roadmap — do not implement before deferred mobile integration review

- F1 Native Files UI for Personal guest files only.
- F2 Structured guest control service and typed Host↔Guest IPC.
- F3 Native Apps page backed by guest data.
- F4 Flatpak inside Personal MOOS.
- F5 Individual graphical Linux app streaming, not a default full desktop.
- F6 Audio.
- F7 Sibling multi-instance management; never nested virtualization.
- F8 AI-agent instances only after multi-instance management is stable.

## Invariants for every slice

- Run existing shell, policy, launcher, and QEMU tests where applicable.
- Do not regress Bubblewrap, cgroups, no-KVM/no-GPU, no-Host-Home, or default
  network isolation.
- Keep client concepts stable and hide QEMU/systemd/path implementation details.
- Use typed operations; never add an unrestricted Host `/exec` endpoint.
- Inspect diff and Git status before commit; never stage generated artifacts,
  images, binaries, credentials, or machine-specific paths.
