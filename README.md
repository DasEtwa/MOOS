# MOOS

MOOS is a small experimental Linux distribution focused on a minimal, shell-first
runtime that can later support controlled remote management. The core system is
kept independent from future clients, GUIs, mobile applications, and streaming
experiments.

## Current status

The current baseline is a Buildroot-generated x86_64 image that boots in QEMU.
It is a development image, not a secure general-purpose distribution.

Verified baseline:

- Linux kernel 6.18.7
- BusyBox 1.38.0
- root shell on the serial console
- QEMU boot
- DHCP networking through explicitly enabled QEMU user-mode networking
- roughly 5.7 MiB used in the root filesystem
- a 60 MiB ext2 image with roughly 55.1 MiB usable space
- a dynamic green login banner with kernel, RAM, uptime, IP, and rootfs data
- initial `moos-version`, `moos-info`, `moos-network`, and `moos-power` tools

Remote access, moosd, host control, a GUI, mobile clients, and application
streaming are not implemented yet. Their interfaces must remain replaceable.

## Repository layout

| Path | Purpose |
| --- | --- |
| configs/ | Tracked Buildroot configuration used for the MOOS QEMU image |
| scripts/build.sh | Fetches the pinned Buildroot revision and builds MOOS |
| scripts/run-qemu.sh | Portable QEMU launcher for the generated image |
| scripts/setup-runtime-user.sh | One-time root setup for the locked moos-runtime account |
| scripts/stage-instance.sh | Atomically stages kernel/rootfs files outside the repository |
| scripts/run-instance.sh | Starts a staged Instance in a systemd cgroup |
| tests/qemu_smoke.py | Host-side boot, login, utility, network, and shutdown smoke test |
| tests/qemu_launcher.py | Host-side checks for isolated QEMU defaults and rejection paths |
| tests/runtime_isolation.py | Host-side checks for Phase 3.1 account and cgroup policy |
| system/overlay/ | Files copied into the guest root filesystem |
| AGENTS.md | Development rules for coding agents |
| rules.md | Project-wide design and contribution rules |
| HOST_GUEST_ISOLATION.md | Current host/guest boundary and remaining Phase 3 work |
| BUG_AUDIT.md | Evidence-based baseline audit |
| STEPS.md | Incremental development roadmap |

Buildroot, compiler output, downloaded sources, rootfs contents, and images are
deliberately kept out of Git. They are created locally under buildroot/,
output/, and host-tools/.

## Build

Use a Linux development host with Git, GNU make, bubblewrap, systemd with
unified cgroup v2, a working C toolchain, and the host packages required by
Buildroot. The first build downloads and compiles the toolchain, kernel,
BusyBox, host QEMU, and the root filesystem. It can take substantially longer
than incremental builds and may use about 14 GB locally.

From the repository root:

~~~bash
./scripts/build.sh 4
~~~

The optional number selects the parallel job count. The build script pins
Buildroot to commit 9ac19958f25a58df65b991ec1d7fa80b34f19eb0 and applies
configs/moos_qemu_x86_64_defconfig.

The script intentionally reapplies the tracked baseline configuration. For
temporary experiments, use Buildroot directly after the baseline build:

~~~bash
make -C buildroot O="$PWD/output" menuconfig
make -C buildroot O="$PWD/output" -j4
~~~

Do not commit the resulting output/ tree. If an experiment becomes part of
MOOS, move the relevant configuration or source into a tracked repository path.

## Run in QEMU

For the isolated console view and login banner:

~~~bash
./scripts/run-qemu.sh --serial-only
~~~

Networking is disabled by default. To explicitly enable the current controlled
QEMU user-mode NAT for development:

~~~bash
./scripts/run-qemu.sh --serial-only --network user
~~~

Wait for `moos login:`, enter root, and leave the password empty. This
blank-password root account is intentional for the current local development
image only; the image must not be exposed as a remote service.

To launch the QEMU graphical window while keeping the serial console in the
terminal, explicitly bypass the rootless sandbox:

~~~bash
./scripts/run-qemu.sh --direct --network user
~~~

`--direct` is a development escape hatch and is not the host/guest security
boundary. Do not use it for an untrusted or compromised guest. Stop the
serial-only session with Ctrl+A, then X.

## Host/guest isolation

The default serial launcher runs QEMU in a rootless bubblewrap sandbox. It does
not bind the repository, home directories, host secrets, host devices, shared
folders, or host sockets. It uses TCG instead of KVM, one vCPU, 256 MiB of RAM,
and a temporary snapshot of the 60 MiB root filesystem image.

