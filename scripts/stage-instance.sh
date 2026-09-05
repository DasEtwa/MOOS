#!/bin/sh

set -eu

PATH='/usr/sbin:/usr/bin:/sbin:/bin'
export PATH
unset CDPATH ENV BASH_ENV PYTHONHOME PYTHONPATH
unset LD_PRELOAD LD_LIBRARY_PATH LD_AUDIT GCONV_PATH LOCPATH TMPDIR
LC_ALL=C
export LC_ALL

if [ "$(id -u)" -eq 0 ]; then
    TRUSTED_SELF=$(readlink -f -- "$0")
    case "$TRUSTED_SELF" in
        /usr/lib/moos/admin-releases/*/scripts/stage-instance.sh) ;;
        *)
            echo 'error: never run Instance staging as root from a checkout' >&2
            exit 1
            ;;
    esac
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
RUNTIME_USER='moos-runtime'
RUNTIME_GROUP='moos-runtime'
STATE_ROOT='/var/lib/moos'
INSTANCE_ROOT="$STATE_ROOT/instances"
RUNTIME_QEMU_ROOT="$STATE_ROOT/runtime/qemu-host"
INSTANCE_ID=''
IMAGE_DIR="$REPO_ROOT/output/images"
QEMU="$REPO_ROOT/output/host/bin/qemu-system-x86_64"
QEMU_EXPLICIT=0
REFRESH_RUNTIME=0
DRY_RUN=0

usage() {
    cat <<'EOF'
Usage: scripts/stage-instance.sh --id ID [options]

Copy only the MOOS kernel and root filesystem into private runtime storage.
The operation is root-only and refuses to replace an existing Instance ID.

Options:
  --id ID            Instance label, lowercase letters/digits/hyphens.
  --image-dir DIR   Source directory containing bzImage and rootfs.ext2.
  --qemu PATH       Source qemu-system-x86_64 binary.
  --refresh-runtime Refresh the staged QEMU runtime files explicitly.
  --dry-run         Print paths and permissions without modifying the host.
  -h, --help        Show this help.

The staged files are root-owned and read-only to moos-runtime. The source repository is never
mounted into the runtime account or copied wholesale into Instance storage.
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
        --image-dir)
            [ "$#" -ge 2 ] || {
                echo 'error: --image-dir requires a directory' >&2
                exit 2
            }
            IMAGE_DIR="$2"
            shift 2
            ;;
        --qemu)
            [ "$#" -ge 2 ] || {
                echo 'error: --qemu requires a binary path' >&2
                exit 2
            }
            QEMU="$2"
            QEMU_EXPLICIT=1
            shift 2
            ;;
        --refresh-runtime)
            REFRESH_RUNTIME=1
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
        *)
            echo "error: unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

case "$INSTANCE_ID" in
    ''|[!a-z]*|*[!a-z0-9-]*)
        echo 'error: --id must start with a lowercase letter and contain only a-z, 0-9, or -' >&2
        exit 2
        ;;
esac
id_length=$(expr length "$INSTANCE_ID")
[ "$id_length" -le 32 ] || {
    echo 'error: --id is limited to 32 characters' >&2
    exit 2
}

if [ ! -d "$IMAGE_DIR" ]; then
    echo "error: image directory does not exist: $IMAGE_DIR" >&2
    exit 1
fi
IMAGE_DIR=$(CDPATH= cd -- "$IMAGE_DIR" && pwd)
[ -f "$IMAGE_DIR/bzImage" ] && [ -f "$IMAGE_DIR/rootfs.ext2" ] || {
    echo 'error: image directory must contain bzImage and rootfs.ext2' >&2
    exit 1
}

if [ "$QEMU_EXPLICIT" -eq 0 ] && [ ! -x "$QEMU" ]; then
    QEMU=$(command -v qemu-system-x86_64 || true)
fi
[ -n "$QEMU" ] && [ -x "$QEMU" ] || {
    echo 'error: no executable qemu-system-x86_64 found' >&2
    exit 1
}
QEMU_BIN_DIR=$(CDPATH= cd -- "$(dirname -- "$QEMU")" && pwd)
QEMU_LIB_DIR=$(CDPATH= cd -- "$QEMU_BIN_DIR/../lib" && pwd)
QEMU_SHARE_DIR="$QEMU_BIN_DIR/../share/qemu"
if [ ! -d "$QEMU_SHARE_DIR" ] && [ -d /usr/share/qemu ]; then
    QEMU_SHARE_DIR='/usr/share/qemu'
fi
[ -d "$QEMU_LIB_DIR" ] && [ -d "$QEMU_SHARE_DIR" ] || {
    echo 'error: QEMU runtime libraries or firmware directory is missing' >&2
    exit 1
}

INSTANCE_DIR="$INSTANCE_ROOT/$INSTANCE_ID"
STAGED_QEMU="$RUNTIME_QEMU_ROOT/bin/qemu-system-x86_64"

if [ "$DRY_RUN" -eq 1 ]; then
    printf 'instance: %s\n' "$INSTANCE_ID"
    printf 'source images: %s (read-only source)\n' "$IMAGE_DIR"
    printf 'instance storage: %s (root:%s, mode 0750)\n' "$INSTANCE_DIR" "$RUNTIME_GROUP"
    printf 'staged kernel: %s/bzImage (root:%s, mode 0440)\n' "$INSTANCE_DIR" "$RUNTIME_GROUP"
    printf 'staged rootfs: %s/rootfs.ext2 (root:%s, mode 0440)\n' "$INSTANCE_DIR" "$RUNTIME_GROUP"
    printf 'staged QEMU: %s\n' "$STAGED_QEMU"
    if [ "$REFRESH_RUNTIME" -eq 1 ]; then
        printf 'refresh QEMU runtime: yes\n'
    else
        printf 'refresh QEMU runtime: no\n'
    fi
    printf 'host mutation: none (dry-run)\n'
    exit 0
fi

[ "$(id -u)" -eq 0 ] || {
    echo 'error: staging runtime files must run as root' >&2
    exit 1
}
id "$RUNTIME_USER" >/dev/null 2>&1 || {
    echo "error: runtime user does not exist: $RUNTIME_USER" >&2
    echo '       run setup-runtime-user.sh first' >&2
    exit 1
}

for command_name in chown cp find flock install mktemp mv python3 readlink rm rmdir stat sync systemd-run; do
    command -v "$command_name" >/dev/null 2>&1 || {
        echo "error: required host command is missing: $command_name" >&2
        exit 1
    }
done
AUTHENTICATED_SCRIPT=$(readlink -f -- "$0")
case "$AUTHENTICATED_SCRIPT" in
    /usr/lib/moos/admin-releases/*/scripts/stage-instance.sh) ;;
    *)
        echo 'error: run staging only from an authenticated administrator release' >&2
        exit 1
        ;;
