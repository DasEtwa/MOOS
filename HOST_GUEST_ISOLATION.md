# MOOS host/guest isolation

This document describes the current Phase 3 boundary between the MOOS host
launcher and a MOOS guest Instance. It is intentionally explicit about what is
already enforced, what the Phase 3.1 setup path enforces after activation, and
what still needs a dedicated host runtime design.

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
| I/O | 10 MiB/s read and write on the storage block device |
| Network | off; `--network user` remains explicit |

The service also enables `ProtectHome`, `ProtectProc=invisible`, and
`ProcSubset=all` for Bubblewrap compatibility. `ProcSubset=pid` would hide
`/proc/sys`, which Bubblewrap needs for its user-namespace setup. The service
also enables `ProtectSystem=strict`, `PrivateDevices`, `PrivateTmp`,
`NoNewPrivileges`, an empty capability bounding set, and group/task cleanup.
I/O is denied if the storage block device cannot be resolved; pass
`--io-device /dev/...` explicitly in that case. `--direct` and arbitrary QEMU
arguments are not available through the managed runner.

This is a host activation path, not a claim that the current developer machine
has already been modified. The one-time setup requires deliberate root
authorization.

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

The only current host-to-guest interaction channel is the local serial console.
It is an interactive development channel, not a remote API. The QEMU monitor is
disabled in the launcher, and there is no host socket, shared folder, guest
agent, or `moosd` channel yet.

The future shape is:

~~~text
client → authenticated moosd on the host → narrow Instance operation
                                      ↘ serial/IPC channel → guest
~~~

The guest must never turn that channel into arbitrary host command execution.
Host operations need explicit, typed, authenticated, validated, and revocable
interfaces.

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

Until those items exist, MOOS has a safe local QEMU baseline, not a complete
production-grade multi-Instance host manager.
