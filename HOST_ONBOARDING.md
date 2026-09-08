# Trusted Host bootstrap and first-run setup

The bootstrap implementation targets **Ubuntu 26.04 amd64, systemd 257+ and
unified cgroup v2**. It produces an unsigned Debian package and implements
protected first-run preparation after independently authenticated installation.
A production package channel and real clean-Host installation are NOT VERIFIED.
Release guest login is NOT AVAILABLE; it does not block Host preparation.

## User path

Obtain `moos-host` through an independently authenticated provider and install
it using the OS package manager. No production repository URL is supplied by
this checkout. A locally built package must not authenticate its own origin.

Run `moos setup` from the normal local account. Setup checks public package
integrity and Host prerequisites before offering protected preparation. It
explains that administrator permission prepares protected components and grants
this account local control. On confirmation, it invokes only the fixed installed
coordinator through sudo. No source paths, executable arguments, keys or target
usernames are accepted by that operation.

Noninteractive setup never prompts or elevates implicitly. `moos setup --prepare`
is an explicit preparation request; without an interactive terminal it uses
sudo's noninteractive authorization mode. `--prepare` is refused by checkout
copies. `moos doctor` remains read-only, with its existing report schema.

Preparation installs the runtime identity and local control, stages Personal
when supplied, and grants only local `moos-control` membership to the validated
sudo invoker. It never starts the guest automatically. A new group grant needs
logout/login; setup checks local access again in the refreshed session.

A successful preparation records these capabilities separately:

- Protected Host preparation completed.
- Local control reachable, or session refresh needed.
- Personal image staged, or not supplied by this package.
- Guest shell NOT AVAILABLE: production root stays locked.
- Optional remote transport, Gateway health and pairing remain separate checks.

A receipt records a past preparation step, not current runtime safety or boot
acceptance. Setup still probes the local protocol and reports a failed Personal
state. It does not call a staged image a verified boot or usable shell.

## Trust and package contents

The [package builder and publisher procedure](distribution/host/README.md)
produce deterministic unsigned bytes using Python's standard library, OpenSSL
public-key validation and readelf metadata inspection. No private release key
is created, accepted, discovered, read, held or used by checkout tooling.

The package contains:

- Fixed installed coordinator and existing administrator-release helper.
- Bootstrap CLI, protocol module and the shared canonical verification core;
  the administrator helper imports that core only from its fixed
  `/usr/libexec/moos/moos_verification.py` trust-anchor path after checking
  root ownership, mode and ancestry.
- Public release-key candidate, never an overwrite of the active trust file.
- Exact administrator archive and a canonical versioned payload manifest.
- Optionally, both Personal kernel/rootfs and the matching QEMU runtime.

Initial authenticity comes from the independently authenticated OS package or
equivalent administrator distribution channel. The shared core parses the
canonical package manifest and checks every member's exact hash and size;
setup records this as integrity-only `packageMembership` evidence. Protected
ownership and hashes maintain installed integrity; neither proves its own
origin, and setup never reports a local package signature as VALID or VERIFIED.
This is a parallel bootstrap provenance path permitted by Rules v2
authenticated package membership. Separately distributed administrator updates
continue to require the existing detached-signature verification path in
`ADMIN_RELEASES.md`.

The coordinator initializes the protected public trust file only when absent,
and refuses a different existing key. Setup never rotates trust. Publisher
private signing stays outside MOOS; only public material reaches normal Hosts.

Personal and QEMU have the same authenticated package membership as Host code.
The builder validates the copied release rootfs's locked root credential without
mounting or running it. It inspects copied QEMU ELF bytes and their transitive
library requirements, bundles dependencies from the specified Buildroot runtime,
and includes only fixed x86 firmware. It never executes source QEMU or uses ldd.
C/C++ system libraries remain OS-package dependencies. ABI and boot acceptance
must be performed on the declared Host baseline before publication.

