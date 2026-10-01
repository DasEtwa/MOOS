#!/bin/sh
set -eu
SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-1790812800}
TZ=UTC
export SOURCE_DATE_EPOCH TZ

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MOOS_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

# GRUB's target tools are not installed into the Native rootfs. Only the
# generated bootloader payloads enter BOOT and the MBR embedding area.
cp "$BUILD_DIR/grub2-2.12/build-i386-pc/grub-core/boot.img" "$BINARIES_DIR/boot.img"
cp "$MOOS_ROOT/system/native/grub.cfg" "$BINARIES_DIR/native-grub.cfg"
cp "$MOOS_ROOT/system/native/grub.cfg" "$BINARIES_DIR/efi-part/EFI/BOOT/grub.cfg"
touch -d "@$SOURCE_DATE_EPOCH" "$BINARIES_DIR/bzImage" "$BINARIES_DIR/native-grub.cfg"
find "$BINARIES_DIR/efi-part" -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +

"$MOOS_ROOT/scripts/validate-release-rootfs.py" \
    --debugfs "$HOST_DIR/sbin/debugfs" --rootfs-image "$BINARIES_DIR/rootfs.ext2"

# Buildroot's genimage helper operates only on regular generated files, as the
# invoking unprivileged builder. There is no block-device/install operation.
support/scripts/genimage.sh -c "$MOOS_ROOT/system/native/genimage.cfg"
