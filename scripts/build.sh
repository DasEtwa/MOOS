#!/bin/sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MOOS_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
BUILDROOT_DIR="$MOOS_ROOT/buildroot"
OUTPUT_DIR="$MOOS_ROOT/output"
HOST_TOOLS_DIR="$MOOS_ROOT/host-tools"
DEVELOPMENT_CONFIG="$MOOS_ROOT/configs/moos_qemu_x86_64_defconfig"
RELEASE_CONFIG="$MOOS_ROOT/configs/moos_qemu_x86_64_release_defconfig"
BUILDROOT_REPO='https://github.com/buildroot/buildroot.git'
BUILDROOT_RELEASE='2026.05.1'
BUILDROOT_REF='cb857ba4c87a93e5265a9e4a3f32071abf39e14a'
BUILDROOT_PATCH_DIR="$MOOS_ROOT/patches/buildroot"
JOBS=4
PROFILE='release'

usage() {
    cat <<'EOF'
Usage: scripts/build.sh [JOBS] [--profile development|release] [--jobs JOBS]

The default release profile disables password-based root login and verifies the
final rootfs image before returning success. The development profile must be
selected explicitly and keeps the insecure blank local root login.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --profile)
            [ "$#" -ge 2 ] || { usage >&2; exit 2; }
            PROFILE=$2
            shift 2
            ;;
        --release)
            PROFILE='release'
            shift
            ;;
        --jobs)
            [ "$#" -ge 2 ] || { usage >&2; exit 2; }
            JOBS=$2
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *[!0-9]*|'')
            echo "error: unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
        *)
            JOBS=$1
            shift
            ;;
    esac
done

case "$PROFILE" in
    development) CONFIG_FILE=$DEVELOPMENT_CONFIG ;;
    release) CONFIG_FILE=$RELEASE_CONFIG ;;
    *)
        echo 'error: --profile must be development or release' >&2
        exit 2
        ;;
esac
case "$JOBS" in
    ''|*[!0-9]*) echo 'error: jobs must be a positive integer' >&2; exit 2 ;;
esac
[ "$JOBS" -gt 0 ] || { echo 'error: jobs must be positive' >&2; exit 2; }

command -v git >/dev/null 2>&1 || {
    echo "error: git is required" >&2
    exit 1
}
command -v make >/dev/null 2>&1 || {
    echo "error: make is required" >&2
    exit 1
}

if [ ! -d "$BUILDROOT_DIR/.git" ]; then
    if [ -e "$BUILDROOT_DIR" ]; then
        echo "error: $BUILDROOT_DIR exists but is not a Git checkout" >&2
        exit 1
    fi
    git clone "$BUILDROOT_REPO" "$BUILDROOT_DIR"
fi

current_ref=$(git -C "$BUILDROOT_DIR" rev-parse HEAD 2>/dev/null || true)
if [ "$current_ref" != "$BUILDROOT_REF" ]; then
    if [ -n "$(git -C "$BUILDROOT_DIR" status --porcelain)" ]; then
        echo "error: refusing to switch a dirty Buildroot checkout" >&2
        exit 1
    fi
    git -C "$BUILDROOT_DIR" fetch --depth=1 origin "$BUILDROOT_REF"
    git -C "$BUILDROOT_DIR" checkout --detach "$BUILDROOT_REF"
fi

for buildroot_patch in "$BUILDROOT_PATCH_DIR"/*.patch; do
    [ -f "$buildroot_patch" ] || continue
    if git -C "$BUILDROOT_DIR" apply --reverse --check "$buildroot_patch" \
        >/dev/null 2>&1; then
        continue
    fi
    git -C "$BUILDROOT_DIR" apply --check "$buildroot_patch" || {
        echo "error: Buildroot patch cannot be applied: $buildroot_patch" >&2
        exit 1
    }
    git -C "$BUILDROOT_DIR" apply "$buildroot_patch"
done

mkdir -p "$HOST_TOOLS_DIR" "$OUTPUT_DIR"

# Some hosts provide uutils' install, while Buildroot requires GNU install.
if ! install --version 2>/dev/null | grep -q 'GNU coreutils'; then
    if command -v gnuinstall >/dev/null 2>&1; then
        ln -sf "$(command -v gnuinstall)" "$HOST_TOOLS_DIR/install"
    else
        echo "error: GNU install is required (install coreutils or provide gnuinstall)" >&2
        exit 1
    fi
fi

export PATH="$HOST_TOOLS_DIR:$PATH"

echo "Configuring MOOS $PROFILE profile from $CONFIG_FILE"
make -C "$BUILDROOT_DIR" \
    O="$OUTPUT_DIR" \
    BR2_DEFCONFIG="$CONFIG_FILE" \
    defconfig

echo "Building MOOS with $JOBS parallel jobs"
make -C "$BUILDROOT_DIR" O="$OUTPUT_DIR" -j"$JOBS"

if [ "$PROFILE" = 'release' ]; then
    "$MOOS_ROOT/scripts/validate-release-rootfs.py" \
        --debugfs "$OUTPUT_DIR/host/sbin/debugfs" \
        --rootfs-image "$OUTPUT_DIR/images/rootfs.ext2"
else
    echo 'warning: development image permits blank-password local root login' >&2
fi
