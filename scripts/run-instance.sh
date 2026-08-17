#!/bin/sh

set -eu

RUNTIME_USER='moos-runtime'
RUNTIME_GROUP='moos-runtime'
STATE_ROOT='/var/lib/moos'
INSTANCE_ROOT="$STATE_ROOT/instances"
RUNTIME_QEMU_ROOT="$STATE_ROOT/runtime/qemu-host"
LIBEXEC_ROOT='/usr/lib/moos'
INSTANCE_ID=''
NETWORK='none'
GUEST_MEMORY='1792M'
VCPUS='2'
CPU_QUOTA='200%'
MEMORY_MAX='2G'
TASKS_MAX='512'
IO_DEVICE=''
IO_READ='10M'
IO_WRITE='10M'
DRY_RUN=0

usage() {
    cat <<'EOF'
Usage: scripts/run-instance.sh --id ID [options]

Run a staged MOOS Instance through a systemd cgroup as moos-runtime. The
command accepts no arbitrary QEMU or host command arguments.

Defaults for this Phase 3.1 runner:
  CPU quota:       200% (up to two host cores)
  memory cgroup:   2G
  guest memory:    1792M (headroom for QEMU itself)
  process limit:   512 host tasks
  I/O:             10M read and 10M write on the Instance storage device
  network:         none

Options:
  --id ID            Staged Instance label.
  --network MODE     "none" (default) or explicit QEMU "user" networking.
  --memory SIZE      Guest memory, up to 2G.
  --cpus COUNT       Guest vCPU count, 1 or 2.
  --io-device PATH   Block device containing /var/lib/moos.
  --io-read SIZE     Read bandwidth, default 10M.
  --io-write SIZE    Write bandwidth, default 10M.
  --dry-run          Print the systemd/cgroup policy without starting.
  -h, --help         Show this help.

The real launch requires root because systemd must create a service owned by
moos-runtime. The runtime account itself has no login, sudo, or extra groups.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --id)
            [ "$#" -ge 2 ] || {
                echo 'error: --id requires a value' >&2
                exit 2
            }
            INSTANCE_ID="$2"
            shift 2
            ;;
        --network)
            [ "$#" -ge 2 ] || {
                echo 'error: --network requires none or user' >&2
                exit 2
            }
            NETWORK="$2"
            shift 2
            ;;
        --memory)
            [ "$#" -ge 2 ] || {
                echo 'error: --memory requires a size such as 1792M' >&2
                exit 2
            }
            GUEST_MEMORY="$2"
            shift 2
            ;;
        --cpus)
            [ "$#" -ge 2 ] || {
                echo 'error: --cpus requires 1 or 2' >&2
                exit 2
            }
            VCPUS="$2"
            shift 2
            ;;
        --io-device)
            [ "$#" -ge 2 ] || {
                echo 'error: --io-device requires a block-device path' >&2
                exit 2
            }
            IO_DEVICE="$2"
            shift 2
            ;;
        --io-read)
            [ "$#" -ge 2 ] || {
                echo 'error: --io-read requires a size such as 10M' >&2
                exit 2
            }
            IO_READ="$2"
            shift 2
            ;;
        --io-write)
            [ "$#" -ge 2 ] || {
                echo 'error: --io-write requires a size such as 10M' >&2
                exit 2
            }
            IO_WRITE="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "error: unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

case "$INSTANCE_ID" in
    [a-z][a-z0-9-]*)
        ;;
    *)
        echo 'error: --id must start with a lowercase letter and contain only a-z, 0-9, or -' >&2
        exit 2
        ;;
esac
id_length=$(expr length "$INSTANCE_ID")
[ "$id_length" -le 32 ] || {
    echo 'error: --id is limited to 32 characters' >&2
    exit 2
}

case "$NETWORK" in
    none|user)
        ;;
    *)
        echo 'error: --network must be none or user' >&2
        exit 2
        ;;
esac

case "$GUEST_MEMORY" in
    *[Mm])
        MEMORY_VALUE=$(printf '%s' "$GUEST_MEMORY" | sed 's/[Mm]$//')
        MEMORY_MULTIPLIER=1
        ;;
    *[Gg])
        MEMORY_VALUE=$(printf '%s' "$GUEST_MEMORY" | sed 's/[Gg]$//')
        MEMORY_MULTIPLIER=1024
        ;;
    *)
        echo 'error: --memory must be a number with M or G suffix' >&2
        exit 2
        ;;
esac
case "$MEMORY_VALUE" in
    ''|*[!0-9]*)
        echo 'error: --memory must contain only digits before its suffix' >&2
        exit 2
        ;;
esac
MEMORY_MIB=$((MEMORY_VALUE * MEMORY_MULTIPLIER))
[ "$MEMORY_MIB" -gt 0 ] && [ "$MEMORY_MIB" -le 2048 ] || {
    echo 'error: guest memory must be between 1M and 2G' >&2
    exit 2
}