Inspect the effective defaults without starting QEMU:

~~~bash
./scripts/run-qemu.sh --serial-only --dry-run
~~~

The current boundary and remaining Phase 3 work are documented in
HOST_GUEST_ISOLATION.md. This is a rootless local QEMU baseline; a dedicated
host runtime user, host-level cgroups, persistent Instance metadata, and
`moosd` are not part of the plain developer launcher.

### Phase 3.1 managed runtime

Phase 3.1 adds a separate host account and a cgroup-enforced launch path. First
inspect the privileged setup; this dry-run does not change the host:

~~~bash
./scripts/setup-runtime-user.sh --dry-run
python3 tests/runtime_isolation.py
~~~

After reviewing it, an administrator can install the locked `moos-runtime`
account and private storage once:

~~~bash
sudo ./scripts/setup-runtime-user.sh --source-root "$PWD"
sudo ./scripts/stage-instance.sh --id luna --image-dir "$PWD/output/images"
~~~

The account uses `nologin`, is locked, has no supplementary groups, and has no
access path to the developer checkout. The staged Instance contains only the
kernel and root filesystem; those files and the copied QEMU runtime are
root-owned and read-only to `moos-runtime`.

Run the managed example with automatic I/O-device detection:

~~~bash
sudo ./scripts/run-instance.sh --id luna
~~~

This creates a transient systemd service for `moos-runtime` with a 200% CPU
quota, 2 GiB cgroup memory limit, 512 host tasks, 10 MB/s read/write I/O
limits, 1792 MiB guest memory, two vCPUs, no network, private devices, and
`ProtectHome=yes`. If the storage device cannot be detected, provide it
explicitly with `--io-device /dev/...`. The runtime account itself never gets
sudo or login access. Use `sudo systemctl stop moos-instance-luna.service` from
another terminal to stop a running managed session.

The managed path requires deliberate root authorization and is separate from
the normal developer launcher. It does not expose an arbitrary host command
argument or a remote API.

## Phase 1/2 smoke test

After building, validate the launcher policy and then run the standard host-side
smoke test:

~~~bash
python3 tests/runtime_isolation.py
python3 tests/qemu_launcher.py
python3 tests/qemu_smoke.py
~~~

The runtime policy test checks the dedicated account plan, private staging,
systemd cgroup values, host protections, and invalid input rejection. The
launcher test checks deny-by-default networking, resource limits, the rootless
sandbox, and invalid option rejection. The QEMU test boots through the
isolated launcher with explicit user-mode networking, waits for the login
prompt, checks the banner, kernel, BusyBox, RAM, uptime, /proc, /sys, DHCP,
rootfs space, and writable /tmp. It also exercises the four MOOS utilities,
reboots the guest, and verifies a clean poweroff and QEMU exit.

## MOOS utilities

The first utilities live in `system/overlay/usr/bin/` and use only interfaces
already provided by BusyBox and the minimal guest filesystem:

~~~text
moos-version       print the current MOOS release
moos-info          print kernel, architecture, memory, uptime, rootfs, and network data
moos-network       print hostname, link status, IPv4 address, and gateway
moos-power status  print the current development power/session status
~~~

`moos-power` is intentionally small. `status` is read-only; `reboot` and
`poweroff` are explicit wrappers around the current BusyBox actions and require
root. It does not yet implement MOOS session management or remote power policy.
The release values are kept in `system/overlay/etc/moos-release`.

## Guest customization

The login banner is stored in
system/overlay/etc/profile.d/00-moos-banner.sh. It is sourced by the guest
login shell and reads live values from /proc, ip, and df. Changes to the
overlay require a rebuild before they appear in a new image.

Do not edit output/target/ as a source of truth. It is generated and will be
recreated by Buildroot.

## Development boundaries

The current layers are intentionally small:

1. Buildroot supplies the toolchain, Linux kernel, BusyBox, init, and image
   construction.
2. The MOOS defconfig selects the current QEMU baseline.
3. The overlay adds MOOS-specific guest files.
4. The host launcher starts the guest without exposing a host directory.

Future host services and remote APIs must be designed around explicit,
authenticated, least-privilege operations. Tailscale may provide transport
reachability later, but it is not application authentication.

See AGENTS.md and rules.md for contribution rules, BUG_AUDIT.md for known
findings, HOST_GUEST_ISOLATION.md for the security boundary, and STEPS.md for
the next milestones.
