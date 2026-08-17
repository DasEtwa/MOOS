#!/bin/sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MOOS_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
BUILDROOT_DIR="$MOOS_ROOT/buildroot"
OUTPUT_DIR="$MOOS_ROOT/output"
HOST_TOOLS_DIR="$MOOS_ROOT/host-tools"
CONFIG_FILE="$MOOS_ROOT/configs/moos_qemu_x86_64_defconfig"
BUILDROOT_REPO='https://github.com/buildroot/buildroot.git'
BUILDROOT_REF='9ac19958f25a58df65b991ec1d7fa80b34f19eb0'
JOBS=4

if [ "$#" -gt 0 ]; then
    JOBS="$1"
fi

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

echo "Configuring Buildroot from $CONFIG_FILE"
make -C "$BUILDROOT_DIR" \
    O="$OUTPUT_DIR" \
    BR2_DEFCONFIG="$CONFIG_FILE" \
    defconfig

echo "Building MOOS with $JOBS parallel jobs"
make -C "$BUILDROOT_DIR" O="$OUTPUT_DIR" -j"$JOBS"
