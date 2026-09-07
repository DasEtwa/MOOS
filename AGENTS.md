# AGENTS.md

This file is the operating manual for coding agents working in MOOS.
`rules.md` is the project constitution. Follow it for project-wide design and
security decisions; use this file for the concrete work loop, evidence gates,
and authority boundaries.

Keep MOOS minimal, shell-first, deterministic, and easy to boot and understand.
Prefer small composable components and justified dependencies over frameworks
or convenience features.

## 1. Source-of-truth model

Keep observed facts and normative permission separate; do not use one ranking
for both.

### Observed implementation truth

For **“What does MOOS currently do?”**, use:
`current code + tests > production docs > STEPS.md > BRAIN/MOOS >
issues/plans/old discussions > memory/assumptions`.

This is evidence, not permission. If code and tests disagree with production
documentation, record the implementation as observed behavior and investigate.
`rules.md` and normative contracts are handled separately below.

### Normative authority

For **“What may MOOS do?”**, use:
`rules.md + Protocol/Security/Release contracts > implementation`.

`rules.md` and the security-, protocol-, host/guest-isolation-, and
release-contracts define the permitted boundary. If implementation or tests
violate that boundary, treat it as a defect or stop condition; do not silently
reinterpret the contract, widen permissions, or bless the behavior.

BRAIN is durable context, not production authority. It may explain
architecture, history, and deferred work, but never override current code,
tests, or contracts. If stale, synchronize it after the production change.

## 2. Mandatory startup flow

Before any larger change, and before every change that crosses a security or
runtime boundary:

1. Run `git status --short --branch`.
2. Record the current branch, `HEAD`, and recent relevant history.
3. Read this file and the relevant parts of `rules.md`.
4. Read the affected production documents. Usually this means `README.md`,
   `STEPS.md`, and, as applicable, `PROTOCOL.md`, `GATEWAY.md`,
   `HOST_GUEST_ISOLATION.md`, or `ADMIN_RELEASES.md`.
5. If available, read `BRAIN/MOOS/README.md`,
   `BRAIN/MOOS/ROADMAP.md`, and `BRAIN/MOOS/ARCHITECTURE.md` as durable
   context. Do not treat them as a substitute for production files.
6. Identify the affected layer using the list in section 3.
7. Inspect the existing implementation and its current test path before
   designing a change.
8. Check the relevant `STEPS.md` prerequisite, current status, and acceptance
   criteria. Do not start a later slice because it is mentioned in a roadmap.
9. Record any dirty, generated, external, or unavailable state that could
   affect the result. Do not overwrite unrelated user changes.

For a trivial documentation-only correction, use judgment, but still check
Git status, the target file, and any directly referenced contract.

## 3. Identify the affected layer

State the primary layer before editing. Also list every secondary boundary if
the change crosses more than one:

- **Buildroot / Guest:** configs, kernel, BusyBox, init, rootfs, overlay, or
  guest utilities.
- **Host Runtime:** launcher, staging, `moos-runtime`, systemd, cgroups,
  QEMU lifecycle, or host resource limits.
- **`moosd`:** local socket activation, daemon lifecycle, local permissions,
  status, Personal lifecycle, or terminal bridge.
- **Protocol:** framing, versioning, request/response schemas, errors, or
  compatibility behavior.
- **Gateway / Auth:** Tailscale binding, pairing, device keys, HMAC, grants,
  revocation, rotation, or remote authorization.
- **Admin Releases:** signing, trust anchors, privileged staging, activation,
  rollback, or release artifact validation.
- **iOS:** Swift/SwiftUI state, Keychain, client connection behavior, cache,
  or presentation.
- **Distribution:** unsigned IPA packaging, GitHub Actions, GitHub Releases,
  Pages, or SideStore metadata.

Do not hide a cross-layer change behind a single-layer description. Name the
boundary and add the verification required for every affected layer.

## 4. Smallest safe slice

Implement the smallest independently reviewable capability that satisfies the
request:

