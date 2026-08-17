#!/bin/sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MOOS_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
OUTPUT_DIR="$MOOS_ROOT/output"
IMAGES_DIR="$OUTPUT_DIR/images"
QEMU="$OUTPUT_DIR/host/bin/qemu-system-x86_64"
QEMU_EXPLICIT=0
SERIAL_ONLY=0
ISOLATED=1
NETWORK_MODE='none'
MEMORY='256M'
CPUS='1'
CONSOLE_SOCKET=''
MAX_MEMORY_MIB=4096
MAX_CPUS=8
DRY_RUN=0

usage() {
    cat <<'EOF'
Usage: scripts/run-qemu.sh [options] [qemu-options]

Options:
  --serial-only       Run without a graphical display.
  --network MODE      Use "none" (default) or explicit QEMU "user" networking.
  --image-dir DIR     Use an explicit instance image directory.
  --qemu PATH         Use an explicit qemu-system-x86_64 binary.
  --memory SIZE       Guest memory, default 256M.
  --cpus COUNT        Guest vCPU count, default 1.
  --console-socket PATH
                      Use the managed /run/moos-instances/ID/console.sock.
  --direct            Bypass the rootless host sandbox (explicit dev escape hatch).
  --dry-run           Print the isolation configuration without starting QEMU.
  -h, --help          Show this help.

The default launcher uses bubblewrap, a user/mount/PID/IPC namespace, TCG,
explicit devices, no shared folders, and a temporary disk snapshot. The
isolated mode currently requires --serial-only. Use --direct only for an
explicitly non-isolated developer launch.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --serial-only)
            SERIAL_ONLY=1
            shift
            ;;
        --network)
            [ "$#" -ge 2 ] || {
                echo 'error: --network requires none or user' >&2
                exit 2
            }
            NETWORK_MODE="$2"
            shift 2
            ;;
        --image-dir)
            [ "$#" -ge 2 ] || {
                echo 'error: --image-dir requires a directory' >&2
                exit 2
            }
            IMAGES_DIR="$2"
            shift 2
            ;;
        --qemu)
            [ "$#" -ge 2 ] || {
                echo 'error: --qemu requires a qemu-system-x86_64 path' >&2
                exit 2
            }
            QEMU="$2"
            QEMU_EXPLICIT=1
            shift 2
            ;;
        --memory)
            [ "$#" -ge 2 ] || {
                echo 'error: --memory requires a size such as 256M' >&2
                exit 2
            }
            MEMORY="$2"
            shift 2
            ;;
        --cpus)
            [ "$#" -ge 2 ] || {
                echo 'error: --cpus requires a positive integer' >&2
                exit 2
            }
            CPUS="$2"
            shift 2
            ;;
        --console-socket)
            [ "$#" -ge 2 ] || {
                echo 'error: --console-socket requires a path' >&2
                exit 2
            }
            CONSOLE_SOCKET="$2"
            shift 2
            ;;
        --direct)
            ISOLATED=0
            shift
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --)
            shift
            break
            ;;
        -*)
            break
            ;;
        *)
            echo "error: unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

case "$NETWORK_MODE" in
    none|off)
        NETWORK_MODE='none'
        ;;
    user)
        ;;
    *)
        echo 'error: --network must be none or user' >&2
        exit 2
        ;;
esac

case "$MEMORY" in
    *[Mm])
        MEMORY_VALUE=${MEMORY%[Mm]}
        MEMORY_MULTIPLIER=1
        MEMORY_SUFFIX='M'
        ;;
    *[Gg])
        MEMORY_VALUE=${MEMORY%[Gg]}
        MEMORY_MULTIPLIER=1024
        MEMORY_SUFFIX='G'
        ;;
    *)
        echo 'error: --memory must be a number with M or G suffix' >&2
        exit 2
        ;;
esac

case "$MEMORY_VALUE" in
    ''|*[!0-9]*|?????*)
        echo 'error: --memory must be a positive value of at most four digits' >&2
        exit 2
        ;;
esac

MEMORY_MIB=$((MEMORY_VALUE * MEMORY_MULTIPLIER))
[ "$MEMORY_MIB" -gt 0 ] || {
    echo 'error: --memory must be positive' >&2
    exit 2
}
[ "$MEMORY_MIB" -le "$MAX_MEMORY_MIB" ] || {
    echo 'error: --memory is capped at 4096M' >&2
    exit 2
}
MEMORY="${MEMORY_VALUE}${MEMORY_SUFFIX}"

case "$CPUS" in
    ''|*[!0-9]*)
        echo 'error: --cpus must be a positive integer' >&2
        exit 2
        ;;
esac

[ "$CPUS" -gt 0 ] || {
    echo 'error: --cpus must be a positive integer' >&2
    exit 2
}
[ "$CPUS" -le "$MAX_CPUS" ] || {
    echo 'error: --cpus is capped at 8' >&2
    exit 2
}

