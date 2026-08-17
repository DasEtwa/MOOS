# MOOS rules

This document contains the project-wide rules for MOOS. `AGENTS.md` adds
working instructions for coding agents; both documents should be followed.

## Rule 1 — No automatic host access

> **MOOS Host controls instances. Instances never control the MOOS Host.**

> Eine Instance bekommt niemals automatisch Zugriff auf den Host.

> **Everything that crosses a trust boundary must be explicit, authenticated,
> validated and revocable.**

The host is sacred. A MOOS Instance must never receive implicit access to host
files, processes, devices, sockets, or credentials. Anything host-side must be
explicitly granted, narrowly scoped, and removable.

## Trust-boundary rules

- **Deny by default.** Networking, shared folders, USB, GPU, clipboard, host
  IPC, and similar integrations are off by default and enabled deliberately.
- **Guest root is not host root.** Root inside an Instance must never imply
  special privileges on the host.
- **Only the host is the external entry point.** Do not install Tailscale per
  Instance or expose public guest ports by default. The intended shape is
  `iPhone → moosd → Instance`.
- **No arbitrary host shell API.** Never build an endpoint such as
  `POST /exec?cmd=...` for the host. Host operations must use narrow APIs such
  as `startInstance()`, `stopInstance()`, and `getStatus()`.
- **The Instance must not know the transport.** A guest should not need to
  know whether a client connects through LAN, Tailscale, P2P, or a relay. That
  is a host concern.

## Instance lifecycle and isolation

- **An Instance is disposable.** Every Instance must be deletable and
  recreatable without damaging the MOOS host. Critical host configuration must
  not exist only inside a guest VM.
- **Contain crashes.** If Instance 4 crashes, Instances 1–3 and `moosd` must
  continue running. If streaming fails, the Instance should remain alive.
- **Bound resources.** CPU, RAM, disk, and eventually network usage must be
  limitable per Instance. A guest must not accidentally consume the whole
  host.
- **Contain OOM.** An Instance OOM must not become a host OOM. A future
  agent-heavy Instance with a memory leak must die in isolation instead of
  taking down the development machine.
- **Use a dedicated runtime identity.** QEMU and host sandbox tooling must run
  as an unprivileged, locked, `nologin` runtime account with no unnecessary
  supplementary groups. It must not run as the developer's normal account.
- **Enforce limits on the host.** CPU, memory, task-count, and I/O budgets must
  be applied by host cgroups or an equivalent host control, not merely reported
  inside the guest.
- **Separate persistence from runtime.** Disk, image, and configuration are
  persistent. PIDs, sockets, temporary ports, and runtime state are not
  persistent Instance metadata.
- **Snapshots are not backups.** Snapshots are for rolling an Instance back;
  real backups must be designed and stored separately.

## Identity, API, and protocol rules

- **Identify Instances by stable IDs, not IP addresses.** An Instance may use
  an ID such as `019abc...`; an address such as `10.0.2.15` is a replaceable
  implementation detail.
- **Version protocols from the beginning.** As soon as `moosd` speaks to a
  client, expose a protocol version rather than relying on implicit behavior.
- **Treat compatibility deliberately.** Changes to disk formats, Instance
  metadata, or remote protocols must remain compatible or include a migration.
- **Keep API and GUI separate.** A future ring UI, iPhone app, or any other GUI
  must not contain core logic. MOOS must remain functional without a GUI.
- **Pair devices instead of sharing passwords.** Future clients should use
  device keys and explicit pairing, not one global “MOOS password”.
- **Revoke devices individually.** A lost iPhone must be disableable without
  resetting every other authorized device.
- **Encryption is not authentication.** Even if Tailscale is used later,
  `moosd` must still know which authorized device is speaking.

## Data, secrets, and portability

- **Never put secrets in images or snapshots.** This includes host tokens,
  Tailscale keys, Apple certificates, and MOOS control credentials.
- **Never log credentials.** Tokens, passwords, private keys, and similar
  secrets must not appear in logs, including debug logs.
- **Keep Instance data portable.** An Instance should eventually move to a
  different host without losing its identity from the client’s perspective.
- **Do not store host-specific paths in Instance formats.** A path such as
  `/home/user/.../instance1.img` must never be a core part of Instance identity
  or metadata.
- **Write important state atomically.** For updates, configuration, and
  metadata: write a temporary file, validate it, then replace the target
  atomically.

