# MOOS host/guest isolation

This document describes the current Phase 3/M5 boundary between the MOOS host
control plane and a MOOS guest Instance. It is intentionally explicit about
what is already enforced, what privileged setup activates, and what remains
unsafe for remote exposure.

## Current default boundary

The standard serial launcher is:

~~~bash
./scripts/run-qemu.sh --serial-only
~~~

It runs QEMU through bubblewrap and creates a rootless user, mount, PID, IPC,
UTS, and cgroup namespace. The QEMU process is not host root. The sandbox
contains only:

- the QEMU executable and its runtime libraries/firmware
- the selected MOOS kernel and root filesystem image
- a synthetic `/dev` and `/proc`
- bounded temporary directories for QEMU snapshot files

The sandbox does not bind the repository, `/home`, `/root`, `/run`, arbitrary
host directories, host devices, or host sockets. The guest image is opened as a
temporary snapshot, so an ordinary session does not write the source image.

The launcher also uses TCG instead of KVM, one vCPU, and 256 MiB of guest RAM
by default. The backing root filesystem is currently a 60 MiB image. These are
QEMU-level limits; the managed Phase 3.1 path adds host cgroups, but the plain
developer launcher does not.

## Phase 3.1 host runtime

The repository now contains a deliberate activation path for a dedicated host
runtime account:

~~~text
administrator → setup-runtime-user.sh → moos-runtime (locked, nologin)
                                      ├── private /var/lib/moos storage
                                      ├── staged QEMU and Instance images
                                      └── systemd cgroup → bwrap → QEMU
~~~

Inspect the setup without changing the host:

~~~bash
./scripts/setup-runtime-user.sh --dry-run
python3 tests/runtime_isolation.py
~~~

After explicit administrator review, activate the account and stage a named
Instance:

~~~bash
sudo ./scripts/setup-runtime-user.sh --source-root "$PWD"
sudo ./scripts/stage-instance.sh --id luna --image-dir "$PWD/output/images"
~~~

`setup-runtime-user.sh` creates `moos-runtime` as a system account with a
locked password, `nologin`, no supplementary groups, no sudo policy, and no
developer-home storage. It creates `/var/lib/moos` with permissions that allow
only the runtime account and root to traverse it. The setup installs a root-owned
copy of the launcher under `/usr/lib/moos/`; the repository is not needed by the
runtime account.

`stage-instance.sh` copies only `bzImage` and `rootfs.ext2` into a new private
root-owned Instance directory. The images are group-readable but not writable
by `moos-runtime`; the QEMU binary, libraries, and firmware under
`/var/lib/moos/runtime/` follow the same root-owned pattern. Existing Instance
IDs are never replaced implicitly. The source image remains outside the runtime
account's normal path.

M5 does not move QEMU into `moosd`. The transient instance service remains the
QEMU owner. It creates one private runtime directory and QEMU listens on a
fixed serial socket inside it:

~~~text
/run/moos-instances/personal/console.sock
~~~

Only that directory is writable inside the QEMU Bubblewrap sandbox. It does
not expose arbitrary `/run` paths. The socket path is fixed by the managed
runner, validated by the launcher, omitted from client responses, and removed
with the transient unit. QEMU server-mode serial accepts a later connection,
so disconnecting a client or restarting `moosd` does not stop the guest.

Run the managed path with:

~~~bash
sudo ./scripts/run-instance.sh --id luna
~~~

The runner uses a transient systemd service owned by `moos-runtime`, then starts
the fixed serial launcher. Its default resource policy is:

| Resource | Limit |
| --- | --- |
| CPU | `CPUQuota=200%` |
| Memory | `MemoryMax=2G`, swap disabled |
| Host tasks | `TasksMax=512` |
| Guest memory | `1792M`, leaving QEMU overhead below the cgroup ceiling |
| vCPUs | 2 |
| I/O | 10 MB/s read and write on the storage block device (10,000,000 bytes/s) |
| Network | off; `--network user` remains explicit |

The service also enables `ProtectHome`, `ProtectProc=invisible`, and
`ProcSubset=all` for Bubblewrap compatibility. `ProcSubset=pid` would hide
`/proc/sys`, which Bubblewrap needs for its user-namespace setup. The unit
leaves `ProtectKernelTunables` and `ProtectKernelLogs` unset because those
systemd proc restrictions prevent rootless Bubblewrap from creating its
namespace-local `/proc` mount on the supported host configuration. This is a
deliberate compatibility boundary: the process is still the unprivileged
`moos-runtime` account, and the guest receives only Bubblewrap's synthetic
proc/sys view rather than host kernel control. The service also enables
`ProtectSystem=strict`, `ProtectKernelModules`, `PrivateDevices`, `PrivateTmp`,
`NoNewPrivileges`, and group/task cleanup. The unit intentionally does not set
an empty `CapabilityBoundingSet`: rootless Bubblewrap needs namespace-local
capabilities for its mount setup, and this does not grant host capabilities or
host root.
The address-family allowlist includes `AF_NETLINK` only because Bubblewrap
needs it to configure the isolated network namespace; it does not grant the
guest a host network interface. Guest networking remains controlled by the
launcher’s explicit `none` or `user` mode.
I/O is denied if the storage block device cannot be resolved; pass
`--io-device /dev/...` explicitly in that case. `--direct` and arbitrary QEMU
arguments are not available through the managed runner.

