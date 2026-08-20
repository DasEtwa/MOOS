# AGENTS.md

These instructions apply to agents working in the MOOS repository.

## Project philosophy

- Keep MOOS minimal by default and shell-first.
- Protect the small runtime footprint and keep boot behavior understandable.
- Avoid unnecessary dependencies and large frameworks.
- Prefer deterministic and reproducible builds where practical.
- Treat anything exposed remotely as security-sensitive.
- Never expose the host machine unnecessarily.
- Preserve sandbox and host/guest boundaries.
- Prefer small, composable components over broad frameworks.
- Keep core functionality independent from UI, mobile clients, and streaming.
- Do not implement future ideas merely because they appear in the roadmap.

## Change discipline

Before changing code:

1. Inspect the relevant files and current Git state.
2. Understand the current behavior and its test path.
3. Identify the affected layer and any host/guest boundary.
4. Keep the change scoped; avoid unrelated refactors.

After changing code:

1. Build the affected component.
2. Run the most relevant available tests and static checks.
3. Boot MOOS in QEMU when boot, rootfs, login, init, or networking is affected.
4. Verify networking when networking is affected.
5. Verify boot and login when boot or shell behavior is affected.
6. Document meaningful behavior, compatibility, or footprint changes.

Never claim that a build, test, or QEMU boot succeeded unless it was actually
run and observed when the environment made that possible.

## Safety rules

These rules are especially important for future remote access:

- Never expose unrestricted host root access.
- Never make arbitrary host command execution the default API.
- Separate host control from guest control.
- Validate external input at every boundary.
- Authenticate remote clients and authorize individual operations.
- Encrypt remote communication.
- Use least privilege and dedicated host identities.
- Prefer sandboxed execution for untrusted workloads.
- Never commit secrets, signing certificates, provisioning profiles, tokens,
  private keys, passwords, or Tailscale auth keys.
- Treat Tailscale as transport/network isolation, not as a replacement for
  application authentication or authorization.
- Keep the current blank-password root login restricted to local development.

For host runtime isolation:

- Keep `moos-runtime` setup root-only, explicit, idempotent, and inspectable via
  a dry-run.
- Keep runtime storage and staged QEMU files root-owned and read-only to the
  runtime account unless a narrowly justified write capability is documented.
- Apply CPU, memory, task, and I/O limits outside the guest process, using the
  host's cgroup manager where available.
- Do not add arbitrary QEMU arguments, host paths, device passthrough, or
  credentials to the managed Instance runner.
- Never make a build or guest script silently create host accounts, modify
  sudo policy, or change host cgroups.

## Architecture boundaries

Keep these layers distinct:

- build tooling
- host tooling
- MOOS guest/rootfs
- low-level system code
- long-running services
- remote API and protocol
- GUI and client applications

The future iOS application must use an intentional, versioned client-facing
interface. It must not depend on internal Linux guest paths or implementation
details.

Do not lock the core system to a radial menu, desktop shell, GUI toolkit,
streaming protocol, or mobile navigation design. Those are replaceable layers.

## Buildroot and generated files

- Treat configs/, scripts/, and system/overlay/ as source of truth.
- Do not edit output/target/, output/images/, or other generated output as a
  source change.
- Do not commit buildroot/, output/, host-tools/, downloaded sources, or
  compiler products.
- If a Buildroot change is required, represent it as a tracked configuration,
  overlay, patch, or documented pinned dependency rather than a silent local
  edit.
- Do not put machine-specific absolute paths in tracked files.
- Keep generated images reproducible from a clean checkout as far as the
  external Buildroot and host dependencies allow.
- Document substantial changes to rootfs size, RAM use, boot time, or binary
  size.

## Coding guidance

### Shell

- Use POSIX sh for init scripts and small guest utilities unless Bash is
  genuinely required.
- Start scripts with set -eu when compatible with their execution context.
- Quote paths and data; validate arguments and command results.
- Avoid eval, uncontrolled word splitting, and host-specific absolute paths.
- Keep login and init scripts fast, deterministic, and dependency-light.
- Run sh -n and ShellCheck when available.

### C and C++

These languages are not currently present in MOOS. When introduced:

- Define ownership, lifetime, and error behavior at interfaces.
- Enable strong compiler warnings and treat relevant warnings as errors.
- Check all system-call and allocation results.
- Avoid shelling out or using system() for privileged operations.
- Keep privileged code small and isolate Linux-specific code behind clear
  interfaces.
- Add focused tests for parsing, boundary conditions, and failure paths.

### Rust

Rust is used for the Host-side authenticated Gateway. For long-running
services and protocol code:

- Use a pinned toolchain and reproducible dependency resolution.
- Run cargo fmt, cargo clippy, and focused tests in CI or local checks.
- Model protocol and authorization failures explicitly.
- Avoid unwrap or expect on externally controlled or long-lived paths.
- Drop privileges and minimize capabilities before serving remote requests.
- Keep release builds separate from privileged installation; setup scripts
  must consume validated prebuilt artifacts and must not invoke Cargo as root.

### Python

Python is not currently present in the runtime. Use it for development tooling
only when shell is no longer clear:

- Prefer the standard library for small generators and test utilities.
- Pin meaningful dependencies and keep them out of the guest image.
- Use explicit CLI arguments, deterministic output, and actionable errors.
- Never read secrets from committed defaults or print them in logs.
- Keep scripts runnable from any repository checkout, not one developer path.

### Swift and SwiftUI

Swift is future client-side work, not current MOOS runtime code. When an iOS
client exists:

- Keep it dependent on the stable remote protocol, not guest internals.
- Store credentials in the platform keychain.
- Handle connection loss, authorization failures, and protocol versions
  explicitly.
- Keep UI experiments replaceable and test the service boundary independently.

## Git workflow

- Inspect git status, the current branch, and recent history before edits.
- Stage only files belonging to the requested change.
- Review the complete diff before committing.
- Verify no generated trees, binaries, secrets, or absolute machine paths are
  staged.
- Use clear, scoped commit messages.
