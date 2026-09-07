# Trusted Host onboarding — implementation gate

Status: design proposal; not an installed or published capability.

The requested end state is an authenticated MOOS installation followed by
`moos setup`, one explained administrator authorization, and ordinary local
`start`, `status`, `shell`, and `stop` operations. The current repository cannot
yet deliver that end state. Do not label the existing diagnostic setup or an
unsigned package as a completed installation.

## Observed prerequisites, 2026-09-07

Inspection reflects the repository implementation and tests reviewed on
2026-09-07, including the merged operator UX stabilization. That repository
state is not a published Host distribution.

| Requirement | Current implementation | Missing acceptance |
| --- | --- | --- |
| Bootstrap authenticity | `host/moos_admin_installer.py` requires the fixed installed helper and protected public key; `ADMIN_RELEASES.md` requires independent provisioning. | A publisher-controlled authenticated Host distribution channel and public trust material. |
| Host payload | `scripts/build-admin-release.py` creates a deterministic unsigned archive; the installed helper verifies its detached signature before parsing. | A published, authenticated release selected by a defined distribution version policy. |
| Personal image | `scripts/stage-instance.sh` copies a separately supplied kernel/rootfs and QEMU runtime; these are absent from `RELEASE_MEMBERS`. | Authenticated image/runtime provenance and an exact supported runtime dependency layout. |
| Local control | Existing authenticated scripts install the runtime and control plane; group membership grants the typed local API. | A narrow installed coordinator, explicit local-user grant, safe repeat, interrupted-install and clean-Host acceptance. |
| Guest login | The release defconfig disables root login; `validate-release-rootfs.py` requires a locked root credential. The terminal API forwards the existing serial console. | A reviewed Personal login mechanism; a running release guest does not imply a usable shell. |
| Optional remote status | Tailscale state checks and status-only Gateway authentication exist. | Guided installation/pairing and real device acceptance; neither is needed for local use. |

## Proposed smallest distribution boundary

Start with one tested Ubuntu 24.04 amd64 package baseline, matching the current
Host CI operating system. This is a proposed acceptance target, not evidence
that the managed runtime works on every Ubuntu installation or Linux distro.
Keep package-manager logic outside the runtime and Protocol v1.

The independently authenticated OS package would own the bootstrap helper,
public trust material, initial CLI, exact signed administrator payload, and
an authenticated release-image/runtime payload. Its publisher must establish
package origin outside the checkout. A locally built `.deb`, a root ownership
check, a checksum shipped next to the download, or a key copied from the same
untrusted checkout does not establish that origin.

Build tooling should emit deterministic unsigned artifacts only. Artifact
review and signing happen after the bytes leave the MOOS environment, using
independently trusted tooling. Only public material and returned signatures
may enter the distribution. Normal Host users never generate or supply a
release private key. Checkout tooling must not accept, read, create, copy,
cache, stage, back up, or use one, including beneath ignored or temporary
project directories. No publisher key-management workflow is part of setup.

Do not publish an installation command until the real authenticated channel,
public identity, initial version, and supported image/runtime are available.
This proposal does not authorize provisioning or replacing a trust anchor.

## Proposed first-run operation

1. The installed CLI checks the supported Host, dependencies, authenticated
   payload availability, and existing installation state. Checkout setup
   retains its read-only stop point.
2. It explains the concrete changes: prepare protected MOOS components and
   permit this local account to control Personal MOOS. It asks for approval
   only if these changes are needed, then requests ordinary administrator
   authorization for one fixed installed operation.
3. The installed coordinator independently repeats all security checks. It
   accepts no executable, image, key, source-root, command, or arbitrary user
   path from the CLI. It uses a validated local administrator-authorized
   invoker identity for the narrow local-control grant, never an unchecked
   username or environment variable.
4. It verifies the fixed signed payload before archive parsing, installs the
   locked runtime identity, stages only authenticated Personal/runtime data,
   activates local control through the existing validated staging path, then
   grants the intended local account access. Failures stop at the failed step.
5. Safe repeats inspect actual state rather than trusting a completion flag.
   Existing Personal data, trust, credentials, active services and rollback
   targets are never silently replaced. Interrupted states require a defined
   recovery path before another activation.
