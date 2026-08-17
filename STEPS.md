# MOOS development roadmap

The roadmap keeps the core small and adds one boundary at a time. Each phase
has an explicit stopping point so future UI, client, and streaming ideas do not
leak into the base system prematurely.

## Phase 0 — Repository foundation

### Goal

Make the current working image understandable and reproducible from a clean
repository checkout.

### Prerequisites

- A Linux development host with Buildroot host prerequisites.
- Git access to the MOOS repository.
- QEMU for the x86_64 smoke test.

### Concrete tasks

- Keep the repository-level documentation and agent instructions current.
- Track the MOOS Buildroot defconfig.
- Pin the Buildroot revision used for the baseline.
- Provide portable build and QEMU entry points.
- Ignore generated Buildroot, compiler, rootfs, image, cache, and local runtime
  data.
- Record the current boot, login, network, and rootfs-size baseline.

### Completion criteria

- A clone explains its structure and current limitations.
- The build command does not depend on a developer's absolute path.
- Generated artifacts are not eligible for accidental commits.
- QEMU launch instructions use repository-relative paths.

### Tests and checks

- sh -n scripts/build.sh scripts/run-qemu.sh
- git check-ignore on buildroot/, output/, and host-tools/
- Configure/build the tracked defconfig.
- Boot the resulting image and observe login, banner, and DHCP.

### Do not do yet

- Do not add a daemon, remote API, GUI, mobile client, Tailscale dependency,
  or streaming protocol.
- Do not commit the full Buildroot checkout or generated images.

## Phase 1 — Stable minimal MOOS

### Goal

Make boot, init, shell, networking, filesystem behavior, shutdown, and login
reliable enough for repeatable development.

### Prerequisites

- Phase 0 repository baseline.
- A repeatable QEMU smoke test.

### Concrete tasks

- Define the intended init and shutdown behavior.
- Add small tests for the login banner and system information.
- Test clean QEMU shutdown/reboot behavior.
- Decide how development authentication differs from release authentication.
- Keep filesystem layout and writable paths explicit.
- Track meaningful kernel/BusyBox/config changes.

### Completion criteria

- Multiple clean boots reach the expected login prompt.
- DHCP and basic network inspection are repeatable.
- Shutdown does not leave the development workflow ambiguous.
- Rootfs size and boot-time changes are recorded.

### Tests and checks

- Boot smoke test in serial-only QEMU.
- Check uname, df, ip, and /proc values from the guest.
- Verify the banner in an interactive shell.
- Verify no host directory is implicitly shared.

### Do not do yet

- Do not broaden the base image with a desktop stack.
- Do not expose a network listener from the guest without an explicit threat
  model and authentication design.

## Phase 2 — MOOS tooling

### Goal

Add only small utilities that make the minimal system easier to operate.

### Prerequisites

- Stable shell and filesystem behavior from Phase 1.
- A demonstrated need for each command.

### Concrete tasks

- Consider moos-info, moos-version, moos-network, and moos-power.
- Define output that is readable by humans and stable enough for scripts.
- Prefer BusyBox-compatible shell for simple commands.
- Use C or Rust only when shell is insufficient for the actual requirement.

### Completion criteria

- Each utility has a narrow purpose and documented exit behavior.
- Utilities work without unnecessary runtime dependencies.
- Tests cover successful and failure paths.

### Tests and checks

- Run utilities in QEMU.
- Check output with shell tests.
- Measure rootfs and boot-time impact.

### Do not do yet

- Do not create a general command framework or package manager.
- Do not add remote-control semantics to local utilities by accident.

## Phase 3 — Host/guest isolation

### Goal

Define and enforce safe boundaries before MOOS controls anything outside the
guest.

### Prerequisites

- Stable local image and an explicit host-side execution model.
- A threat model for the development machine.

### Concrete tasks

- Use a dedicated unprivileged host user where practical.
- Define minimal QEMU permissions and device access.
- Define controlled shared directories, if one is actually needed.
- Choose safe IPC boundaries and validate all messages.
- Add negative tests for path traversal and unauthorized operations.

### Completion criteria

- The guest cannot reach arbitrary host files by default.
- Host-side helpers have explicit, least-privilege capabilities.
- Failure and cleanup behavior is documented.

### Tests and checks

- Inspect QEMU command-line permissions and devices.
- Test access to allowed and denied paths.
- Test process cleanup after guest or host failure.

### Do not do yet

- Do not implement a host API that runs arbitrary commands as root.
- Do not treat a private network as sufficient authorization.

## Phase 4 — moosd

### Goal

Design a small host management daemon only after the host/guest boundary is
understood.

### Prerequisites

- Phase 3 isolation model.
- A concrete management use case.
- A decision about the minimum service privilege.

### Concrete tasks

- Define health/status reporting.
- Define VM/session lifecycle operations.
- Define a controlled command or terminal interface.
- Define file operations with path and size limits.
- Add authentication, authorization, audit logging, and API versioning.
- Introduce Rust only if a long-running service benefits from it.

### Completion criteria

