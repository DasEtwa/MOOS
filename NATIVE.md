# MOOS Native

MOOS is the platform, not the machine. Native is an experimental x86_64
deployment backend for a headless server/appliance OS. Its intended uses are
dedicated servers, homelabs, appliances, controlled service hosts and development
systems. It is not recommended as a primary desktop operating system.

## N0/N1 architecture contract

MOOS Core may use Native (bare metal), WSL (Windows), VM (macOS or generic
virtualization), or future backends. Backend names describe deployment, not
new Personal identities. Stable CLI semantics, Protocol operations, IDs,
configuration, service identities, persistent user state and a future runtime
ABI must remain backend-neutral. EFI/BIOS, drivers, devices, partition tables,
boot slots and system disk layout belong to Native. Windows WSL lifecycle,
Apple virtualization APIs and QEMU paths likewise belong to their backends.

This slice adds a separate Buildroot target and a development disk-boot runner.
It does not turn Native into the existing managed Personal Host, install
`moosd`/Gateway, add a remote operation, or change Protocol v1. The internal
`/etc/moos-platform` file records `native` and `x86_64`; `/etc/issue` exposes
the shared MOOS version and release profile before login. No generic CLI or
Protocol extension is needed. New substantial production Native components
should use Rust; tiny early-boot POSIX shell is acceptable. Python remains a
build/test tool and is not a Native runtime dependency.

## Build and disk acceptance

Use the Linux build prerequisites in README. Windows development uses WSL2
and a Linux checkout with LF shell scripts, a PATH containing the Linux build
tools, and an unprivileged builder. Buildroot, the existing pinned Linux sources,
and the existing dependency hash checks remain authoritative.

```sh
./scripts/build.sh --target native --jobs 4
python3 tests/native_config.py
python3 tests/native_boot.py --boot bios --network user
python3 tests/native_boot_negative.py
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

Output is `output/native/images/moos-native-x86_64.img`, with generated
`bzImage`, `rootfs.ext2` (ext4 contents), bootloader payloads and BOOT FAT image.
The existing default Personal target still uses `output/` and its existing
development/release profiles. Native is release-only: root stays locked.
There is deliberately no convenient release root shell or provisioned credential.

The test inspects the actual disk's SYSTEM_A shadow and backend identity,
requires the GRUB marker, kernel, mounted root, MOOS version and login prompt,
checks DHCP when requested, rejects blank root login, terminates QEMU cleanly,
and checks that disk and firmware backing files are unchanged. A direct
`-kernel` boot is not Native acceptance. Physical devices and firmware require
separate hardware evidence; QEMU acceptance does not establish hardware support.

## Initial disk layout and reproducibility

The initial image follows Buildroot's small GRUB2 BIOS/EFI disk pattern:

| Region | Initial role |
| --- | --- |
| MBR sector | BIOS GRUB boot code and partition table |
| Sector 1 to 1 MiB | Bounded GRUB BIOS embedding region |
| BOOT, partition 1 | 16 MiB FAT, EFI fallback executable, GRUB config, kernel |
| SYSTEM_A, partition 2 | 60 MiB ext4 MOOS release system |

GRUB supports legacy BIOS and x86_64 UEFI fallback boot. Secure Boot is not
implemented.
Locked root protects normal password login; the unsigned editable boot chain
does not authenticate physical access. Replacing the medium or editing GRUB
can change kernel/init behavior. Verified system integrity is not implemented.

The source enables serial console, virtio, AHCI, NVMe and a small
selection of Ethernet drivers; inclusion alone is not hardware acceptance.
The kernel finds SYSTEM_A by PARTUUID instead of a hardcoded device name.

The MBR signature, FAT ID, filesystem UUID and hash seed are fixed prototype
values. They make image generation deterministic, but cloned images do not
have independent installed identities. **Do not treat this as an installer or
a supported multi-disk deployment layout.** MBR has four primary entries and
the conventional 2 TiB limit. N2 must decide GPT migration, per-install stable
identities and recovery semantics before persistent installations; no stable
core API exposes this prototype partition representation.

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

SYSTEM_A contains today's writable minimal rootfs; it is not yet an enforced
immutable system. There is no DATA partition, B slot, slot selection, installed
user state, recovery promise, installer, or production image provenance path.
Future persistent state must live separately from replaceable system contents.
Do not store valuable data in this experimental system partition.

## Ordered next slices

| Slice | Capability / acceptance prerequisite |
| --- | --- |
| N0 | Backend-neutral architecture contract in this document |
| N1 | Separate reproducible x86_64 disk, actual GRUB disk boot, serial, DHCP and locked release root |
| N2 | BOOT/system/DATA separation, persistent layout, installed identities and recovery semantics |
| N3 | USB/ISO installation media, safe disk selection, local headless installer; requires N2 |
| N4 | App-assisted installer, ephemeral QR pairing, typed protocol, LAN-first UX; requires N3 |
| P1 | Package manifest, verified local store, install/remove/list |
| P2 | Generations, transactions, atomic activation, rollback, pinning, GC, dependencies/services; requires P1 |
| U1 | SYSTEM_A/SYSTEM_B updates, try boot, health confirmation, automatic rollback; requires N2 and verified update trust |
| U2 | Interrupted update recovery, rescue path and update history; requires U1 |
| H1 | Acceptance on a dedicated disposable physical machine |
| H2 | User's actual server only after sufficiently safe install/data/recovery semantics and H1 |
| W1 | Complete WSL runtime backend acceptance; building under WSL does not establish this |
| M1 | macOS virtualization backend (distinct from the historical Personal M1 slice) |

Only N0/N1 are implemented here. STEPS records observed acceptance; later rows
are design direction, not permission to skip their prerequisites.

## Persistent systems and A/B direction

Future Native layouts should separate BOOT, SYSTEM_A, SYSTEM_B and DATA.
DATA must preserve user data, package state, configuration, credentials,
pairing, instance data and rollback state through updates. N2 defines mounts,
ownership, migrations and recovery before installation. U1 writes a complete
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
release provenance remain separate from reproducible bytes (ADMIN_RELEASES and
HOST_ONBOARDING still apply).

## Installer direction (unimplemented)

USB/ISO boots a headless installer, brings up networking, offers a local setup
session, optionally pairs the MOOS App, validates a declarative plan, requires
explicit disk erase confirmation, installs, and reboots into Native. Local
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
permissions are introduced by N1.
