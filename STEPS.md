# MOOS execution roadmap

This file is the execution source of truth. Each slice produces one observable
capability, is tested before the next slice starts, and records its actual
verification. The existing Linux/runtime foundation remains protected while
the mobile MVP is built around one Personal MOOS system.

## Verified foundation

Complete before this roadmap: Buildroot x86_64 Linux 6.18.43 LTS, BusyBox 1.38.0,
QEMU boot/login/networking, MOOS utilities, reboot/poweroff smoke tests,
rootless Bubblewrap isolation, deny-by-default networking, dedicated
`moos-runtime`, staged runtime files, and systemd/cgroup-v2 CPU/RAM/swap/task/I/O
limits. The blank root password remains development-only; the separate release
profile disables password root login and validates the generated image.

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
Installed-control-plane acceptance on 2026-08-17 used the old direct-checkout
bootstrap (now prohibited). Current installations use the externally
authenticated administrator release described in `ADMIN_RELEASES.md`. The old
test invocation imported checkout Python as root and is retained only as a
historical result, not a supported command. The real systemd-managed Personal
start/login,
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

Status: Implemented; Rust migration complete, one real iPhone authentication
observed, post-replacement reconnect acceptance pending

Implementation: the production `moos-gateway` and `moos-gateway-device` are
synchronous Rust binaries built with exact Rust 1.97.1 and `Cargo.lock`.
`moos-gateway-device` creates independent random device keys, an atomically
replaced root-managed authorized-device store, status-only grants, individual
revocation, and key rotation. Gateway v1 uses fresh server and client nonces
with an HMAC-SHA256 challenge proof. The iOS app accepts the one-time manual
pairing code and stores the key with ThisDeviceOnly Keychain protection. The
schema, keys, UUIDs, pairing code, HMAC transcripts, grants, and paths remain
compatible with the Python implementation, which is no longer installed or
executed. Automated cross-client, malformed-input, revocation, rotation,
concurrent-store, permission, and denial-before-backend tests pass. A real
paired iPhone authentication against the Rust service was observed on
2026-08-20. End-to-end reconnect acceptance after replacing the installed
service remains pending.

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

Status: Partial; authenticated status transport implemented, terminal pending

Implementation: the separate unprivileged Rust `moos-gateway` binds the actual
listener to `tailscale0` with `SO_BINDTODEVICE`, refuses addresses outside
Tailscale's IPv4 and IPv6 ranges, and retains the existing systemd interface/IP
policy, empty capability set, and address-family allowlist. It needs no
Netlink access. It authenticates before opening the local control channel and
authorizes every Protocol-v1 request. `moosd` independently restricts that
Gateway Unix peer to `status`. Automated socket tests verify valid forwarding,
deny-before-backend, absolute auth and idle deadlines, live revocation,
generic auth failures, and Tailscale-interface binding. The installer consumes
only matching prebuilt release binaries and restores the prior installation if
service activation fails. The roadmap's remote terminal acceptance remains
deliberately unimplemented.

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

Changes: dependency-free workflow, unchanged unsigned simulator build/test,
separate unsigned physical-device build, arm64/iPhoneOS verification,
`MOOS.ipa` artifact packaging, and build documentation.

Tests: workflow/static validation locally and GitHub Actions build after the
commits are pushed.

Completion criteria: CI is configured to build and test the project with code
signing disabled and no secrets, and separately produces an unsigned
physical-device IPA only after verifying an iPhoneOS/arm64 executable.

Status: Complete
Implemented commits: `ci: verify iOS shell without signing`,
`ci: package unsigned iPhoneOS app`, `ci: update device artifact uploader`
Actual verification: `python3 tests/ios_client.py`, GitHub Actions YAML parsing,
`python3 tests/protocol_contract.py`, `python3 tests/runtime_isolation.py`,
Python compilation, shared-scheme XML validation, and `git diff --check` passed.
GitHub Actions run `32058883408` on commit `8618281` passed both the unchanged
simulator build/test job and the new unsigned device job. The device log records
the `Release-iphoneos` product, `arm64-apple-ios17.0` target, and `platform IOS`;
the uploaded `MOOS-unsigned-iphoneos-arm64` artifact contains `MOOS.ipa` with
the standard `Payload/MOOSApp.app` layout.
The workflow uses `macos-15`, Xcode 16.4, and a compatible installed iPhone
simulator selected by UDID, preferring iPhone 16 / iOS 18.5 when available.
It may create a device from an already installed iOS 17.0-or-newer runtime but
does not download runtimes. `build-for-testing` and `test-without-building` run
against the same selected UDID with code signing disabled. A
separate Release build uses `iphoneos`, a generic iOS-device destination, and
an explicit arm64 architecture; `lipo`, `vtool`, and the app's platform metadata
must all confirm a physical-device product before `Payload/MOOSApp.app` is
packaged and uploaded as `MOOS.ipa`.
Blockers: none
Deviations: the device IPA remains unsigned and cannot be installed until a
future signing/provisioning phase; no signing material is stored in CI.