- The daemon exposes a small, documented set of operations.
- No operation provides arbitrary host root execution.
- Protocol errors, timeouts, disconnects, and cleanup are tested.

### Tests and checks

- Unit-test parsing and authorization.
- Integration-test daemon-to-QEMU lifecycle.
- Exercise invalid, unauthenticated, and over-privileged requests.

### Do not do yet

- Do not couple the daemon to an iOS view hierarchy or a GUI layout.
- Do not promise a frozen protocol before the operations are exercised.

## Phase 5 — Remote protocol

### Goal

Create a stable client-facing protocol independent of presentation.

### Prerequisites

- A working, least-privilege moosd prototype.
- Threat model and authentication design.

### Concrete tasks

- Model status, sessions, shell, files, and power operations.
- Version the protocol and define capability discovery.
- Define request limits, timeouts, errors, and reconnect behavior.
- Consider concepts such as GET /status, POST /session/start, and a terminal
  WebSocket only as design examples.

### Completion criteria

- A non-UI client can use the protocol.
- Authorization is operation-specific.
- Compatibility and deprecation rules are documented.

### Tests and checks

- Protocol conformance tests.
- Authentication and authorization tests.
- Fuzz or property-test parsers where practical.

### Do not do yet

- Do not select a mobile UI or streaming protocol as a prerequisite.
- Do not expose the service publicly before security review.

## Phase 6 — Remote access

### Goal

Make remote operation possible without turning MOOS into an implicit host
backdoor.

### Prerequisites

- Versioned protocol and authenticated moosd.
- Explicit deployment and key-management plan.

### Concrete tasks

- Evaluate Tailscale as an optional transport path on the host.
- Keep Tailscale out of the guest unless a clear need appears.
- Define device enrollment, revocation, and logging.
- Add network failure and reconnect handling.

### Completion criteria

- Remote access can be enabled deliberately and disabled cleanly.
- Transport reachability and application authorization are separate checks.
- No secret or auth key is stored in the repository.

### Tests and checks

- Test authorized and unauthorized clients.
- Test revocation and expired credentials.
- Test operation when the transport is unavailable.

### Do not do yet

- Do not treat Tailscale membership as full application authorization.
- Do not bind the core image to one network provider.

## Phase 7 — Mobile companion

### Goal

Provide an optional client for status and controlled operations.

### Prerequisites

- Stable remote protocol and authentication.
- Documented lifecycle and error semantics.

### Concrete tasks

- Explore Swift/SwiftUI for connection status, terminal, files, sessions, and
  power controls.
- Store credentials in the platform keychain.
- Keep client state derived from protocol responses, not guest internals.
- Define offline, reconnect, and permission states.

### Completion criteria

- The client works against the documented protocol.
- It handles failures without weakening authorization.
- UI can change without changing the core service.

### Tests and checks

- Protocol integration tests independent of the UI.
- Client tests for disconnects, stale sessions, and denied operations.

### Do not do yet

- Do not declare a final navigation system, radial menu, or desktop metaphor.
- Do not move core logic into the client.

## Phase 8 — Streaming research

### Goal

Determine whether remote desktop or application streaming is useful and safe.

### Prerequisites

- Stable remote protocol and client.
- A specific latency-sensitive use case.

### Concrete tasks

- Compare desktop and individual-application streaming.
- Measure video, audio, keyboard, mouse, and touch behavior.
- Evaluate adaptive resolution, bandwidth, latency, and session isolation.
- Record protocol and licensing tradeoffs before selecting technology.

### Completion criteria

- A measured prototype demonstrates a defined use case.
- Security, resource use, and operational cost are understood.
- The selected transport can remain a replaceable layer.

### Tests and checks

- Measure latency and resource use under realistic network conditions.
- Test session termination and input isolation.

### Do not do yet

- Do not add a streaming stack to the minimal base image.
- Do not select a protocol only because it is convenient for one client.

## Phase 9 — Optional graphical MOOS environment

### Goal

Add a replaceable graphical shell only if the underlying system and remote
interfaces justify it.

### Prerequisites

- Stable minimal system.
- Stable remote boundaries if remote GUI use is intended.
- Evidence that a graphical environment solves a real need.

### Concrete tasks

- Choose the smallest appropriate graphics stack.
- Keep graphical startup separate from core init and shell operation.
- Define fallback behavior when no display is available.
- Measure rootfs, RAM, boot time, and maintenance cost.

### Completion criteria

- Console-only MOOS remains usable.
- The graphical shell can be replaced without changing core services.
- Resource and security impact is documented.

### Tests and checks

- Boot with and without graphics.
- Test local and remote input isolation.
- Measure footprint and startup regressions.

### Do not do yet

- Do not build the core architecture around a particular desktop or radial
  menu.
- Do not add GUI dependencies before the earlier phases are stable.

## Next recommended milestone

Finish Phase 0 validation on a fresh checkout, then begin Phase 1 with a small
repeatable QEMU boot/login/network smoke test. The next feature should be
chosen from a measured Phase 1 need, not from the future client or GUI ideas.
