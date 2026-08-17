# MOOS initial bug audit

This is an evidence-based audit of the local MOOS workspace before repository
preparation. The workspace contained a Buildroot checkout, a generated
output/ tree, and the MOOS login overlay, but no top-level Git repository.
The current QEMU image was booted and checked during the audit.

Severity describes risk in a future deployed system. The current image is a
local development prototype and is not suitable for remote exposure.

## Findings

## MOOS-0001

Severity: High
Component: guest authentication
Category: Critical for deployment
Status: Open

### Problem

The generated guest has an empty root password. The root shell can be reached
by entering root at the console and leaving the password empty; the generated
shadow entry is also empty for root.

### Impact

This is convenient for local QEMU development but would allow trivial
unauthorized access if the image or a future remote console were exposed.

### Reproduction

Boot the image with the QEMU launcher, enter root at the login prompt, and
submit an empty password. Alternatively inspect the generated guest shadow file
with:

~~~text
grep '^root:' output/target/etc/shadow
~~~

### Proposed fix

Keep a clearly marked development configuration separate from a release
configuration. Before any remote deployment, require explicit provisioning or
key-based authentication, disable or restrict root login, and add an
integration check that rejects insecure release settings. Never commit a real
password or private credential.

## MOOS-0002

Severity: Medium
Component: QEMU startup
Category: Reproducibility
Status: Resolved in repository preparation

### Problem

The generated Buildroot launcher embedded an absolute path to the local
developer's output/host/bin directory.

### Impact

Moving the checkout or cloning it on another machine made the generated
launcher dependent on a path that does not exist there.

### Reproduction

Inspect the generated launcher:

~~~text
grep -En 'Documents|output/host' output/images/start-qemu.sh
~~~

### Proposed fix

Use the tracked scripts/run-qemu.sh, which resolves paths relative to the
repository and falls back to a system QEMU binary. Generated launchers remain
ignored.

## MOOS-0003

Severity: Medium
Component: repository/build process
Category: Reproducibility
Status: Resolved in repository preparation

### Problem

The original workspace had no top-level Git repository, no tracked Buildroot
defconfig, and no documented build entry point. The working Buildroot checkout
and generated output were local state only.

### Impact

A fresh clone could not determine the selected Buildroot revision, how the
overlay was applied, or how to rebuild the image. The generated workspace also
occupied roughly 14 GB and was at risk of accidental staging.

### Reproduction

Before preparation, running git status at the workspace root reported that it
was not a Git repository. The only Git repository was the unrelated local
Buildroot checkout.

### Proposed fix

Track the MOOS defconfig, portable build/run scripts, repository documentation,
and a specific Buildroot commit. Ignore Buildroot and all generated trees.

## MOOS-0004

Severity: Medium
Component: Buildroot configuration
Category: Portability
Status: Resolved in repository preparation

### Problem

The only effective configuration lived in generated output/.config. It
contained a machine-specific BR2_DEFCONFIG path, while the overlay path
depended on the local layout.

### Impact

The configuration was difficult to reproduce outside the original checkout and
could silently diverge from the source overlay.

### Reproduction

Inspect the generated configuration:

~~~text
grep -E '^BR2_(DEFCONFIG|ROOTFS_OVERLAY)' output/.config
~~~

### Proposed fix

Track configs/moos_qemu_x86_64_defconfig and make scripts/build.sh create the
expected repository-relative Buildroot layout before configuring.

## MOOS-0005

Severity: Observation
Component: QEMU networking
Category: Current behavior
Status: Verified

### Finding

The default launcher has no guest network device. With the explicit
`--network user` option, QEMU supplies user-mode NAT and the smoke test performs
DHCP on eth0 after login. No host port forwarding or host directory is shared
with the guest by the current launcher.

### Significance

This is suitable for the current local smoke test and limits direct host
network integration, but the explicit user-mode network still gives QEMU
outbound host-network capability and is not a remote-access design. Future host
services must define their own authentication, authorization, and isolation.

## MOOS-0006

Severity: Observation
Component: init and login
Category: Current behavior
Status: Verified

### Finding

BusyBox init mounts the basic filesystems, starts the Buildroot init scripts,
starts a serial getty, and adds a tty1 getty for the QEMU graphical console.
The profile overlay prints the dynamic MOOS banner for interactive shells.

