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

Goal: Add a native Swift/SwiftUI MOOS Mobile project with replaceable views.

Prerequisites: M6 protocol shape; M8 is needed for live connectivity.

Changes: branding, connection state, Host/Personal status, and placeholder
Terminal/Files/Apps/Settings tiles. Only Terminal will eventually function.

Tests: Xcode project validation and simulator build where available.

Completion criteria: project opens/builds without embedding Host internals.

Do not do yet: graphical streaming or final radial/ring navigation.

Status: Planned

## Slice I2 — GitHub Actions iOS build

Goal: Build unsigned simulator/app artifacts on macOS CI without local Mac
requirements.

Prerequisites: I1 complete.

Changes: workflow, dependency-free build verification, artifact documentation.

Tests: GitHub Actions build; no signing secrets in Git.

Completion criteria: CI can verify the project without Apple credentials.

Status: Planned

## Slice I3 — Mobile protocol client

Goal: Implement the iPhone-side authenticated versioned client.

Prerequisites: M6, M7, I1.

Changes: configured Host, protocol check, Host/Personal state, reconnect and
clear offline/error states; credentials in Keychain.

Tests: client protocol tests independent of SwiftUI.

Status: Planned

## Slice I4 — Native MOOS Home

Goal: Render the first local/native MOOS shell on iPhone.

Prerequisites: I3 complete.

Changes: flexible Home view showing Personal, Host, connection quality and
Terminal/Files/Apps/Settings entry points.

Tests: state rendering and offline/reconnect UI tests.

Do not do yet: hard-code radial navigation or stream a Linux desktop.

Status: Planned

## Slice I5 — Native Terminal / mobile MVP stop point

Goal: Connect SwiftUI Terminal to the real Personal terminal channel.

Prerequisites: M8, I3, I4, and M5.

Changes: keyboard, scrolling output, connect/disconnect/reconnect, monospace
rendering, and basic ANSI support if practical.

Tests: real iPhone runs `moos-info`, `moos-version`, and `moos-network` in the
actual Personal guest; disconnect does not stop the guest.

Completion criteria: the real iPhone reaches Personal MOOS over Tailscale and
shows `moos-info` output.

Status: Planned

STOP: After I5 succeeds on a real iPhone, stop and review the experience.

## Future roadmap — do not implement before I5 review

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