case "$VCPUS" in
    1|2)
        ;;
    *)
        echo 'error: --cpus must be 1 or 2' >&2
        exit 2
        ;;
esac

for io_limit in "$IO_READ" "$IO_WRITE"; do
    case "$io_limit" in
        ''|*[!0-9KMGkmg]*)
            echo 'error: I/O limits must contain digits and an optional K, M, or G suffix' >&2
            exit 2
            ;;
    esac
done

if [ -z "$IO_DEVICE" ] && command -v findmnt >/dev/null 2>&1 && [ -d "$STATE_ROOT" ]; then
    IO_DEVICE=$(findmnt -no SOURCE -T "$STATE_ROOT" 2>/dev/null || true)
fi

INSTANCE_DIR="$INSTANCE_ROOT/$INSTANCE_ID"
STAGED_QEMU="$RUNTIME_QEMU_ROOT/bin/qemu-system-x86_64"
UNIT="moos-instance-$INSTANCE_ID.service"

if [ -n "$IO_DEVICE" ]; then
    io_description="$IO_DEVICE ($IO_READ read / $IO_WRITE write)"
else
    io_description='unresolved; launch will be refused until a block device is supplied'
fi

if [ "$DRY_RUN" -eq 1 ]; then
    printf 'instance: %s\n' "$INSTANCE_ID"
    printf 'runtime user: %s:%s (locked, nologin, no supplementary groups)\n' "$RUNTIME_USER" "$RUNTIME_GROUP"
    printf 'systemd unit: %s\n' "$UNIT"
    printf 'launcher: %s/run-qemu.sh\n' "$LIBEXEC_ROOT"
    printf 'instance storage: %s\n' "$INSTANCE_DIR"
    printf 'guest memory/vCPUs: %s / %s\n' "$GUEST_MEMORY" "$VCPUS"
    printf 'cgroup CPUQuota: %s\n' "$CPU_QUOTA"
    printf 'cgroup MemoryMax: %s\n' "$MEMORY_MAX"
    printf 'cgroup TasksMax: %s\n' "$TASKS_MAX"
    printf 'cgroup I/O: %s\n' "$io_description"
    printf 'network: %s\n' "$NETWORK"
    printf 'host home: protected (ProtectHome=yes, no source bind)\n'
    printf 'host processes: hidden (ProtectProc=invisible, ProcSubset=all for bwrap)\n'
    printf 'host devices: private (PrivateDevices=yes, no KVM/USB/GPU passthrough)\n'
    printf 'arbitrary host command API: none\n'
    printf 'host mutation: none (dry-run)\n'
    exit 0
fi

[ "$(id -u)" -eq 0 ] || {
    echo 'error: running a managed Instance requires root/systemd authorization' >&2
    echo '       use --dry-run as a normal user to inspect the policy' >&2
    exit 1
}
for command_name in awk cut getent grep id passwd; do
    command -v "$command_name" >/dev/null 2>&1 || {
        echo "error: required host command is missing: $command_name" >&2
        exit 1
    }
done
[ -x "$LIBEXEC_ROOT/run-qemu.sh" ] || {
    echo "error: installed launcher is not executable: $LIBEXEC_ROOT/run-qemu.sh" >&2
    exit 1
}
[ -x "$STAGED_QEMU" ] || {
    echo "error: staged QEMU is not executable: $STAGED_QEMU" >&2
    exit 1
}
runtime_passwd_entry=$(getent passwd "$RUNTIME_USER" 2>/dev/null || true)
[ -n "$runtime_passwd_entry" ] || {
    echo "error: runtime user does not exist: $RUNTIME_USER" >&2
    exit 1
}
runtime_home=$(printf '%s\n' "$runtime_passwd_entry" | cut -d: -f6)
runtime_shell=$(printf '%s\n' "$runtime_passwd_entry" | cut -d: -f7)
[ "$runtime_home" = "$STATE_ROOT" ] || {
    echo "error: runtime user has an unexpected home: $runtime_home" >&2
    exit 1
}
[ "$runtime_shell" = '/usr/sbin/nologin' ] || [ "$runtime_shell" = '/sbin/nologin' ] || {
    echo "error: runtime user has an interactive shell: $runtime_shell" >&2
    exit 1
}
runtime_groups=$(id -nG "$RUNTIME_USER")
[ "$runtime_groups" = "$RUNTIME_GROUP" ] || {
    echo "error: runtime user has unexpected groups: $runtime_groups" >&2
    exit 1
}
password_state=$(passwd -S "$RUNTIME_USER" 2>/dev/null | awk '{print $2}')
case "$password_state" in
    L|LK)
        ;;
    *)
        echo "error: runtime user password is not locked" >&2
        exit 1
        ;;
esac
[ -n "$IO_DEVICE" ] || {
    echo 'error: I/O limit could not resolve the storage block device' >&2
    echo '       pass --io-device /dev/... explicitly' >&2
    exit 1
}
[ -b "$IO_DEVICE" ] || {
    echo "error: I/O device is not a block device: $IO_DEVICE" >&2
    exit 1
}