Ubuntu 24.04 remains an unprivileged CI test environment, not this installer
baseline. The existing control installer requires `PrivatePIDs`, introduced in
[systemd 257](https://raw.githubusercontent.com/systemd/systemd/v257/man/systemd.exec.xml).
The bootstrap checks this dependency instead of weakening isolation.

## Transactions and recovery

Setup serializes its operation using a protected file lock. Under that lock it
copies each bounded, regular, single-link payload file into a private protected
snapshot while checking its manifest digest. The administrator archive, imported
helper, images and QEMU staging sources subsequently come from that snapshot.
Package replacement cannot swap those inputs after verification.

Every traversed runtime directory and member must match the exact inventory:
extra files, directories, symlinks and hardlinks are refused before recursive
staging. Existing administrator releases are also checked through protected
ancestry and exact member contents before any code is reused as root.

The coordinator pins the highest accepted canonical numeric package version and
manifest before mutation. Older versions and different payloads under the same
version are refused even after interruption. This records identity, not setup
success. No release freshness is inferred from a valid signature alone.

Setup refuses to replace a different active administrator release, existing
Personal data or mismatched staged QEMU. Reusing the same release does not
switch pointers or prune rollback targets. Existing matching active local
control is reused. The component control-plane installer owns its existing
activation/rollback transaction; the coordinator does not kill it with an outer
timeout that would bypass its rollback traps.

There is no global destructive rollback. A failed or interrupted run may leave
protected trust, a locked runtime account, staged release/image or local control
prepared. Rerunning the same package verifies actual state and resumes the
remaining steps. Foreign or inconsistent state is refused and preserved for
administrator investigation. Account grants are never silently revoked. This
is first-run preparation, not an update, migration or repair framework.

## Threat model

| Threat | Implemented boundary |
| --- | --- |
| Malicious checkout | Fixed installed entry points; checkout preparation refused; no root import or execution of checkout code. |
| PATH/environment injection | Python isolation, fixed sudo and helper paths, clean subprocess environment and working directory. |
| Writable parents, symlinks, hardlinks | Protected ancestry, bounded no-follow file reads, exact tree inventory and stable-read checks. |
| Package/payload tampering | External package authenticity prerequisite; installed digests checked during private copying before extraction/activation. |
| Trust replacement | Public candidate remains separate from active trust; any existing difference fails closed. |
| Excess local privileges | Sudo-authorized local invoker, ordinary local-account validation, narrow group grant; no sudoers modification. |
| Partial installation | Serialized setup, version pin, private snapshot, component rollback, actual-state repeat checks; no false completion receipt. |
| Stale release replay | Canonical version and manifest continuity; no implicit switch of an existing different administrator release. |
| Private-key handling | Public-only build inputs and externally signed immutable test fixtures; no release signing inside tooling. |
| Credential leakage | Curated setup errors, no device-store reads or pairing creation, no raw helper output in reports. |
| Remote privilege expansion | Gateway/Protocol unchanged; optional transport checks never grant remote lifecycle, terminal or sudo. |

## Verification scope and next steps

`tests/host_bootstrap.py` covers package structure/determinism, tamper and missing
inputs, public trust continuity, unsafe links/ancestry, canonical versions,
private snapshots, persisted interruption/retry and locking, current-release
reuse, local grants, command ordering, and fixed CLI handoff. Its package-layout
fixture uses inert test Gateway bytes; it does not install them. Existing
administrator tests preserve the full externally signed archive roundtrip and
also independently build the current source twice for determinism.

Unprivileged checks do not establish production distribution authenticity,
package installation, root/systemd activation, real grants or session refresh,
managed resource behavior, or iPhone acceptance. Those require their real
external environments. A future release-login mechanism and automated optional
Tailscale/Gateway installation or device pairing are not implemented here.

BRAIN/MOOS is unavailable in this checkout. Synchronize its README, ROADMAP and
ARCHITECTURE with this package/coordinator boundary and the still-open external
acceptance and release-login work when available.