### Significance

The behavior is intentionally small and currently works in QEMU. It should be
covered by focused boot/login smoke tests before adding services.

## MOOS-0007

Severity: Observation
Component: filesystem construction
Category: Current behavior
Status: Verified

### Finding

Buildroot creates a 60 MiB ext2 image. The image reports roughly 55.1 MiB
usable space because the filesystem reserves blocks; the observed rootfs use was
roughly 5.7 MiB.

### Significance

The distinction between image size, usable space, and used space should remain
clear in future size reports.

## MOOS-0008

Severity: Future Risk
Component: remote architecture
Category: Remote boundary
Status: Local control implemented; remote exposure remains prohibited

### Finding

The repository now has a local Unix-socket `moosd` with fixed Personal
status/start/stop/terminal operations. It does not have remote application
authentication, Tailscale integration, a mobile client, or streaming.

### Significance

The local daemon is not a remote endpoint. Host control remains separate from
guest serial input, arbitrary host execution is absent, and the socket is
restricted to a dedicated local group. Any later remote boundary must
authenticate and authorize every operation and encrypt transport.

## MOOS-0009

Severity: Observation
Component: host/guest boundary
Category: Current behavior
Status: Verified

### Finding

The default serial launcher runs QEMU through a rootless bubblewrap namespace.
Only the QEMU runtime files and selected image files are mounted into the
sandbox. `/home`, `/root`, `/run`, arbitrary repository paths, host devices,
and host sockets are not mounted. QEMU uses TCG, one vCPU, 256 MiB by default,
and a temporary rootfs snapshot.

### Significance

The current boundary protects the host from ordinary guest filesystem access
and avoids exposing KVM, USB, GPU, clipboard, or shared-folder capabilities by
default. This is a local QEMU baseline, not yet a complete multi-Instance host
runtime.

## MOOS-0010

Severity: Future Risk
Component: QEMU hardening
Category: Host/guest isolation
Status: Not an active bug

### Finding

The Buildroot-provided host QEMU binary was checked locally and does not have
QEMU's optional `-sandbox` seccomp support enabled. The current boundary relies
on rootless bubblewrap namespaces, TCG, explicit image mounts, and the absence
of host device bindings.

### Significance

A future hardened runtime should evaluate a QEMU build with seccomp support or
an equivalent external policy. The new Phase 3.1 runtime path adds an external
systemd/cgroup policy, but this QEMU limitation remains until seccomp or an
equivalent policy is deliberately evaluated.

## MOOS-0011

Severity: Observation
Component: host runtime setup
Category: Phase 3.1 activation
Status: Verified on one Kubuntu host; broader host coverage pending

### Finding

The repository contains root-only setup and launch scripts for a dedicated
`moos-runtime` system account. The setup uses a locked `nologin` account with
no supplementary groups, private `/var/lib/moos` storage, and a root-owned
launcher copy. Instance staging copies only the kernel and root filesystem.
The managed launcher creates a transient systemd service with `CPUQuota=200%`,
`MemoryMax=2G`, `TasksMax=512`, automatic or explicit block-device I/O limits,
`ProtectHome`, `ProtectSystem=strict`, `PrivateDevices`, and no new privileges.

The privileged setup was activated deliberately on one Kubuntu development
host for Instance `test-phase31`. The managed service reached the MOOS login,
obtained a user-mode DHCP lease, and accepted the configured systemd/cgroup
policy. This is evidence for that host and configuration, not a guarantee that
every distribution or systemd version accepts the same properties.

### Significance

This keeps host-account creation and cgroup enforcement explicit and
reproducible without silently changing the developer's machine. A managed
Instance must not be treated as protected by these controls until the setup is
reviewed, activated, and checked with `systemctl status` and cgroup inspection
on the target host.

## MOOS-0012

Severity: High
Component: M5 local control and terminal bridge
Category: Reliability and control-plane boundary
Status: Resolved and verified, including privileged managed acceptance

### Findings and fixes

The original M5 implementation assumed one `recv()` was one JSON message. This
lost fragmented frames, merged concatenated frames, and could merge the
terminal acknowledgment with first output. `FrameDecoder` now incrementally
parses newline-delimited object frames, preserves partial data, returns every
complete frame, limits encoded frames to 16 KiB, and reports malformed,
non-object, truncated, and oversized input without killing `moosd`.

