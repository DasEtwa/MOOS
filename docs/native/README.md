# MOOS Native

MOOS is the platform, not the machine. Native is an experimental x86_64
deployment backend for a headless server/appliance OS. Its intended uses are
dedicated servers, homelabs, appliances, controlled service hosts and development
systems. It is not recommended as a primary desktop operating system.

## N0/N2 architecture contract

MOOS Core may use Native (bare metal), WSL (Windows), VM (macOS or generic
virtualization), or future backends. Backend names describe deployment, not
new Personal identities. Stable CLI semantics, Protocol operations, IDs,
configuration, service identities, persistent user state and a future runtime
ABI must remain backend-neutral. EFI/BIOS, drivers, devices, partition tables,
boot slots and system disk layout belong to Native. Windows WSL lifecycle,
Apple virtualization APIs and QEMU paths likewise belong to their backends.

N1 added a separate Buildroot target and a development disk-boot runner;
N2 extends that target with explicit system/DATA ownership.
It does not turn Native into the existing managed Personal Host, install
`moosd`/Gateway, add a remote operation, or change Protocol v1. The internal
`/etc/moos-platform` file records `native` and `x86_64`; `/etc/issue` exposes
the shared MOOS version and release profile before login. No generic CLI or
Protocol extension is needed. New substantial production Native components
should use Rust; tiny early-boot POSIX shell is acceptable. Python remains a
build/test tool and is not a Native runtime dependency.

## Build and disk acceptance

Use the Linux build prerequisites in [README](../../README.md). Windows development uses WSL2
and a Linux checkout with LF shell scripts, a PATH containing the Linux build
tools, and an unprivileged builder. Buildroot, the existing pinned Linux sources,
and the existing dependency hash checks remain authoritative.

```sh
./scripts/build.sh --target native --jobs 4
python3 tests/native_config.py
python3 tests/native_layout.py
python3 tests/native_grub.py
python3 tests/native_boot.py --boot bios --network user
python3 tests/native_boot_negative.py
python3 tests/native_persistence.py
python3 tests/native_state_negative.py
python3 tests/native_boot.py --boot uefi --network user \
  --uefi-code /usr/share/OVMF/OVMF_CODE_4M.fd \
  --uefi-vars /usr/share/OVMF/OVMF_VARS_4M.fd
```

OVMF paths above are distribution examples; pass the actual installed firmware
pair. Tests use the existing Personal build's QEMU by default, or an explicit
`--qemu` executable. Native does not build QEMU into its rootfs. Bubblewrap is
required. The runner is always serial/headless, TCG, one CPU and 256 MiB RAM;
it binds only QEMU runtime files, the selected regular image and optional OVMF
templates read-only. Disk and variable writes go to temporary snapshots.
Networking defaults to disabled. Explicit `--network user` permits QEMU NAT
and DHCP with no host forwards. No arbitrary QEMU arguments, passthrough,
shared folders or isolation bypass are accepted.

Native is separate from the Personal control-plane boundary described by
HOST_GUEST_ISOLATION.md. Bare-metal MOOS authority and Guest Instance isolation
must be designed separately before Native becomes a managed service Host.
That existing document is a hash-bound control-plane release input and remains
unchanged; Native's additional boundaries are recorded here.

Output is `output/native/images/moos-native-x86_64.img`, with generated
`bzImage`, `rootfs.ext2` (ext4 contents), bootloader payloads and BOOT FAT image.
The existing default Personal target still uses `output/` and its existing
development/release profiles. Native is release-only: root stays locked.
There is deliberately no convenient release root shell or provisioned credential.

The test inspects the actual disk's SYSTEM_A shadow and backend identity,
requires the GRUB marker, kernel, mounted root, MOOS version and login prompt,
checks DATA readiness, DHCP when requested, rejects blank root login, terminates QEMU cleanly,
and checks that disk and firmware backing files are unchanged. A direct
`-kernel` boot is not Native acceptance. Physical devices and firmware require
separate hardware evidence; QEMU acceptance does not establish hardware support.

## N2 GPT disk layout and reproducibility

N2 replaces the N1 MBR prototype with a 171 MiB GPT distribution image.
Primary and backup GPT headers/entry arrays and a protective MBR are present.
The pinned host `grub-bios-setup` embeds GRUB into BIOS_GRUB using its normal
GPT algorithm on a regular file. A narrow tracked GRUB host-tool patch selects
the mapped whole regular image instead of guessing the build host's root
device; embedding checks and blocklist refusal remain intact. No loop device, mounted Host filesystem or
privileged checkout execution is needed. The layout is Native implementation
detail, never a generic Protocol or package concept.