if [ -n "$CONSOLE_SOCKET" ]; then
    CONSOLE_DIR=${CONSOLE_SOCKET%/console.sock}
    CONSOLE_ID=${CONSOLE_DIR##*/}
    [ "$CONSOLE_DIR" = "/run/moos-instances/$CONSOLE_ID" ] || {
        echo 'error: console socket must be /run/moos-instances/ID/console.sock' >&2
        exit 2
    }
    case "$CONSOLE_ID" in
        ''|[!a-z]*|*[!a-z0-9-]*)
            echo 'error: console socket Instance ID is invalid' >&2
            exit 2
            ;;
    esac
    [ "${#CONSOLE_ID}" -le 32 ] || {
        echo 'error: console socket Instance ID is too long' >&2
        exit 2
    }
fi

if [ "$QEMU_EXPLICIT" -eq 0 ] && [ ! -x "$QEMU" ]; then
    QEMU=$(command -v qemu-system-x86_64 || true)
fi

if [ -z "$QEMU" ] || [ ! -x "$QEMU" ]; then
    if [ "$QEMU_EXPLICIT" -eq 1 ]; then
        echo "error: explicit QEMU binary is missing or not executable: $QEMU" >&2
    else
        echo 'error: no qemu-system-x86_64 found; run scripts/build.sh first' >&2
    fi
    exit 1
fi

if [ ! -d "$IMAGES_DIR" ]; then
    echo "error: image directory does not exist: $IMAGES_DIR" >&2
    exit 1
fi
IMAGES_DIR=$(CDPATH= cd -- "$IMAGES_DIR" && pwd)

if [ ! -f "$IMAGES_DIR/bzImage" ] || [ ! -f "$IMAGES_DIR/rootfs.ext2" ]; then
    echo 'error: MOOS images are missing; run scripts/build.sh first' >&2
    exit 1
fi

if [ "$ISOLATED" -eq 1 ] && [ "$SERIAL_ONLY" -ne 1 ]; then
    echo 'error: isolated mode currently requires --serial-only' >&2
    echo '       use --direct for an explicit graphical developer launch' >&2
    exit 2
fi

QEMU_BIN_DIR=$(CDPATH= cd -- "$(dirname -- "$QEMU")" && pwd)
QEMU_LIB_DIR=$(CDPATH= cd -- "$QEMU_BIN_DIR/../lib" && pwd)
QEMU_SHARE_DIR="$QEMU_BIN_DIR/../share/qemu"
if [ ! -d "$QEMU_SHARE_DIR" ] && [ -d /usr/share/qemu ]; then
    QEMU_SHARE_DIR='/usr/share/qemu'
fi

if [ ! -d "$QEMU_LIB_DIR" ] || [ ! -d "$QEMU_SHARE_DIR" ]; then
    echo 'error: QEMU runtime libraries or firmware directory is missing' >&2
    exit 1
fi

if [ "$DRY_RUN" -eq 1 ]; then
    if [ "$ISOLATED" -eq 1 ]; then
        isolation='bubblewrap rootless sandbox (user/mount/PID/IPC namespaces)'
    else
        isolation='direct host process (explicit --direct escape hatch)'
    fi

    if [ "$NETWORK_MODE" = 'user' ]; then
        network='QEMU user-mode NAT (explicit; no host forwards)'
    else
        network='disabled'
    fi

    printf 'isolation: %s\n' "$isolation"
    printf 'network: %s\n' "$network"
    printf 'memory: %s\n' "$MEMORY"
    printf 'cpus: %s\n' "$CPUS"
    printf 'disk: temporary snapshot of rootfs.ext2\n'
    printf 'shared folders: none\n'
    printf 'host device passthrough: none (TCG, no KVM/USB/GPU passthrough)\n'
    printf 'host-guest channel: serial console only\n'
    if [ -n "$CONSOLE_SOCKET" ]; then
        printf 'serial endpoint: managed reconnectable Unix socket\n'
    fi
    exit 0
fi

NETWORK_ARGS=''
if [ "$NETWORK_MODE" = 'user' ]; then
    NETWORK_ARGS='-net nic,model=virtio -net user'
fi

if [ -n "$CONSOLE_SOCKET" ]; then
    [ -d "$CONSOLE_DIR" ] || {
        echo "error: managed console directory is missing: $CONSOLE_DIR" >&2
        exit 1
    }
    DISPLAY_ARGS="-display none -chardev socket,id=moos-serial,path=$CONSOLE_SOCKET,server=on,wait=off -serial chardev:moos-serial -monitor none"
elif [ "$SERIAL_ONLY" -eq 1 ]; then
    DISPLAY_ARGS='-nographic -serial stdio -monitor none'