esac

# Serialize the shared runtime tree across all Instance IDs.
[ -d "$STATE_ROOT/runtime" ] && [ ! -L "$STATE_ROOT/runtime" ] || {
    echo 'error: trusted runtime directory is missing' >&2; exit 1;
}
[ "$(stat -c %u:%a "$STATE_ROOT/runtime")" = '0:750' ] || {
    echo 'error: unsafe runtime directory ownership or mode' >&2; exit 1;
}
RUNTIME_LOCK="$STATE_ROOT/runtime/.stage.lock"
if [ -e "$RUNTIME_LOCK" ] || [ -L "$RUNTIME_LOCK" ]; then
    [ ! -L "$RUNTIME_LOCK" ] && [ -f "$RUNTIME_LOCK" ] &&
    [ "$(stat -c %u:%g:%a:%h "$RUNTIME_LOCK")" = '0:0:600:1' ] || {
        echo 'error: unsafe runtime staging lock' >&2; exit 1;
    }
fi
(umask 077; : >> "$RUNTIME_LOCK")
exec 9>>"$RUNTIME_LOCK"
flock -w 30 9 || { echo 'error: another runtime staging is active' >&2; exit 1; }

[ ! -e "$INSTANCE_DIR" ] || {
    echo "error: Instance already exists: $INSTANCE_ID" >&2
    echo '       refusing an implicit replacement; use a new ID or an explicit migration procedure' >&2
    exit 1
}