## Slice I6 — GitHub Releases and SideStore OTA channel

Goal: Turn the already verified unsigned iPhoneOS build into an explicit,
versioned SideStore update channel without moving signing into GitHub.

Prerequisites: I4 complete. M7/M8 are not required because this slice changes
distribution automation only and does not connect the app to a Host.

Changes: one canonical unsigned IPA packager/validator shared by normal and
release CI, strict Xcode/tag and built-bundle validation, explicit `ios-v*`
release workflow, immutable GitHub Release assets, deterministic Classic
AltSource generation with version history, GitHub Pages publication, a
committed distribution icon, generator tests, and release documentation.

Tests: iOS distribution unit tests for tag/version/build parsing, Xcode metadata,
bundle identity, semantic history order, immutable release URLs, malformed
metadata, marketplace-field rejection, and workflow trigger/permission rules;
existing client-boundary, simulator, unit, and unsigned device checks remain in
the reusable normal workflow.

Completion criteria: a tag at the current default-branch tip fails closed on
any version, test, iPhoneOS/arm64, unsigned-bundle, IPA, source-history, or Pages
preparation error; only the publish job can create the Release; SideStore can
consume a stable Pages source whose versions point to immutable Release assets.

Status: Implemented and verified through the published `ios-v0.1.3` release
(Xcode version `0.1.3`, build `4`).
Implemented commits: `build(ios): centralize unsigned IPA packaging`,
`feat(ios): add deterministic AltSource tooling`,
`ci(ios): add guarded release publishing`,
`docs(ios): document the SideStore release channel`,
`ci(ios): verify release assets before publication`, and
`docs(ios): record release verification and recovery`.
Actual local verification: all 15 `tests/ios_distribution.py` cases,
`tests/ios_client.py`, `tests/moos_cli.py`, `tests/moosd_protocol.py`,
`tests/personal_identity.py`, `tests/protocol_contract.py`,
`tests/qemu_launcher.py`, `tests/runtime_control.py`, and
`tests/runtime_isolation.py` passed. Real `tests/qemu_smoke.py` and
`tests/qemu_terminal_bridge.py` boot/reconnect tests also passed. Shell/Python
syntax checks, GitHub Actions YAML plus embedded-shell parsing, deterministic
fixture AltSource generation/validation, and `git diff --check` passed.
`tests/managed_personal.py` was not rerun: the unprivileged development session
has no non-interactive sudo and its root-only Personal image staging is absent;
this distribution-only slice does not change that runtime. The complete macOS
simulator, unit-test, and unsigned iPhoneOS/arm64 workflow passed for the
published `ios-v0.1.3` release (workflow run `32419535993`). The published
release sequence is `ios-v0.1.1`, `ios-v0.1.2`, and `ios-v0.1.3`.
The Pages setting **Settings → Pages → Build and deployment → Source:
GitHub Actions** is enabled. Release `ios-v0.1.3`, its unsigned `MOOS.ipa`, and
the stable SideStore source were published successfully. The current source is
`https://dasetwa.github.io/MOOS/ios/source.json`.
Security: normal CI is `contents: read`; only the tag-only Release job receives
`contents: write`; Pages receives only its required scoped write/OIDC rights.
There is no `pull_request_target`, PAT, Apple credential, certificate,
provisioning profile, private key, signing step, live networking, or protocol
change.
Blockers: none in repository code.
Deviations: none.

### Current default-branch CI note — 2026-09-05

The initial post-merge `main` run at `dacc9f4` passed Host, Gateway, and the
unsigned physical-device IPA, but its iOS simulator job failed before
compilation because the hosted runner did not provide the requested `iPhone 16`
/ `iOS 18.5` destination. The subsequent documentation-only commit `d96f917`
completed the iOS workflow successfully (simulator/unit tests and unsigned
device job; run `33990323811`) and its Gateway check passed (run `33990323853`).
The workflow now discovers installed compatible simulators instead of requiring
that exact destination and fails if no compatible installed runtime/device type
can be used. Local Linux verifies the selection policy; real selection and
Xcode execution remain macOS CI evidence.

## Operator UX foundation — 2026-09-06

Prerequisites: implemented M4–M6 local control, existing authenticated
administrator release contract, and existing status-only Gateway. This is a
local operator slice, not completion of deferred M8 remote terminal work.

Status: Implemented in the CLI; installed Host onboarding acceptance pending.

`moos setup` detects local readiness and explains the administrator trust,
local installation and optional remote-status paths. It asks only about remote
status when no Gateway exists; non-interactive use is supported. `moos doctor`
separates curated observations from rendering, reports actionable failures and
unverified areas, and offers a sanitized schema-versioned JSON report. Local
`start`, `stop`, and `shell` aliases use existing typed operations; explicit
commands and default status JSON remain compatible. No installer, trust
transition, protocol operation, resource policy or privilege was added. The
existing CLI artifact remains in the unchanged administrator bundle allowlist;
its control-plane manifest and embedded digest were refreshed.