else
    DISPLAY_ARGS='-device VGA -serial stdio -monitor none'
fi

run_direct() {
    # NETWORK_ARGS and DISPLAY_ARGS contain only fixed, internal option strings.
    # shellcheck disable=SC2086
    exec "$QEMU" \
        -M pc \
        -accel tcg \
        -m "$MEMORY" \
        -smp "$CPUS" \
        -kernel "$IMAGES_DIR/bzImage" \
        -append "rootwait root=/dev/vda console=tty1 console=ttyS0" \
        -drive "file=$IMAGES_DIR/rootfs.ext2,if=virtio,snapshot=on,format=raw" \
        $NETWORK_ARGS \
        $DISPLAY_ARGS \
        -L "$QEMU_SHARE_DIR" \
        "$@"
}

if [ "$ISOLATED" -eq 0 ]; then
    run_direct "$@"
fi

command -v bwrap >/dev/null 2>&1 || {
    echo 'error: bubblewrap (bwrap) is required for isolated QEMU' >&2
    echo '       install bubblewrap or use --direct as an explicit dev escape hatch' >&2
    exit 1
}

QEMU_BASENAME=${QEMU##*/}
SANDBOX_QEMU="/opt/moos-qemu/bin/$QEMU_BASENAME"
SANDBOX_LIB='/opt/moos-qemu/lib'
SANDBOX_SHARE='/opt/moos-qemu/share/qemu'

BWRAP_NETWORK_ARGS=''
if [ "$NETWORK_MODE" = 'user' ]; then
    BWRAP_NETWORK_ARGS='--share-net'
fi

# The sandbox intentionally binds only QEMU's executable/runtime files and the
# selected MOOS images. Managed launches additionally bind only their dedicated
# console directory. They never bind /home, /root, arbitrary /run paths, host
# devices, or arbitrary repository directories.
if [ -n "$CONSOLE_SOCKET" ]; then
    BWRAP_CONSOLE_ARGS="--dir /run --dir /run/moos-console --bind $CONSOLE_DIR /run/moos-console"
    QEMU_CONSOLE_ARGS='-display none -chardev socket,id=moos-serial,path=/run/moos-console/console.sock,server=on,wait=off -serial chardev:moos-serial -monitor none'
else
    BWRAP_CONSOLE_ARGS=''
    QEMU_CONSOLE_ARGS='-nographic -serial stdio -monitor none'
fi
# shellcheck disable=SC2086
exec bwrap \
    --unshare-all \
    $BWRAP_NETWORK_ARGS \
    --die-with-parent \
    --new-session \
    --clearenv \
    --uid 65534 \
    --gid 65534 \
    --dev /dev \
    --proc /proc \
    --size 134217728 \
    --tmpfs /tmp \
    --chmod 1777 /tmp \
    --dir /var \
    --size 134217728 \
    --tmpfs /var/tmp \
    --chmod 1777 /var/tmp \
    --dir /etc \
    --dir /lib \
    --dir /lib64 \
    --dir /usr \
    --dir /usr/lib \
    --dir /opt \
    --dir /opt/moos-qemu \
    --dir /opt/moos-qemu/bin \
    --dir /opt/moos-qemu/share \
    --dir /moos \
    $BWRAP_CONSOLE_ARGS \
    --ro-bind "$QEMU" "$SANDBOX_QEMU" \
    --ro-bind "$QEMU_LIB_DIR" "$SANDBOX_LIB" \
    --ro-bind "$QEMU_SHARE_DIR" "$SANDBOX_SHARE" \
    --ro-bind /lib /lib \
    --ro-bind /lib64 /lib64 \
    --ro-bind /usr/lib /usr/lib \
    --ro-bind-try /usr/lib64 /usr/lib64 \
    --ro-bind-try /etc/resolv.conf /etc/resolv.conf \
    --ro-bind "$IMAGES_DIR/bzImage" /moos/bzImage \
    --ro-bind "$IMAGES_DIR/rootfs.ext2" /moos/rootfs.ext2 \
    --setenv PATH /usr/bin:/bin \
    --setenv HOME /nonexistent \
    --setenv TMPDIR /tmp \
    --setenv LC_ALL C \
    --setenv QEMU_AUDIO_DRV none \
    "$SANDBOX_QEMU" \
    -M pc \
    -accel tcg \
    -m "$MEMORY" \
    -smp "$CPUS" \
    -kernel /moos/bzImage \
    -append "rootwait root=/dev/vda console=tty1 console=ttyS0" \
    -drive file=/moos/rootfs.ext2,if=virtio,snapshot=on,format=raw \
    $NETWORK_ARGS \
    $QEMU_CONSOLE_ARGS \
    -L "$SANDBOX_SHARE" \
    "$@"