| GPT role | Offset / size | Ownership and purpose |
| --- | --- | --- |
| BIOS_GRUB, partition 1 | 1 MiB / 1 MiB | GRUB BIOS embedding, no filesystem |
| BOOT, partition 2 | 2 MiB / 16 MiB | EFI System Partition/FAT, loaders, config and kernel |
| SYSTEM_A, partition 3 | 18 MiB / 60 MiB | Active ext4 release system; still writable |
| SYSTEM_B, partition 4 | 78 MiB / 60 MiB | Zero-filled, unformatted reserved system slot; no-automount flag |
| DATA, partition 5 | 138 MiB / 32 MiB | Persistent ext4 state, mounted at `/var/lib/moos` |

SYSTEM_B contains no system, active/verified marker, health status or boot entry.
There is no slot switch, try boot, confirmation, fallback or A/B updater.

GRUB supports legacy BIOS and x86_64 UEFI fallback boot. Secure Boot is not
implemented.
Locked root protects normal password login; the unsigned editable boot chain
does not authenticate physical access. Replacing the medium or editing GRUB
can change kernel/init behavior. Verified system integrity is not implemented.

The source enables serial console, virtio, AHCI, NVMe and a small
selection of Ethernet drivers; inclusion alone is not hardware acceptance.
The kernel finds SYSTEM_A by PARTUUID instead of a hardcoded device name.

GPT disk/partition GUIDs, FAT ID and filesystem UUID/hash seeds are fixed
distribution-image values. The local runtime installation UUID is separate and
never baked into the image. It is an identifier, not an authentication secret,
pairing key or authorization grant. The separate [N3 installer](installer.md)
generates per-install disk/partition identities and unique boot references for
its restricted disposable QEMU targets.
Attaching identical system-image clones simultaneously is not supported:
the kernel PARTUUID selection may be ambiguous. DATA selection stays on the
actual SYSTEM_A disk and never falls back to another disk with the same label.

