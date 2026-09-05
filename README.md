# MOOS

MOOS is a small experimental Linux distribution focused on a minimal, shell-first
runtime with controlled remote status access. The core system remains
independent from the native iOS client, GUIs, and streaming experiments.

## Current status

The current baseline is a Buildroot-generated x86_64 image that boots in QEMU.
It is a development image, not a secure general-purpose distribution.

Verified baseline:

- Linux kernel 6.18.43 LTS
- QEMU 11.0.3 with host seccomp support on Linux
- BusyBox 1.38.0
- root shell on the serial console
- QEMU boot
- DHCP networking through explicitly enabled QEMU user-mode networking
- roughly 5.7 MiB used in the root filesystem
- a 60 MiB ext2 image with roughly 55.1 MiB usable space
- a dynamic green login banner with kernel, RAM, uptime, IP, and rootfs data
- initial `moos-version`, `moos-info`, `moos-network`, and `moos-power` tools
- a bounded local `moosd` protocol and `moos` status/start/stop/terminal client
- a managed serial-console endpoint whose lifetime is independent of `moosd`

Authenticated status access over Tailscale is implemented through a separate,
status-only `moos-gateway`; `moosd` itself remains local-only. The native iOS
app under `ios/` starts empty, imports a per-device pairing credential into the
Keychain, performs Gateway and Protocol-v1 negotiation, and renders the real
Personal state. Remote terminal/lifecycle control, application streaming, and
device signing remain unimplemented. Its unsigned GitHub Release/SideStore
distribution channel is active; SideStore performs signing and installation
outside this repository.

## Repository layout

| Path | Purpose |
| --- | --- |
| configs/ | Tracked Buildroot configuration used for the MOOS QEMU image |
| scripts/build.sh | Fetches the pinned Buildroot revision and builds MOOS |
| scripts/run-qemu.sh | Portable QEMU launcher for the generated image |
| scripts/setup-runtime-user.sh | One-time root setup for the locked moos-runtime account |
| scripts/setup-control-plane.sh | Explicitly installs socket-activated local moosd |
| scripts/build-admin-release.py | Builds the deterministic unsigned administrator bundle |
| scripts/stage-instance.sh | Atomically stages kernel/rootfs files outside the repository |
| scripts/run-instance.sh | Starts a staged Instance in a systemd cgroup |
| host/moos_protocol.py | Incremental bounded NDJSON framing |
| PROTOCOL.md | Versioned Protocol v1 contract and compatibility rules |
| host/moos_runtime.py | Fixed Personal lifecycle/status/console adapter |
| host/moosd.py | Typed local control and terminal service |
| host/moos_admin_installer.py | Source for the externally provisioned root-owned trust anchor |
| ADMIN_RELEASES.md | Signed administrator-release bootstrap and trust contract |
| host/moos-gateway/ | Authenticated, status-only Rust Tailscale Gateway |
| scripts/build-gateway.sh | Builds the pinned Rust Gateway release binaries |
| scripts/setup-gateway.sh | Staged, unprivilegedly validated Gateway installer |
| GATEWAY.md | Gateway authentication, authorization, and installation contract |
| ios/MOOSApp/ | Native SwiftUI client with authenticated Gateway status |
| distribution/ios/ | Deterministic SideStore AltSource seed, release icon, and guide |
| .github/workflows/ios.yml | Unsigned simulator tests and physical-device IPA build CI |
| .github/workflows/ios-release.yml | Explicit-tag GitHub Release and Pages publication |
| tests/qemu_smoke.py | Host-side boot, login, utility, network, and shutdown smoke test |
| tests/qemu_launcher.py | Host-side checks for isolated QEMU defaults and rejection paths |
| tests/qemu_terminal_bridge.py | Real QEMU/moosd terminal restart/reconnect test |
| tests/managed_personal.py | Root-only managed Personal M5 integration test |
| tests/protocol_contract.py | Protocol v1 schema, error, and size contract test |
| tests/gateway_auth.py | Device pairing, challenge, revocation, and rotation test |
| tests/gateway_protocol.py | Authenticated status forwarding and authorization test |
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
./scripts/build.sh --jobs 4
~~~

The optional number selects the parallel job count. The default build is the
release profile. The build script pins Buildroot to the stable 2026.05.1
release at commit `cb857ba4c87a93e5265a9e4a3f32071abf39e14a`, applies the
tracked MOOS Buildroot patch for QEMU 11.0.3 and Linux host seccomp support,
then applies configs/moos_qemu_x86_64_release_defconfig. The Buildroot source
checkout is allowed to contain only those documented local patch changes.