- keep the change scoped and avoid unrelated refactors;
- preserve existing ownership, interfaces, and security boundaries;
- follow the current `STEPS.md` order and do not skip prerequisites;
- do not implement future ideas merely because they appear in BRAIN, `STEPS.md`,
  an issue, or a roadmap;
- do not add a dependency, service, privilege, transport, or persistence layer
  without a demonstrated need and an explicit test path;
- keep stable client concepts independent of QEMU, systemd, filesystem paths,
  and other replaceable implementation details;
- update documentation when behavior, compatibility, footprint, or security
  boundaries materially change.

If the requested result requires a larger architectural decision, stop at the
smallest safe boundary and report the decision needed instead of expanding the
scope implicitly.

## 5. Explicit stop conditions

Stop implementation and report the blocker when any of these is true:

- the QEMU smoke test, boot/login path, or isolation test regresses;
- host rights, devices, paths, mounts, sockets, sudo, or root access would
  need to be expanded only for convenience;
- authentication, authorization, device identity, revocation, or grant
  semantics are unclear or inconsistent;
- Protocol v1 would need a hidden or breaking change without an explicit
  compatibility/migration decision;
- arbitrary Host command execution, a generic `exec` endpoint, or an
  uncontrolled QEMU argument would be needed;
- a privileged installer would have to execute, import, or trust
  developer-owned checkout code or an unvalidated artifact;
- a secret, signing credential, token, private key, certificate, provisioning
  profile, Tailscale key, log credential, or machine-specific path would enter
  Git, an image, a snapshot, or CI;
- a required `STEPS.md` prerequisite, acceptance test, or trust anchor is
  missing;
- a review explicitly required by the task or release process is missing;
- existing user changes overlap the requested files and cannot be preserved
  safely;
- the necessary build, macOS, QEMU, root-only, or hardware evidence is
  unavailable for a claim that requires it.

Do not work around a stop condition by weakening a test, broadening a
permission, marking an unverified feature complete, or silently changing the
scope. State what was observed, what remains unknown, and the smallest safe
next decision.

## 6. Verification matrix

Run the checks that apply to every affected layer. If a check is unavailable,
record it as not verified; do not infer success.

| Affected area | Required evidence |
| --- | --- |
| Guest / Buildroot | affected build/config checks plus QEMU boot |
| Init / Login | real QEMU boot and login |
| Networking | network behavior plus isolation verification |
| Host Runtime | launcher, isolation, and cgroup-policy tests |
| `moosd` | daemon, CLI, and Protocol tests |
| Terminal | real QEMU terminal, `moos-info`, disconnect/reconnect, clean stop |
| Gateway / Auth | authentication, denial-before-backend, revocation, rotation, binding |
| Protocol | contract tests plus malformed, truncated, oversized, and boundary cases |
| iOS | client/source-boundary tests plus macOS CI |
| Admin Release | staging, runtime-identity validation, activation, and rollback |
| Security Boundary | independent second-agent/model review when practical; record status |

For shell changes run `sh -n` and ShellCheck when available. For Rust changes
run `cargo fmt`, `cargo clippy`, and focused tests with the pinned toolchain.
For code changes, build the affected component whenever that layer has a build
step; a documentation-only change does not require a product build.
Use the existing Python and iOS test harnesses rather than inventing a weaker
parallel check.

## 7. Security review gate

Security-sensitive changes should receive an independent second-agent or
second-model review when practical, and before new security-sensitive
architecture is declared established. Request the review early for changes
involving:

- authentication, authorization, pairing, keys, grants, revocation, or
  rotation;
- network listeners, Tailscale binding, transport encryption, or remote
  exposure;
- Host↔Guest IPC, Unix-socket peer checks, terminal/file/app channels, or
  shared folders;
- privilege transitions, root helpers, systemd units, cgroups, sandboxing,
  devices, mounts, or QEMU isolation;
- administrator signing, trust anchors, update activation, rollback, or
  release publication;
- Protocol compatibility or any new remotely callable operation;
- credentials, secret storage, Keychain behavior, or iOS authorization;
- streaming, clipboard, audio, GPU, USB, or any new capability crossing a
  trust boundary.