Verification: 18 operator tests; existing CLI, daemon, Protocol contract,
runtime control, isolation, launcher, Personal identity, Host regression,
release-profile, control-plane installer, administrator-release, Gateway auth
and Gateway integration tests passed. Rust workspace tests passed (30 tests).
Python compilation, shell syntax, diff checks and an unsigned administrator
bundle build using the existing release binaries passed. Independent security
review found no remaining blocking findings after correcting the initial
lifecycle-timeout scope and adding direct trust-detector tests.

The available generated image has locked root login (verified by the release
rootfs validator). The development QEMU smoke attempt reached login but timed
out waiting for its expected blank-password shell; this is the wrong image
profile for that acceptance, not evidence of a CLI/runtime regression. The
release QEMU smoke verifies boot and rejection of blank root login. Full
development utility/login and terminal reconnect acceptance are not verified
in this slice; no image was modified to bypass login. Real administrator
installation/activation, account grants, private image staging, trust-channel
authenticity and paired-iPhone connectivity remain unverified. ShellCheck was
unavailable locally. No macOS or hardware claim is made.

Deferred product steps: independently authenticated OS-package/bootstrap
provisioning and an explicit release-guest login model, then first-run
acceptance on a clean Host. No automatic fixes or signing-key creation/import
inside checkout tooling. BRAIN/MOOS was not available in this workspace;
README, ROADMAP and ARCHITECTURE there should record this operator model and
its remaining bootstrap/login limitations when that context is available.

### Operator presentation polish — 2026-09-06

Setup, doctor and help now present short owner-neutral guidance. A separate
presentation layer groups shared installation steps and labels results READY,
NEEDS ATTENTION, BLOCKED or INFO by user impact. A stopped Personal and
uninspected device authentication are not failures; trust mismatches still
explicitly stop update installation. Technical observations remain available
with `--verbose`, while the existing report schema, fields and diagnostic
exit-code rules are unchanged. Optional emoji never replace text labels;
non-interactive/CI/NO_COLOR/--no-color output is plain text with no ANSI escapes.

A bounded read-only comparison can notice when the PATH-resolved CLI differs
from the checkout. It never executes that file, infers version age, or changes
installation. Human advice then uses the checkout's relative command. Host
checks, protocol operations and terminal implementation are unchanged.

Verification: 29 operator tests plus existing CLI, daemon, Protocol contract,
Host regressions, runtime control, isolation, launcher, Personal identity,
control-plane installer, administrator-release, Gateway auth and Gateway
integration tests passed. Python compilation, shell syntax, diff checks and an
unsigned administrator-bundle build using existing release binaries passed.
An incremental AST comparison confirmed the existing checks and protocol/
terminal paths were unchanged. Live setup/help output was inspected locally.
The independent review found no blocking issue in the incremental code it
examined, but its final review completion was unavailable due to a usage limit;
the final diff was reviewed locally. No new security architecture is declared.

No real installation, account grant, trust change, remote-device acceptance,
QEMU boot or terminal session was performed for this presentation-only pass.
The existing release-image/development-login limitation above still applies.
ShellCheck remains unavailable. README was synchronized; this polish introduces
no new BRAIN architecture requirement beyond the outstanding operator-model
sync described above.

### Trusted first-run onboarding assessment — 2026-09-07

Status: Blocked at the trust/login architecture gate; no installer implemented.

`HOST_ONBOARDING.md` records the inspected implementation, a proposed initial
Ubuntu 24.04 amd64 package boundary, the fixed installed first-run operation,
and its threat model and required acceptance. Missing inputs are an independently
authenticated publisher/channel and public trust material, authenticated
Personal/runtime payload provenance, and a reviewed release-guest login model.
The existing release profile remains locked; setup remains diagnostic.

Independent read-only review confirmed these gaps and the stricter task's
conflict with checkout signing in `tests/admin_release.py`. That harness was
not run; public-only externally signed test fixtures are required without
weakening existing verification coverage. No package, account, trust, service,
credential, release image, Gateway grant or protocol was changed. The existing
uncommitted operator UX work was preserved. BRAIN/MOOS is absent; synchronize
its open onboarding prerequisites when it becomes available.

Verification for this documentation-only assessment: release-profile,
Protocol-v1 contract, local CLI, control-plane installer, runtime-isolation
policy and QEMU-launcher tests passed, as did `git diff --check`. Independent
review of the assessment found no blocking documentation/security issue; it
is not an implementation or package security review. No build, real QEMU boot,
Host installation, service replacement, Tailscale login or iPhone acceptance
was performed. Missing channel trust prevents delivery of an authenticated
consumer installation; it does not make an unsigned packaging prototype
inherently impermissible.

## Mobile integration status

- The production iOS application uses the authenticated Gateway session, keeps
  its device key in the Keychain, and requests real Protocol-v1 status.
- The Gateway carries unchanged status frames over Tailscale without directly
  publishing the local control socket.
- Full Host/iPhone acceptance after installed-service replacement remains to be
  performed on Apple hardware with a configured tailnet; one Rust-Gateway
  authentication was already observed.
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