For interactive console development and the QEMU login tests, explicitly opt
into the insecure development profile:

~~~bash
./scripts/build.sh --profile development --jobs 4
~~~

The release build fails unless `/etc/shadow` in the generated ext2 image has a
locked root credential. Development and release settings therefore cannot be
silently mixed.

MOOS tracks the maintained Linux 6.18 LTS line. Before each release, check the
latest 6.18 patch at kernel.org, update both guest configs, and update the
tracked `linux` and `linux-headers` SHA-256 files together; the release-profile
test rejects version/hash drift between those inputs.

MOOS tracks stable Buildroot releases. Before a MOOS release, review the
Buildroot release announcements and support status, update `BUILDROOT_RELEASE`
and its exact `BUILDROOT_REF` together, and rebase only the tracked patches.
Rebuild both profiles and run the guest boot/login, DHCP, utilities,
reboot/poweroff, terminal bridge, and runtime policy tests before adopting a pin.
GitHub Action pins are maintained through weekly Dependabot pull requests.

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

With the explicitly selected development profile, wait for `moos login:`,
enter root, and leave the password empty. This
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
HOST_GUEST_ISOLATION.md. The plain developer launcher remains separate from
the dedicated runtime account, host cgroups, and local `moosd` control path.

### Phase 3.1 managed runtime

Phase 3.1 adds a separate host account and a cgroup-enforced launch path. First
inspect the privileged setup; this dry-run does not change the host:

~~~bash
./scripts/setup-runtime-user.sh --dry-run
python3 tests/runtime_isolation.py
~~~

After provisioning the authenticated administrator release described in
`ADMIN_RELEASES.md`, an administrator can install the locked `moos-runtime`
account and private storage once:

~~~bash
ADMIN_ROOT=/usr/lib/moos/admin-current
sudo "$ADMIN_ROOT/scripts/setup-runtime-user.sh" --source-root "$ADMIN_ROOT"
sudo "$ADMIN_ROOT/scripts/stage-instance.sh" --id luna --image-dir "$PWD/output/images"
~~~

The account uses `nologin`, is locked, has no supplementary groups, and has no
access path to the developer checkout. The staged Instance contains only the
kernel and root filesystem; those files and the copied QEMU runtime are
root-owned and read-only to `moos-runtime`.

Run the managed example with automatic I/O-device detection:

~~~bash
sudo /usr/lib/moos/run-instance.sh --id luna
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

### M5 local control plane

M5 uses newline-delimited JSON with an incremental 16 KiB frame limit. A read
may contain part of a frame or several frames; malformed JSON, non-object JSON,
and oversized frames produce request-local errors instead of terminating the
daemon. The same decoder is used for terminal input/output, including a
terminal acknowledgment immediately followed by guest output.

The managed Personal unit owns
`/run/moos-instances/personal/console.sock`. QEMU serves its serial console on
that socket. A terminal client disconnect or `moosd` restart closes only that
connection; systemd continues to own QEMU, and a new daemon reconnects to the
same console endpoint. Before root connects, `moosd` rejects symlinks,
non-sockets, hard-linked sockets, and unexpected owners; after connecting it
verifies the peer UID with Linux `SO_PEERCRED`. The host path is never returned
by the protocol.

Provision the signed administrator release as documented in
`ADMIN_RELEASES.md`. Install or refresh the runtime launcher, stage the stable
Personal ID, and install the control plane only from that root-owned release:

~~~bash
./scripts/setup-runtime-user.sh --dry-run
./scripts/setup-control-plane.sh --dry-run
ADMIN_ROOT=/usr/lib/moos/admin-current
sudo "$ADMIN_ROOT/scripts/setup-runtime-user.sh" --source-root "$ADMIN_ROOT"
sudo "$ADMIN_ROOT/scripts/stage-instance.sh" \
  --id personal --image-dir "$PWD/output/images"
sudo "$ADMIN_ROOT/scripts/setup-control-plane.sh" --source-root "$ADMIN_ROOT"
~~~

`moosd.socket` is local Unix-socket activation at `/run/moos/moosd.sock`, mode
0660, owned by `root:moos-control`. `moosd.service` runs as
`root:moos-control` because the managed runner requires systemd authority. It
has an empty capability bounding set, read-only host system paths,
no-new-privileges, a fixed operation allowlist, restart-on-failure, and
journald logging. It creates no sudo policy. An administrator may explicitly
add a trusted local development user to `moos-control`; that grants the narrow
Personal API and guest console, not an arbitrary host-root shell.