The review should see the actual diff and the intended threat/permission model.
Resolve valid findings and re-run the affected verification before calling the
change complete. A green CI run does not replace this review. Record whether
the review happened; if it was unavailable, report it as not verified and flag
it for the maintainer or task owner. Do not use an unavailable review as a
reason to widen the scope or declare a new security boundary established.

## 8. Non-negotiable implementation boundaries

This is a short operational checklist; use `rules.md` for the full project-wide
rationale and policy. Keep MOOS minimal, shell-first, deterministic, and
independent of GUI, mobile, and streaming layers. Preserve these eight
operational invariants:

- **Host/Guest:** Guest root is never Host root; networking, host files, shared
  folders, USB, GPU, clipboard, host IPC, and credentials are never implicit.
- **Host control:** The Host controls Instances through narrow typed operations;
  never expose arbitrary Host command execution.
- **Default isolation:** Keep the default QEMU launcher rootless,
  deny-by-default, no-KVM/no-GPU, and free of repository, Home, device, and
  socket bindings. The managed runner accepts no arbitrary QEMU arguments,
  Host paths, device passthrough, or credentials.
- **Managed runtime:** Keep `moos-runtime` setup root-only, explicit, idempotent,
  and dry-run inspectable; keep staged files root-owned/read-only, enforce CPU,
  memory, task, and I/O limits on the Host, and never silently alter accounts,
  sudo policy, or host cgroups.
- **Privileged release:** Statically inspect inputs, copy them into root-owned
  staging, validate under the final unprivileged identity, and only then
  activate; never execute user-checkout code as root.
- **Remote/client boundary:** Authenticate clients, authorize every operation,
  and encrypt remote communication. Tailscale is transport isolation, not
  application authorization; iOS depends only on the versioned protocol.
- **Replaceable presentation:** Keep the desktop shell, radial menu, GUI
  toolkit, streaming protocol, and mobile navigation replaceable.
- **Development login:** Keep the blank-password root login restricted to local
  development QEMU.

When these boundaries need to change, treat the work as a security-reviewed
architecture change, not as a local convenience fix.

## 9. Buildroot and generated files

Treat `configs/`, `scripts/`, `system/overlay/`, and tracked contracts as
source. Do not edit generated output as a source change. Do not commit
`buildroot/`, `output/`, `host-tools/`, downloaded sources, compiler products,
images, logs, or local runtime state. Represent Buildroot changes as tracked
configuration, overlay, patch, or documented dependency-pin changes.

Do not add machine-specific absolute paths to tracked files. Keep generated
images reproducible where the external Buildroot and host dependencies allow
it. Document meaningful rootfs, RAM, boot-time, or binary-size changes.

## 10. Coding guidance by language

### Shell

- Use POSIX `sh` for init scripts and small guest utilities unless Bash is
  genuinely required.
- Start scripts with `set -eu` when compatible with their context.
- Quote paths and data; validate arguments and command results.
- Avoid `eval`, uncontrolled word splitting, and host-specific paths.
- Keep init, login, and status commands fast, deterministic, and dependency-
  light.

### C and C++

C and C++ are not currently present in MOOS. If introduced, define ownership,
lifetime, error behavior, strong warnings, allocation/system-call checks, and
focused boundary/failure tests. Keep privileged code small and avoid
`system()` or shelling out for privileged operations.

### Rust

Rust is used for the Host-side authenticated Gateway. Use the pinned toolchain
and `Cargo.lock`; run `cargo fmt`, `cargo clippy`, and focused tests. Model
protocol and authorization failures explicitly. Avoid `unwrap`/`expect` on
externally controlled or long-lived paths. Drop privileges and minimize
capabilities before serving. Keep release builds separate from privileged
installation; setup scripts must consume validated prebuilt artifacts and must
not invoke Cargo as root.

### Python

Python implements the small local control plane, clients, and development
tools, not the Guest image. Prefer the standard library for small utilities;
pin meaningful dependencies; use explicit CLI arguments, deterministic output,
and actionable errors. Never read secrets from committed defaults or print
them in logs. Keep scripts runnable from any checkout.

