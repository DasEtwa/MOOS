#!/bin/sh

set -eu

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

The staged files are owned by moos-runtime. The source repository is never
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

for command_name in chown cp find install mktemp mv rm rmdir; do
    command -v "$command_name" >/dev/null 2>&1 || {
        echo "error: required host command is missing: $command_name" >&2
        exit 1
    }
done

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

QEMU_STAGE_DIR=''
INSTANCE_STAGE_DIR=''
cleanup() {
    if [ -n "$QEMU_STAGE_DIR" ] && [ -d "$QEMU_STAGE_DIR" ]; then
        rm -rf -- "$QEMU_STAGE_DIR"
    fi
    if [ -n "$INSTANCE_STAGE_DIR" ] && [ -d "$INSTANCE_STAGE_DIR" ]; then
        rm -rf -- "$INSTANCE_STAGE_DIR"
    fi
}
trap cleanup EXIT HUP INT TERM

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

    if [ -e "$RUNTIME_QEMU_ROOT" ]; then
        previous_qemu_root=$(mktemp -d "$STATE_ROOT/runtime/.qemu-host-previous.XXXXXX")
        rmdir "$previous_qemu_root"
        mv -- "$RUNTIME_QEMU_ROOT" "$previous_qemu_root"
    fi
    mv -- "$QEMU_STAGE_DIR" "$RUNTIME_QEMU_ROOT"
    QEMU_STAGE_DIR=''
fi

INSTANCE_STAGE_DIR=$(mktemp -d "$INSTANCE_ROOT/.$INSTANCE_ID.XXXXXX")
install -d -o root -g "$RUNTIME_GROUP" -m 0750 "$INSTANCE_STAGE_DIR"
install -o root -g "$RUNTIME_GROUP" -m 0440 \
    "$IMAGE_DIR/bzImage" "$INSTANCE_STAGE_DIR/bzImage"
install -o root -g "$RUNTIME_GROUP" -m 0440 \
    "$IMAGE_DIR/rootfs.ext2" "$INSTANCE_STAGE_DIR/rootfs.ext2"
mv -- "$INSTANCE_STAGE_DIR" "$INSTANCE_DIR"
INSTANCE_STAGE_DIR=''

echo "MOOS Instance staged: $INSTANCE_ID"
echo "  storage: $INSTANCE_DIR"
echo "  QEMU: $STAGED_QEMU"