6. It explains logout/login when the account grant is not yet effective in
   the current session. It verifies local control again from the user's
   session. It reports guest login separately from service or process health.
7. Only after local preparation does it offer optional iPhone status. Local
   use remains independent of Tailscale. Remote setup must retain separate
   transport, Gateway, and unverified pairing states and status-only grants.

No sudoers rule, remote sudo, remote terminal, arbitrary Host execution, or
resource-sizing questionnaire is needed. The privileged coordinator must be
usable only through the trusted installed boundary; invoking checkout code
with sudo remains forbidden.

## Release Personal login is a separate blocking decision

Do not unlock root, install a shared password, use a development image, inject
credentials into the immutable release image, or treat the local console's
existence as login acceptance. There is currently no guest credential-
provisioning or authenticated guest-login channel to compose into setup.

A login design must specify the local principal, credential or capability
lifetime, storage, reboot and reconnect behavior, revocation, and the Guest
versus Host authority boundary. It must keep root locked in the release image
and provide real QEMU boot/login, `moos-info`, reconnect and clean-stop evidence.
Selecting and implementing that mechanism changes the Guest/terminal security
contract; it cannot be hidden in a packaging script. Until it is implemented,
setup must explicitly say that release shell access is unavailable.

## Threat model and acceptance gates

| Threat | Required protection and evidence |
| --- | --- |
| Malicious checkout | No privileged execution/import; package provenance established independently; fixed installed entry points. |
| PATH/environment injection | Fixed trusted executables, isolated Python, clean subprocess environment and working directory; hostile-environment tests. |
| Symlink/hardlink races and writable parents | Protected ancestry and descriptor-based bounded reads/copies; tests for changed sources, links, unsafe parents and special files. |
| Tampered or partial package/payload | OS-channel authentication plus exact payload verification before parsing or activation; tamper and truncation tests. |
| Trust-anchor replacement | Reject unexpected existing trust; no setup repair or rotation. Package upgrades need an explicit key-continuity contract. |
| Privilege escalation and group overreach | Validate the intended local invoker; grant only typed local-control membership after authorization; no broad sudo rule. |
| Interrupted installation | Serialize activation, retain prior state, verify stage boundaries and test interruption/repeat at each mutation. |
| Stale signed release | Define distribution version/freshness selection. Existing digest/signature verification alone permits historical signed releases; retain deliberate administrator rollback. |
| Private-key handling | No private key in checkout tooling or artifacts. Verification tests consume public material and externally signed fixtures only. |
| Credential leakage | Curated diagnostics, no raw credential-store reads or pairing secrets in reports/logs. |
| Remote privilege expansion | Tailscale remains optional transport; preserve per-device authentication, revocation, and status-only Gateway/daemon enforcement. |

`tests/admin_release.py` uses immutable NIST public verification material and no
private signing identity. Valid and tampered signature handling, deterministic
archive creation, extraction, activation, rollback and malicious-member
rejection remain covered. A future signed MOOS administrator-bundle fixture
must be prepared in the independent signing environment before claiming the
complete end-to-end publisher fixture gate; checkout tooling must never create
it. The existing public-vector and archive tests do not substitute for that
external acceptance evidence.

The implementation gate requires the publisher/channel trust input, an
authenticated Personal/runtime distribution design, and a reviewed guest-login
contract. Missing external trust material blocks only operations and acceptance
that require it; it does not block implementing the missing bootstrap capability
or verified work up to that boundary. No new trust architecture is established
by this proposal.

## Required evidence before completion

Exercise clean, partial and configured Hosts; missing dependencies/bootstrap;
safe repeat; image present/missing; local grant and session refresh; tamper,
interruption and rollback; hostile paths/environment; absence of key handling
and leakage; and ordinary local commands after setup. Preserve existing
runtime, isolation, daemon and Protocol tests. Test optional transport states
without treating missing device-store access as no paired devices.

Actual package installation, managed service activation, guest login, and
remote device acceptance require their respective integration evidence.
Unprivileged tests and package inspection do not substitute for those runs.
Publication and real Host changes remain separately authorized actions.

BRAIN/MOOS is unavailable in this checkout. Its README, ROADMAP and
ARCHITECTURE need to retain these open onboarding gates when synchronized;
do not mark this proposal or consumer onboarding complete.