for required_path in "$INSTANCE_DIR" "$INSTANCE_DIR/bzImage" "$INSTANCE_DIR/rootfs.ext2" "$STAGED_QEMU" "$LIBEXEC_ROOT/run-qemu.sh"; do
    [ -e "$required_path" ] || {
        echo "error: staged runtime path is missing: $required_path" >&2
        echo '       run setup-runtime-user.sh and stage-instance.sh first' >&2
        exit 1
    }
done
[ -d "$INSTANCE_DIR" ] && [ -r "$INSTANCE_DIR/bzImage" ] && [ -r "$INSTANCE_DIR/rootfs.ext2" ] || {
    echo "error: runtime user cannot read Instance storage: $INSTANCE_DIR" >&2
    exit 1
}

[ -d /run/systemd/system ] || {
    echo 'error: the system systemd manager is not active' >&2
    exit 1
}
[ -f /sys/fs/cgroup/cgroup.controllers ] || {
    echo 'error: unified cgroup v2 is required for this runner' >&2
    exit 1
}
for controller in cpu memory pids io; do
    grep -qw "$controller" /sys/fs/cgroup/cgroup.controllers || {
        echo "error: cgroup v2 controller is unavailable: $controller" >&2
        exit 1
    }
done
command -v systemd-run >/dev/null 2>&1 || {
    echo 'error: systemd-run is required for cgroup-enforced Instances' >&2
    exit 1
}

run_managed_instance() {
    if [ -n "$IO_DEVICE" ]; then
        exec systemd-run \
            --unit="$UNIT" \
            --service-type=exec \
            --pty \
            --wait \
            --collect \
            --uid="$RUNTIME_USER" \
            --gid="$RUNTIME_GROUP" \
            --property=SupplementaryGroups= \
            --working-directory="$INSTANCE_DIR" \
            --setenv=HOME=/nonexistent \
            --setenv=TMPDIR=/tmp \
            --property=CPUQuota="$CPU_QUOTA" \
            --property=MemoryMax="$MEMORY_MAX" \
            --property=MemorySwapMax=0 \
            --property=TasksMax="$TASKS_MAX" \
            --property="IOReadBandwidthMax=$IO_DEVICE $IO_READ" \
            --property="IOWriteBandwidthMax=$IO_DEVICE $IO_WRITE" \
            --property=IOAccounting=yes \
            --property=NoNewPrivileges=yes \
            --property=PrivateDevices=yes \
            --property=PrivateTmp=yes \
            --property=ProtectHome=yes \
            --property=ProtectProc=invisible \
            --property=ProcSubset=all \
            --property=ProtectSystem=strict \
            --property=ProtectKernelModules=yes \
            --property=ProtectControlGroups=yes \
            --property=RestrictSUIDSGID=yes \
            --property=RestrictRealtime=yes \
            --property=Delegate=no \
            --property=KillMode=control-group \
            --property=OOMPolicy=stop \
            --property=UMask=0077 \
            --property='RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK' \
            -- \
            "$LIBEXEC_ROOT/run-qemu.sh" \
            --serial-only \
            --image-dir "$INSTANCE_DIR" \
            --qemu "$STAGED_QEMU" \
            --network "$NETWORK" \
            --memory "$GUEST_MEMORY" \
            --cpus "$VCPUS"
    fi

    exec systemd-run \
        --unit="$UNIT" \
        --service-type=exec \
        --pty \
        --wait \
        --collect \
        --uid="$RUNTIME_USER" \
        --gid="$RUNTIME_GROUP" \
        --property=SupplementaryGroups= \
        --working-directory="$INSTANCE_DIR" \
        --setenv=HOME=/nonexistent \
        --setenv=TMPDIR=/tmp \
        --property=CPUQuota="$CPU_QUOTA" \
        --property=MemoryMax="$MEMORY_MAX" \
        --property=MemorySwapMax=0 \
        --property=TasksMax="$TASKS_MAX" \
        --property=NoNewPrivileges=yes \
        --property=PrivateDevices=yes \
        --property=PrivateTmp=yes \
        --property=ProtectHome=yes \
        --property=ProtectProc=invisible \
        --property=ProcSubset=all \
        --property=ProtectSystem=strict \
        --property=ProtectKernelModules=yes \
        --property=ProtectControlGroups=yes \
        --property=RestrictSUIDSGID=yes \
        --property=RestrictRealtime=yes \
        --property=Delegate=no \
        --property=KillMode=control-group \
        --property=OOMPolicy=stop \
        --property=UMask=0077 \
        --property='RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK' \
        -- \
        "$LIBEXEC_ROOT/run-qemu.sh" \
        --serial-only \
        --image-dir "$INSTANCE_DIR" \
        --qemu "$STAGED_QEMU" \
        --network "$NETWORK" \
        --memory "$GUEST_MEMORY" \
        --cpus "$VCPUS"
}

run_managed_instance
