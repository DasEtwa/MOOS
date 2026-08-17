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
- DHCP networking through QEMU user-mode networking
- roughly 5.7 MiB used in the root filesystem
- a 60 MiB ext2 image with roughly 55.1 MiB usable space
- a dynamic green login banner with kernel, RAM, uptime, IP, and rootfs data

Remote access, moosd, host control, a GUI, mobile clients, and application
streaming are not implemented yet. Their interfaces must remain replaceable.

## Repository layout

| Path | Purpose |
| --- | --- |
| configs/ | Tracked Buildroot configuration used for the MOOS QEMU image |
| scripts/build.sh | Fetches the pinned Buildroot revision and builds MOOS |
| scripts/run-qemu.sh | Portable QEMU launcher for the generated image |
| system/overlay/ | Files copied into the guest root filesystem |
| AGENTS.md | Development rules for coding agents |
| BUG_AUDIT.md | Evidence-based baseline audit |
| STEPS.md | Incremental development roadmap |

Buildroot, compiler output, downloaded sources, rootfs contents, and images are
deliberately kept out of Git. They are created locally under buildroot/,
output/, and host-tools/.

## Build

Use a Linux development host with Git, GNU make, a working C toolchain, and the
host packages required by Buildroot. The first build downloads and compiles the
toolchain, kernel, BusyBox, host QEMU, and the root filesystem. It can take
substantially longer than incremental builds and may use about 14 GB locally.

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

For the console view and login banner:

~~~bash
./scripts/run-qemu.sh --serial-only
~~~

Wait for buildroot login:, enter root, and leave the password empty. This
blank-password root account is intentional for the current local development
image only; the image must not be exposed as a remote service.

To launch the QEMU graphical window while keeping the serial console in the
terminal:

~~~bash
./scripts/run-qemu.sh
~~~

Stop the serial-only session with Ctrl+A, then X.

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

See AGENTS.md for contribution rules, BUG_AUDIT.md for known findings, and
STEPS.md for the next milestones.