[ -d "$INSTANCE_ROOT" ] || {
    echo "error: Instance storage is missing: $INSTANCE_ROOT" >&2
    echo '       run setup-runtime-user.sh first' >&2
    exit 1
}

# Linux directory exchange keeps the active runtime present even if the
# installer is killed. This inline code belongs to the authenticated release;
# isolated Python must never import executable code from the source checkout.
exchange_runtime() {
    python3 -I - "$1" "$2" <<'PYTHON'
import ctypes
import os
import sys

libc = ctypes.CDLL(None, use_errno=True)
rename = libc.renameat2
rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
rename.restype = ctypes.c_int
if rename(-100, os.fsencode(sys.argv[1]), -100, os.fsencode(sys.argv[2]), 2) != 0:
    raise OSError(ctypes.get_errno(), "atomic runtime exchange failed")
fd = os.open(os.path.dirname(sys.argv[2]), os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(fd)
finally:
    os.close(fd)
PYTHON
}

QEMU_STAGE_DIR=''
INSTANCE_STAGE_DIR=''
PREVIOUS_QEMU_ROOT=''
QEMU_ORIGINAL_ID=''
QEMU_ACTIVATED=0
STAGING_COMMITTED=0
cleanup() {
    trap '' HUP INT TERM
    if [ "$STAGING_COMMITTED" -eq 0 ]; then
        if [ -n "$PREVIOUS_QEMU_ROOT" ] && [ -d "$PREVIOUS_QEMU_ROOT" ]; then
            # Inspect identity instead of relying on a flag set after the
            # syscall: a signal can arrive immediately after the exchange.
            if [ -n "$QEMU_ORIGINAL_ID" ] &&
               [ "$(stat -c %d:%i "$RUNTIME_QEMU_ROOT")" != "$QEMU_ORIGINAL_ID" ]; then
                exchange_runtime "$PREVIOUS_QEMU_ROOT" "$RUNTIME_QEMU_ROOT" || {
                    echo "error: rollback failed; preserved runtime at $PREVIOUS_QEMU_ROOT" >&2
                    return
                }
            fi
            rm -rf -- "$PREVIOUS_QEMU_ROOT"
        elif [ "$QEMU_ACTIVATED" -eq 1 ]; then
            rm -rf -- "$RUNTIME_QEMU_ROOT"
        fi
    fi
    if [ -n "$QEMU_STAGE_DIR" ] && [ -d "$QEMU_STAGE_DIR" ]; then
        rm -rf -- "$QEMU_STAGE_DIR"
    fi
    if [ -n "$INSTANCE_STAGE_DIR" ] && [ -d "$INSTANCE_STAGE_DIR" ]; then
        rm -rf -- "$INSTANCE_STAGE_DIR"
    fi
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

if [ ! -x "$STAGED_QEMU" ] || [ "$REFRESH_RUNTIME" -eq 1 ]; then
    install -d -o root -g "$RUNTIME_GROUP" -m 0750 "$STATE_ROOT/runtime"
    QEMU_STAGE_DIR=$(mktemp -d "$STATE_ROOT/runtime/.qemu-host.XXXXXX")
    install -d -o root -g "$RUNTIME_GROUP" -m 0750 \
        "$QEMU_STAGE_DIR/bin" "$QEMU_STAGE_DIR/lib" "$QEMU_STAGE_DIR/share/qemu"
    install -o root -g "$RUNTIME_GROUP" -m 0750 \
        "$QEMU" "$QEMU_STAGE_DIR/bin/qemu-system-x86_64"
    cp -a "$QEMU_LIB_DIR/." "$QEMU_STAGE_DIR/lib/"
    cp -a "$QEMU_SHARE_DIR/." "$QEMU_STAGE_DIR/share/qemu/"
    chown -R root:"$RUNTIME_GROUP" "$QEMU_STAGE_DIR"
    find "$QEMU_STAGE_DIR" -type d -exec chmod 0750 {} +
    find "$QEMU_STAGE_DIR" -type f -exec chmod 0640 {} +
    chmod 0750 "$QEMU_STAGE_DIR/bin/qemu-system-x86_64"

    # Validate only the root-owned copy under the final unprivileged identity.
    systemd-run --quiet --wait --collect --pipe \
        --property=User="$RUNTIME_USER" --property=Group="$RUNTIME_GROUP" \
        --property=NoNewPrivileges=yes --property=PrivateNetwork=yes \
        --property=PrivateDevices=yes --property=ProtectHome=yes \
        --property=ProtectSystem=strict --property=CapabilityBoundingSet= \
        --property=MemoryMax=256M --property=TasksMax=32 --property=RuntimeMaxSec=30 \
        "$QEMU_STAGE_DIR/bin/qemu-system-x86_64" --version

fi

INSTANCE_STAGE_DIR=$(mktemp -d "$INSTANCE_ROOT/.$INSTANCE_ID.XXXXXX")
install -d -o root -g "$RUNTIME_GROUP" -m 0750 "$INSTANCE_STAGE_DIR"
install -o root -g "$RUNTIME_GROUP" -m 0440 \
    "$IMAGE_DIR/bzImage" "$INSTANCE_STAGE_DIR/bzImage"
install -o root -g "$RUNTIME_GROUP" -m 0440 \
    "$IMAGE_DIR/rootfs.ext2" "$INSTANCE_STAGE_DIR/rootfs.ext2"
if [ -n "$QEMU_STAGE_DIR" ]; then
    if [ -e "$RUNTIME_QEMU_ROOT" ]; then
        QEMU_ORIGINAL_ID=$(stat -c %d:%i "$RUNTIME_QEMU_ROOT")
        PREVIOUS_QEMU_ROOT=$(mktemp -d "$STATE_ROOT/runtime/.qemu-host-previous.XXXXXX")
        rmdir "$PREVIOUS_QEMU_ROOT"
        mv -- "$QEMU_STAGE_DIR" "$PREVIOUS_QEMU_ROOT"
        QEMU_STAGE_DIR=''
        sync -f "$PREVIOUS_QEMU_ROOT"
        exchange_runtime "$PREVIOUS_QEMU_ROOT" "$RUNTIME_QEMU_ROOT"
    else
        QEMU_ACTIVATED=1
        mv -- "$QEMU_STAGE_DIR" "$RUNTIME_QEMU_ROOT"
        QEMU_STAGE_DIR=''
    fi
    QEMU_ACTIVATED=1
fi
mv -- "$INSTANCE_STAGE_DIR" "$INSTANCE_DIR"
INSTANCE_STAGE_DIR=''
STAGING_COMMITTED=1
# Keep one rollback tree; prune only after both activations succeeded.
if [ "$QEMU_ACTIVATED" -eq 1 ]; then
    for old_runtime in "$STATE_ROOT"/runtime/.qemu-host-previous.*; do
        [ -d "$old_runtime" ] && [ ! -L "$old_runtime" ] || continue
        [ "$old_runtime" = "$PREVIOUS_QEMU_ROOT" ] || rm -rf -- "$old_runtime"
    done
fi

echo "MOOS Instance staged: $INSTANCE_ID"
echo "  storage: $INSTANCE_DIR"
echo "  QEMU: $STAGED_QEMU"