This is a host activation path that requires deliberate root authorization. It
was manually activated and tested for `test-phase31` on one Kubuntu host; that
result is host-specific and does not remove the need to inspect the policy on
other machines.

## Real-host validation

The deliberate Phase 3.1 test on 2026-08-17 produced these observations:

- the managed systemd unit stayed active while QEMU booted to the MOOS login;
- explicit QEMU user networking obtained `10.0.2.15` by DHCP;
- `CPUQuota=200%`, `MemoryMax=2G`, `MemorySwapMax=0`, `TasksMax=512`, and
  `10,000,000` byte/s read/write I/O limits were visible in the active unit;
- a bounded 1700M Guest tmpfs write reached a cgroup memory peak of
  `1,973,456,896` bytes while the unit remained active with `Result=success`;
- 600 bounded Guest `sleep` processes were started while the host cgroup
  remained at `TasksCurrent=6`; Guest PIDs did not become host tasks;
- `/home` and `/dev/kvm` were hidden from the Guest, and the Guest process list
  contained Guest kernel/userspace processes only.

The final M5 managed integration test on the same date additionally verified:

- the real `personal` transient unit started QEMU and reached the MOOS login;
- `moos-info` completed through the bounded local terminal bridge;
- terminating and reaping `moosd` did not stop the managed guest;
- a new daemon reconnected to the existing QEMU serial socket and terminal;
- managed stop succeeded, processes were reaped, and the unit ended inactive
  with `Result=success`.

`/root`, `/run`, and a possible `/dev/dri` entry in the Guest are not host
paths: they belong to the Guest filesystem or its emulated QEMU graphics
device. The test does not claim that the future host API, shared folders, or
host/guest IPC are implemented.

## Network boundary

Networking is disabled by default:

~~~bash
./scripts/run-qemu.sh --serial-only
~~~

The current development network can be explicitly enabled with QEMU user-mode
NAT:

~~~bash
./scripts/run-qemu.sh --serial-only --network user
~~~

This adds an emulated virtio network interface, gives the guest outbound SLIRP
networking, and creates no host port forwards. It is still a deliberate host
network capability, not an authorization mechanism. A future host policy must
decide whether a particular Instance receives it.

## Host/guest channel

The only host-to-guest interaction channel is still the guest serial console.
The managed path now makes that channel reconnectable and bridges it through a
local typed `moosd` operation:

~~~text
local moos-control client → /run/moos/moosd.sock → typed Personal operation
                                                    ↘ fixed QEMU serial socket
~~~

The client hop uses bounded incremental newline-delimited JSON framing.
Fragmented frames, several frames in one read, malformed JSON,
non-object JSON values, and oversized frames are handled without terminating
the daemon. Terminal input is written only to the QEMU serial socket. There is
no `/exec` operation, arbitrary host command, host path, QEMU argument, shared
folder, guest agent, or QEMU monitor in this interface.

`/run/moos` is traverse-only (`root:root`, mode 0711), while `moosd.socket` is
owned by `root:moos-control` with mode 0660. The socket mode therefore remains
the authorization boundary even though clients can traverse its parent. The service uses
socket activation, restart-on-failure, journald logging, read-only system
paths, no-new-privileges, and an empty capability bounding set. It retains UID
0 because the existing fixed runner must ask the system systemd manager to
create and stop the unprivileged instance service. Membership in
`moos-control` grants only the typed local Personal API and guest console; no
sudo rule is installed. Runtime socket directories are managed by systemd,
and daemon code is installed root-owned under `/usr/lib/moos`.

The local socket is not published remotely. `moos-gateway` is a separate,
unprivileged entry point that binds only to an explicit Tailscale address,
authenticates an individually revocable MOOS device key, authorizes every
operation, and forwards only validated Protocol-v1 frames. Tailscale supplies
the encrypted transport but is not treated as MOOS device identity. Gateway v1
remotely permits only `status`; `moosd` also enforces that restriction from the
Gateway's Unix peer UID. Lifecycle and terminal operations remain local. The
guest's blank development root password therefore remains unreachable from the
remote Gateway.

Runtime status is derived from systemd `LoadState`, `ActiveState`, `SubState`,
and `Result`, producing `starting`, `running`, `stopping`, `stopped`, `failed`,
or `unknown`. A systemctl failure is `unknown`, not silently `stopped`. Launch
uses a synchronous `systemd-run --service-type=exec` handoff, so the launcher
is reaped and immediate exec failures are reported. Stop preserves console
recoverability until systemctl confirms success.

## Explicit escape hatch

`--direct` bypasses the rootless bubblewrap sandbox for local development, for
example when a graphical QEMU window is needed:

~~~bash
./scripts/run-qemu.sh --direct --network user
~~~

This mode is not the security boundary and must not be used for an untrusted or
compromised guest. It is intentionally explicit so a normal launch cannot
silently fall back to it.

## Remaining Phase 3 work

- define stable Instance IDs and separate persistent metadata from runtime state
- add explicit shared-folder capabilities only where a use case requires them
- define and validate a narrow host/guest IPC contract
- add negative tests for path traversal, unauthorized operations, and cleanup
- make persistent Instance storage portable and separately backupable
- extend remote grants only after operation-specific authorization review

Until those items exist, MOOS has a safe local QEMU baseline, not a complete
production-grade multi-Instance host manager.