`BR2_REPRODUCIBLE`, a recorded `SOURCE_DATE_EPOCH` (default 1790812800), UTC,
fixed filesystem metadata and normalized BOOT timestamps control image
generation. Use identical source, configuration, dependency inputs and epoch
when comparing outputs. Generated files are never production source or committed
artifacts. Repeated image generation and independent clean toolchain builds are
different reproducibility claims; record exactly which comparison was observed.
The FAT controls follow the pinned tools' behavior: dosfstools
[`--invariant`](https://github.com/dosfstools/dosfstools/blob/v4.2/src/mkfs.fat.c)
and mtools' deterministic timestamp handling; Buildroot's `board/pc` recipes
provide the underlying GRUB/genimage pattern.

SYSTEM_A is not an enforced immutable system. DATA persistence is an N2
foundation, not an installation, backup or full recovery guarantee. There is
no production image provenance path. The separate N3 installer is restricted
to disposable QEMU targets; use disposable images only;
valuable state still needs independently verified backups/recovery.

## Persistent-state ownership and initialization

SYSTEM_A/B are replaceable. `/etc` remains system defaults; durable operator
configuration belongs to DATA/config, not modified system defaults. `/run`,
`/tmp` and seedrng's `/run/seedrng` are transient. A seed from a cloned system
slot is not credited as entropy. This slice adds no credential/pairing service.

| Path under `/var/lib/moos` | N2 contract |
| --- | --- |
| `state-version` | Prepared `1\n`, root:root, mode 0600, regular single-link file |
| `identity/` | Created on first initialization, root:root 0700 |
| `identity/installation-id` | Local random UUIDv4, root:root 0600, single-link; generated once |
| `config/` | Created bounded persistent configuration boundary, root:root 0700 |
| `packages/`, `apps/`, `instances/`, `updates/`, `recovery/` | Future-reserved ownership boundaries; not created or implemented in N2 |

DATA root is root:root 0700, mounted nodev/nosuid/noexec before future stateful
components may start. `S20moos-state` finds exactly one DATA partition by its
GPT role name on the actual root disk, then checks filesystem label, UUID and
ext4 type. It first mounts readonly with `noload`, validates schema, directory
ownership and an existing identity, and only then permits writes. This is
metadata safety, not authenticated filesystem provenance. No runtime formatter,
fsck repair, schema migration or downgrade is called.

New identities wait at most 20 seconds on Linux `/dev/random` for CRNG readiness
before using `/proc/sys/kernel/random/uuid`. Entropy failure blocks state instead
of using MAC, serial, CPU, hostname or cloud identity. Existing valid identity is
reused without new randomness. Staging plus no-clobber hardlink publication and
sync prevent replacing an existing identity. Interrupted initialization can leave
an empty identity directory, staged file or link count of two: these are explicit
BLOCKED/recovery cases, not automatic regeneration. Offline copying already
initialized DATA copies its identity; N2 does not detect or reset such clones.

The transient `/run/moos-native-state` records READY or BLOCKED for this state
boundary. Future stateful components MUST require READY; no such service is
introduced here. Missing, corrupt, mismatched, ambiguous or incompatible DATA
keeps core diagnostics/login bootable and emits `DATA NOT AVAILABLE; stateful
services BLOCKED`. DATA stays readonly, or a readonly tiny tmpfs blocks writable
system fallback. A failure after rw initialization revokes writes; if the kernel
cannot enforce that blocker it is reported explicitly and services must stay
blocked. Newer/unknown schema or invalid existing identity is never repaired,
replaced, migrated or downgraded silently.

Normal Native acceptance keeps readonly backing plus snapshots. Persistence
acceptance first creates a private single-link 0600 regular copy under a 0700
`/tmp/moos-native-persistence-*` directory. The runner's explicit test-only
`--test-writable-copy` cannot take `--image`, a symlink, hardlink, canonical
output or block device. Firmware still uses readonly backing and snapshots.
Tests inject fixed telemetry/poweroff into disposable SYSTEM_A offline and
write a bounded DATA marker offline; no release shell or exec API is introduced.
The distribution has no test hook. Replacement tests refresh only SYSTEM_A and
prove DATA identity/marker remain after booting again.

## N2 recovery contract (full recovery is deferred)

A damaged system slot does not authorize formatting DATA. A future recovery
environment may repair/replace A/B independently while preserving DATA and the
installation identity. Identity reset belongs only to an explicit new-install
operation. Unknown state schema fails safely. Recovering incomplete identity
initialization, dirty/damaged filesystems and hardware loss needs an explicit
future recovery design; normal boot does not format or repair them. A successful
kernel boot cannot mark a future update healthy or successful. U1/U2 remain
unimplemented, with no fake health or rollback state.

## Core, Apps & Features, and update planes (design direction only)

The base Native image should contain only the minimal MOOS Core needed to boot,
network, maintain local identity/state and eventually verify, update, recover
and manage itself. Package, verification, updater and recovery foundations are
future Core responsibilities; N2 does not implement them merely by naming them.

Optional Apps & Features must not silently enter the base OS. Recommended
foundation capabilities may include Tailscale, MOOS Sandboxes and MOOS Store;
apps may include MOOS Servers and workloads; features add optional Host
capabilities; runtimes such as Java are installed only when needed. None is
installed or implemented by N2.

MOOS Store / `ms` is future product/catalog UX, not the package engine:

```text
MOOS Store -> package/catalog API -> moos pkg -> verifier -> transactional store
```

MOOS remains usable without Store. Persistent package/application state belongs
to DATA, independently of replaceable system slots and optional catalog UX.

| Future lifecycle plane | Examples |
| --- | --- |
| System / security | Kernel, boot/recovery, update/trust engines, critical local control; installed sandbox isolation policy/runtime and security-critical auth/network components |
| Product | MOOS Servers, Sandbox management UX, Store and additional MOOS capabilities |
| Apps / user workloads | Minecraft, databases, web services, third-party apps and optional runtimes |

Security-sensitive components remain in the security lifecycle even when an
optional product installs them. Future implementation MUST NOT depend solely on
users manually checking for known critical vulnerabilities. It must support
authenticated signed security metadata and proactive remediation, including
component/version/minimum-safe-version/severity/required-action information.
Transport is reachability, not trust; metadata and artifacts must follow MOOS
trust rules. An unpatchable vulnerable capability must fail closed as
`BLOCKED — SECURITY`, without unnecessarily disabling unrelated capabilities:

```text
MOOS Core READY / Network READY / MOOS Sandboxes BLOCKED — SECURITY / MOOS Servers READY
```

This is a future design requirement, not an implemented channel, automatic
updater, Store, package engine or security certification.

## Ordered next slices

| Slice | Capability / acceptance prerequisite |
| --- | --- |
| N0 | Backend-neutral architecture contract in this document |
| N1 | Separate reproducible x86_64 disk, actual GRUB disk boot, serial, DHCP and locked release root |
| N2 | BOOT/system/DATA separation, persistent layout, installed identities and recovery semantics |
| N3 | USB-style raw installer media, typed local plans and safe disk selection; requires N2; ISO deferred |
| N4 | App-assisted installer, ephemeral QR pairing, typed protocol, LAN-first UX; requires N3 |
| P1 | Package manifest, verified local store, install/remove/list |
| P2 | Generations, transactions, atomic activation, rollback, pinning, GC, dependencies/services; requires P1 |
| U1 | SYSTEM_A/SYSTEM_B updates, try boot, health confirmation, automatic rollback; requires N2 and verified update trust |
| U2 | Interrupted update recovery, rescue path and update history; requires U1 |
| H1 | Acceptance on a dedicated disposable physical machine |
| H2 | User's actual server only after sufficiently safe install/data/recovery semantics and H1 |
| W1 | Complete WSL runtime backend acceptance; building under WSL does not establish this |
| M1 | macOS virtualization backend (distinct from the historical Personal M1 slice) |

N0/N1, the N2 disk/state foundation and the experimental N3 installer are
implemented on the stacked work branches, not merged production truth.
[STEPS](../../STEPS.md) and [N2 acceptance](acceptance/N2.md) distinguish observed results from pending gates; later rows
are design direction, not permission to skip their prerequisites. N3 evidence
is recorded separately in [N3 acceptance](acceptance/N3.md).

## Persistent systems and A/B direction

Future Native layouts should separate BOOT, SYSTEM_A, SYSTEM_B and DATA.
DATA must preserve user data, package state, configuration, credentials,
pairing, instance data and rollback state through updates. N2 defines mounts,
ownership and safe schema rejection; migrations and full recovery remain future
work before production installation. U1 writes a complete
update to the inactive slot, verifies it, tries boot, confirms health and only
then marks it good; an unconfirmed failed system must return to the prior slot.
There is no fake A/B state in this image.

## Package direction (unimplemented)

Buildroot constructs the base image; it is not the runtime package manager.
Future `moos pkg` may provide search, install, remove, update, list, history,
rollback, pin and gc. Prefer content-addressed
`/var/lib/moos/packages/store/` entries and numbered
`/var/lib/moos/packages/generations/`, with `active` selecting a valid generation.
An installation downloads, verifies, stages, validates, creates a generation
and activates it atomically. Rollback selects a previous valid generation.
Arbitrary root-running post-install scripts must not be the default mechanism.

Manifests should declare name, version, architecture, `moos_abi`, dependencies,
files, services, platform requirements, integrity and publisher/signature
information. Portable packages target the declared MOOS ABI; Native-only
capabilities must be declared explicitly. Reuse the project's trust principles,
not a competing trust authority. The historically reviewed PR #41 proposal is
not merged production authority at the inspected baseline; no Native component
depends on it. Re-evaluate the current verifier and findings before P1/U1.
Production private signing keys never enter checkout, CI, build trees, normal
Hosts or MOOS-controlled temporary paths. Public trust provisioning and verified
release provenance remain separate from reproducible bytes
([Admin releases](../../ADMIN_RELEASES.md) and
[Host onboarding](../../HOST_ONBOARDING.md) still apply).

## Installer and later app-assisted direction

N3's [offline raw installer](installer.md) provides the local Core installation
contract on restricted QEMU targets. The future app-assisted flow adds network
setup and optional MOOS App pairing around that independently validated plan,
explicit disk confirmation and installation. Local
installation must work without the app or a MOOS cloud service. The installer
owns validation and destructive authority; the app is a convenience client.

An app-assisted LAN-first session uses an ephemeral installer keypair, QR/short
code and an authenticated encrypted setup channel. Pairing binds the specific
installer/session ID, ephemeral public key, nonce, short-lived secret and expiry.
Use typed operations such as inspect_hardware, inspect_disks, configure_network,
set_hostname, prepare_install, confirm_install, install_status and cancel_install;
never shell/exec, client-supplied mkfs or dd commands. QR discovery may carry IP
information; mDNS or explicit IP may follow. Destroy temporary pairing after
setup; permanent normal MOOS device authorization requires separate explicit
pairing. Transport, authentication and operation authorization remain separate.

A later optional internet rendezvous/relay provides reachability only. Installer
pairing supplies authentication and the end-to-end session supplies integrity
and confidentiality. Installation must remain possible without the relay.

Before erase, show the exact model, size and stable hardware identifier where
available, with **ALL DATA ON THIS DEVICE WILL BE ERASED.** Require deliberate
confirmation such as hold-to-confirm. The installer independently verifies that
the target exists, its identity still matches the approved plan, it did not
change, it is not installation media where distinguishable, and the plan is
internally consistent. A `/dev/sda` string is insufficient identity. Consider a
short local cancellation window before destructive writes. No such writes or
installation permissions are introduced by N1/N2.
