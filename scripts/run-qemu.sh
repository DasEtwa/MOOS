#!/bin/sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MOOS_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
OUTPUT_DIR="$MOOS_ROOT/output"
IMAGES_DIR="$OUTPUT_DIR/images"
QEMU="$OUTPUT_DIR/host/bin/qemu-system-x86_64"
SERIAL_ONLY=0

if [ "$#" -gt 0 ] && [ "$1" = "--serial-only" ]; then
    SERIAL_ONLY=1
    shift
fi

if [ ! -x "$QEMU" ]; then
    QEMU=$(command -v qemu-system-x86_64 || true)
fi

if [ -z "$QEMU" ]; then
    echo "error: no qemu-system-x86_64 found; run scripts/build.sh first" >&2
    exit 1
fi

if [ ! -f "$IMAGES_DIR/bzImage" ] || [ ! -f "$IMAGES_DIR/rootfs.ext2" ]; then
    echo "error: MOOS images are missing; run scripts/build.sh first" >&2
    exit 1
fi

if [ "$SERIAL_ONLY" -eq 1 ]; then
    exec "$QEMU" \
        -M pc \
        -kernel "$IMAGES_DIR/bzImage" \
        -drive "file=$IMAGES_DIR/rootfs.ext2,if=virtio,format=raw" \
        -append "rootwait root=/dev/vda console=tty1 console=ttyS0" \
        -net nic,model=virtio \
        -net user \
        -nographic "$@"
fi

exec "$QEMU" \
    -M pc \
    -kernel "$IMAGES_DIR/bzImage" \
    -drive "file=$IMAGES_DIR/rootfs.ext2,if=virtio,format=raw" \
    -append "rootwait root=/dev/vda console=tty1 console=ttyS0" \
    -net nic,model=virtio \
    -net user \
    -serial stdio "$@"