Terminal dispatch previously called `.get()` on any decoded JSON value, so
arrays, strings, numbers, and null could raise `AttributeError`. Object type
validation now occurs before business dispatch for request and terminal
frames. Per-client handlers also contain unexpected failures.

The old console was a PTY master stored only in the daemon process. QEMU now
owns a fixed server-mode Unix serial socket in its systemd-managed private
runtime directory. Each terminal session opens a fresh connection, and QEMU
continues when a client or daemon disconnects. No VM restart is used for
recovery.

The daemon previously discarded a long-lived `Popen` launcher and could miss
startup failure. `run-instance.sh` now hands QEMU directly to a transient
`systemd-run --service-type=exec` unit without `--pty` or `--wait`; the daemon
uses synchronous `subprocess.run`, reaps the launcher, checks its return code,
checks systemd state, and waits for the console endpoint. Stop retains the
console when systemctl fails and checks state only after successful stop.

Status previously inspected only `ActiveState` and treated every systemctl
error as stopped. It now combines `LoadState`, `ActiveState`, `SubState`, and
`Result` into starting, running, stopping, stopped, failed, or unknown. Manager
errors are unknown and block a risky duplicate start.

The repository now defines a socket-activated root broker with a dedicated
`moos-control` client group, mode-0660 local socket, systemd-owned runtime
directory, restart-on-failure, journald logging, and service hardening. Root is
retained only for the fixed systemd lifecycle handoff. No sudo policy,
arbitrary command, host path, QEMU argument, or remote listener is exposed.

### Verification

Regression tests cover fragmented and concatenated requests, fragmented and
combined responses, all invalid JSON value types, malformed and oversized
JSON, fragmented terminal frames, disconnect/reconnect, startup failure,
failed stop, structured status, and a new runtime object reconnecting to the
same console. `tests/qemu_terminal_bridge.py` booted the real image, ran
`moos-info` through the bridge, terminated and reaped the daemon while QEMU
remained alive, then reconnected through a new daemon and ran `moos-info`
again.

The administrator-executed `tests/managed_personal.py` final acceptance test
passed on 2026-08-17. It started the real systemd-managed Personal guest,
reached its login, ran `moos-info` through the framed terminal, restarted and
reaped `moosd` while the guest stayed active, reconnected the terminal, then
stopped Personal and reaped the managed processes. The unit ended absent and
inactive with `Result=success`. The M5 High findings are resolved.

## Area checklist

| Area | Audit result |
| --- | --- |
| Build reproducibility | Initial workspace was not reproducible from Git; tracked pin/config/scripts now address this. |
| Rootfs construction | Verified Buildroot overlay and 60 MiB ext2 image; root authentication remains open. |
| QEMU startup | Boot verified; generated launcher had an absolute path and is replaced by a portable wrapper. |
| Filesystem permissions | Generated target files are build intermediates owned by the host until fakeroot image creation; final image creation was verified. |
| Init | BusyBox init and Buildroot init scripts run; serial and tty1 gettys are intentional. |
| Networking | Default is disabled; explicit QEMU user networking plus DHCP works; local moosd is not a remote service. |
| Shell/login | Root login and dynamic banner work; blank root password is unsafe outside development. |
| Hard-coded paths | Generated launcher/config contained a developer path; tracked scripts avoid it. |
| Generated artifacts | buildroot/, output/, and host tools are large generated/local state and must stay ignored. |
| Temporary files | Buildroot creates temporary files under its ignored output/build trees; no tracked temporary file was found. |
| Host/guest boundaries | Rootless QEMU sandbox and one real Phase 3.1 activation verified; M5 adds only a fixed local serial socket and typed local broker. |
| Secret exposure | No secret or credential was found; the empty root password is an insecure configuration, not a leaked secret. |
| Developer-machine assumptions | Original launcher and generated config depended on the original absolute checkout path; portable scripts remove that dependency. |

## Audit limits

This audit covers the MOOS-specific overlay, Buildroot configuration, generated
image behavior, and local workspace layout. It does not treat the upstream
Buildroot source as MOOS-owned code and does not claim exhaustive upstream
security review.