## Development and quality rules

- **Prefer reproducibility over convenience.** Anything changed manually in a
  build system must eventually be represented by source, configuration, or a
  documented dependency pin.
- **Every feature needs a test path.** A new system function needs at least an
  automated test, or a documented reason why automation is not possible.
- **The smoke test is sacred.** If the fundamental QEMU smoke test turns red,
  stop and fix or understand it before building further functionality.
- **Security-sensitive changes need review.** Authentication, networking,
  host/guest IPC, filesystem sharing, update systems, and privilege code should
  receive a second-agent or second-model review before becoming established
  architecture.
- **Document performance budgets.** Regularly measure rootfs size, idle RAM,
  boot time, and later remote latency. Do not let MOOS silently grow from
  5.7 MB into a system whose init needs 900 MB of RAM.

## Availability and infrastructure rules

- **No unnecessary telemetry.** MOOS must work locally. If telemetry is ever
  added, it must be explicit, minimal, and disableable.
- **Offline is a first-class mode.** Local host-to-client operation must not
  suddenly require a MOOS cloud account.
- **Cloud is a mediator, not the owner.** If accounts, rendezvous, or relays
  are introduced, central infrastructure should learn as little as possible
  about Instances, files, and sessions.

## Core principles

- Keep the core minimal, shell-first, and easy to understand.
- Prefer a small, composable component over a framework or service stack.
- Add a dependency only when it solves a demonstrated problem.
- Keep builds portable, deterministic, and reproducible where practical.
- Keep the runtime independent from future GUIs, mobile apps, and streaming.
- Do not implement roadmap ideas before their prerequisites are understood.

## Architecture boundaries

Keep these areas separate:

- build tooling and development scripts
- host-side tooling
- the MOOS guest and root filesystem
- low-level system code
- long-running services such as a future `moosd`
- remote protocols and APIs
- GUI, mobile, and other client applications

The guest must not gain host access merely because remote management is planned.
Clients should use a deliberate, versioned interface rather than guest paths or
internal implementation details.

## Security rules

- Never expose unrestricted host root access.
- Never make arbitrary host command execution the default remote operation.
- Separate host control from guest control.
- Authenticate clients, authorize individual operations, and encrypt transport.
- Validate input at every host/guest and client/service boundary.
- Use least privilege, dedicated identities, and sandboxing where appropriate.
- Treat Tailscale as optional transport isolation, not as application security.
- Never commit passwords, tokens, private keys, certificates, signing files, or
  other local secrets.
- The current empty-password root login is for local development QEMU only.

## Runtime rules

- Use BusyBox and POSIX shell facilities for small guest features whenever they
  are sufficient.
- Keep init, login, and status commands fast and dependency-light.
- Give utilities one narrow purpose and predictable exit codes.
- Keep human-readable output stable enough for simple scripts.
- Document meaningful increases in rootfs size, RAM use, boot time, or binary
  size.
- Keep `moos-power` local and explicit until MOOS has its own session and power
  model.

## Source and generated files

- Treat `configs/`, `scripts/`, `system/overlay/`, and tracked documentation as
  source of truth.
- Do not edit `output/target/` or `output/images/` as source changes.
- Do not commit `buildroot/`, `output/`, `host-tools/`, downloaded sources,
  compiler output, generated images, logs, or local runtime state.
- Do not add absolute paths tied to one developer machine.
- Represent Buildroot changes as tracked configuration, overlays, patches, or a
  documented dependency pin.

## Change rules

Before changing MOOS:

1. Inspect the relevant files and current behavior.
2. Identify the affected layer and boundary.
3. Keep the change focused and avoid unrelated refactors.

After changing MOOS:

1. Run syntax and static checks that apply.
2. Build the affected image or component.
3. Run the relevant tests.
4. Boot in QEMU when boot, login, init, networking, or rootfs behavior changed.
5. Document behavior and measured impact when it matters.

Never describe an untested build, boot, or feature as working.

## UI and client rules

The GUI, desktop shell, radial menu, mobile navigation, toolkit, and streaming
protocol are replaceable experiments. Do not make the minimal guest depend on a
particular visual design. Stabilize core behavior and service boundaries first.

## Completion rule

A change is ready when it is small, understandable, reproducible from the
repository, tested at the affected boundary, and safe to extend later.