### Swift and SwiftUI

Swift is client-side code, not Guest runtime code. Keep it dependent on the
stable remote protocol, store credentials in the platform Keychain, handle
connection loss, authorization failures, and protocol versions explicitly, and
keep UI experiments replaceable. Test the service boundary independently of
presentation.

### iOS release versioning

Every iOS build distributed to users, including a rebuild or hotfix, must have
its own new release identity so it can be offered through the SideStore update
channel instead of requiring a manual IPA replacement.

- Before each publication, increase both `MARKETING_VERSION` (numeric
  `MAJOR.MINOR.PATCH`) and `CURRENT_PROJECT_VERSION` (a positive integer greater
  than every published build) in all app configurations. An unpublished local
  build or CI retry does not by itself require a release-version bump.
- Use `VERSION-bBUILD` when describing a shipped build, for example `0.1.4-b5`.
  This is a human-readable label only: the IPA still contains
  `CFBundleShortVersionString=0.1.4` and `CFBundleVersion=5`. Never put the `-b5`
  suffix into the iOS version field or the canonical `ios-v0.1.4` tag.
- Do not ship a changed IPA as the same release, bump only its build number,
  overwrite a published asset, reuse a tag, or remove the prior source history.
  Follow the current channel contract in [distribution/ios/README.md](distribution/ios/README.md):
  create a new immutable release and prepend an AltSource entry whose `version`
  and `buildVersion` exactly match the new IPA, using its new release URL.
- Verify the project/tag/IPA/source metadata with the existing distribution
  checks. Before claiming an in-app update works, confirm it from the previously
  installed release in SideStore without uninstalling MOOS; otherwise record
  device update acceptance as unverified. A display-label change alone is not
  evidence that update detection is fixed.
- This rule does not authorize tagging, publishing, signing or deployment;
  those actions still require an explicit request.

Apple documents the numeric version-field format in
[CFBundleShortVersionString](https://developer.apple.com/documentation/bundleresources/information-property-list/cfbundleshortversionstring).

## 11. Git, release, and installation authority

An agent may inspect, edit, test, and prepare a scoped diff. Without an
explicit request for the corresponding repository or external action, the
agent must not:

- commit or push changes;
- force-push or rewrite shared history;
- merge, retarget, or delete branches;
- create, move, or delete tags or releases;
- change branch protection, repository settings, or CI permissions;
- publish artifacts, Pages/SideStore metadata, or other release outputs;
- deploy or install software on a real Host or other external machine;
- create, rotate, expose, delete, or otherwise modify secrets and credentials;
- run destructive cleanup against a broad workspace, repository, image, or
  runtime directory.

Even with an explicit action request, inspect the exact target first, stage only
files in scope, and preserve unrelated user changes. Never use destructive
reset/checkout operations to discard work without explicit approval. Review the
complete diff, run `git diff --check`, verify no generated files/secrets/
machine paths are staged, and use a clear scoped commit message when a commit
is authorized.

## 12. BRAIN integration

For larger MOOS work, use these documents when available as durable context:

- `BRAIN/MOOS/README.md` — current project map and verified shape;
- `BRAIN/MOOS/ROADMAP.md` — slice order, status, prerequisites, and deferred
  work;
- `BRAIN/MOOS/ARCHITECTURE.md` — ownership, data flows, and boundaries.

After a meaningful implementation slice, synchronize BRAIN when the change
adds durable architecture, compatibility, security, release, footprint, or
roadmap knowledge. Keep current code/tests/contracts authoritative, record
unverified hardware or CI work as open, and leave intentionally deferred
features visible. A small mechanical fix does not require a broad BRAIN
rewrite.

## 13. Completion report

For a larger task, finish with this compact report and fill it with observed
facts only:

```text
Changed:
* …

Verified:
* …

Not verified:
* …

Security/architecture impact:
* …

Deferred:
* …

BRAIN sync needed:
* yes/no

Next safe step:
* …
```

Do not describe an unrun build, test, QEMU boot, network check, hardware
acceptance, review, installation, or release as successful.