No checkout byte is parsed as an archive, imported, or executed by root before
an externally anchored signature authenticates the exact copied administrator
bundle. The root-owned installer then validates and stages the control plane,
runs executable checks in a transient sandbox as `moos-runtime`, and atomically
switches the active links. Unit, documentation, links, and prior systemd state
are restored if activation fails.

After a new login has picked up that group membership:

~~~bash
moos status
moos personal start
moos personal terminal
moos personal stop
journalctl -u moosd.service
~~~

The guest still has the documented blank local-development root password.
Therefore the local socket group is security-sensitive and must never be
exposed directly. Remote status uses the separate status-only authenticated
Gateway described below.

### M6 Protocol v1

The local control plane now has an explicit transport-neutral Protocol v1
contract in `PROTOCOL.md`. It defines the bounded NDJSON wire format, typed
Personal status/lifecycle/terminal frames, structured errors, compatibility
rules, and the public runtime states. Both daemon and CLI validate exact integer
versions and operation-specific response shapes; incomplete frames remain
isolated to their connection. The Unix socket remains `moosd`'s only transport.
The separate MOOS Gateway adds a Tailscale-only listener, per-device
challenge authentication, revocation, rotation, and status-only authorization
without exposing the local daemon socket.

Build the Gateway and administrator bundle as an unprivileged user, have the
bundle signed outside the checkout, and install it through the trust anchor as
described in `ADMIN_RELEASES.md`. Then install the Gateway only from the
authenticated release:

~~~bash
ADMIN_ROOT=/usr/lib/moos/admin-current
sudo "$ADMIN_ROOT/scripts/setup-gateway.sh" \
  --tailscale-address "$(tailscale ip -4)" \
  --port 7411 \
  --source-root "$ADMIN_ROOT" \
  --binary-dir "$ADMIN_ROOT/target/release"
sudo moos-gateway-device add --name "My iPhone" --allow status
~~~

The second command prints a pairing code once. Enter that code with the Host's
Tailscale address and port in the iOS app. Device keys are stored in the iOS
Keychain and the root-managed Host device store; they are never committed.
The Gateway is a pinned, lockfile-reproducible Rust service; its installer only
accepts prebuilt matching release artifacts and rolls back the prior service
files when activation fails. See `GATEWAY.md` for the exact handshake,
compatibility, installation, rollback, and authorization boundary.

## Phase 1/2 smoke test

After building, validate the launcher policy and then run the standard host-side
smoke test:

~~~bash
python3 tests/runtime_isolation.py
python3 tests/qemu_launcher.py
python3 tests/qemu_smoke.py
python3 tests/qemu_terminal_bridge.py
~~~

The runtime policy test checks the dedicated account plan, private staging,
systemd cgroup values, host protections, and invalid input rejection. The
launcher test checks deny-by-default networking, resource limits, the rootless
sandbox, and invalid option rejection. The QEMU test boots through the
isolated launcher with explicit user-mode networking, waits for the login
prompt, checks the banner, kernel, BusyBox, RAM, uptime, /proc, /sys, DHCP,
rootfs space, and writable /tmp. It also exercises the four MOOS utilities,
reboots the guest, and verifies a clean poweroff and QEMU exit.

The real terminal-bridge test uses a private mount namespace rather than
changing host runtime state. It boots the real image, runs `moos-info` through
`moosd`, terminates and reaps the daemon while QEMU remains alive, starts a new
daemon, reconnects, runs `moos-info` again, and powers off cleanly. The final
checkout-safe M5 integration test uses a private mount namespace:

~~~bash
python3 tests/qemu_terminal_bridge.py
~~~

That unprivileged private-namespace test verifies actual QEMU start/stop plus
daemon-restart reconnect without interpreting checkout code as root. It passed
on the development host on 2026-08-17:
Personal reached the real login, `moos-info` worked through the framed bridge,
the guest stayed running across daemon restart, the terminal reconnected, and
managed stop reaped the processes. M5 is complete; this does not make the
local development protocol safe for remote exposure.

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

Additional host services and remote APIs must retain explicit, authenticated,
least-privilege operations. The Gateway uses Tailscale for encrypted transport
reachability, never as application authentication.

See AGENTS.md and rules.md for contribution rules, BUG_AUDIT.md for known
findings, HOST_GUEST_ISOLATION.md for the security boundary, and STEPS.md for
the next milestones.
