#!/bin/sh
set -eu
SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-1790812800}
TZ=UTC
export SOURCE_DATE_EPOCH TZ
E2FSPROGS_FAKE_TIME=$SOURCE_DATE_EPOCH
export E2FSPROGS_FAKE_TIME

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MOOS_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

# Build output is unprivileged, but pre-existing links/devices must never turn
# a generated-file operation into a write to another builder-owned object.
safe_output() {
    if [ -e "$1" ] || [ -L "$1" ]; then
        [ -f "$1" ] && [ ! -L "$1" ] && [ "$(stat -c %h "$1")" = 1 ] || {
            echo "error: unsafe generated Native output: $1" >&2
            exit 1
        }
    fi
}
safe_output "$BINARIES_DIR/native-data.ext4"
safe_output "$BINARIES_DIR/native-device.map"
safe_output "$BINARIES_DIR/moos-native-x86_64.img"

# GRUB's target tools are not installed into the Native rootfs. Only the
# generated bootloader payloads enter BOOT and the BIOS_GRUB partition.
cp "$BUILD_DIR/grub2-2.12/build-i386-pc/grub-core/boot.img" "$BINARIES_DIR/boot.img"
cp "$MOOS_ROOT/system/native/grub.cfg" "$BINARIES_DIR/native-grub.cfg"
cp "$MOOS_ROOT/system/native/grub.cfg" "$BINARIES_DIR/efi-part/EFI/BOOT/grub.cfg"
touch -d "@$SOURCE_DATE_EPOCH" "$BINARIES_DIR/bzImage" "$BINARIES_DIR/native-grub.cfg"
find "$BINARIES_DIR/efi-part" -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +

"$MOOS_ROOT/scripts/validate-release-rootfs.py" \
    --debugfs "$HOST_DIR/sbin/debugfs" --rootfs-image "$BINARIES_DIR/rootfs.ext2"

# A distribution image has a known empty schema, never an installation ID.
# Only build-time tools format this generated regular file. Boot never does.
data_image=$(mktemp "$BINARIES_DIR/.native-data.XXXXXX")
trap 'rm -f "$data_image"' 0
truncate -s 0 "$data_image"
truncate -s 32M "$data_image"
"$HOST_DIR/sbin/mkfs.ext4" -F -q -b 1024 -L MOOS_DATA \
    -U 4d4f4f53-0000-4000-8000-000000000005 -O '^64bit' \
    -E root_owner=0:0,hash_seed=4d4f4f53-0000-4000-8000-000000000006,lazy_itable_init=0,lazy_journal_init=0 \
    "$data_image"
"$HOST_DIR/sbin/debugfs" -w -R "write $MOOS_ROOT/system/native/state-version /state-version" "$data_image"
"$HOST_DIR/sbin/debugfs" -w -R 'set_inode_field /state-version mode 0100600' "$data_image"
"$HOST_DIR/sbin/debugfs" -w -R 'set_inode_field / mode 040700' "$data_image"
mv -f "$data_image" "$BINARIES_DIR/native-data.ext4"
trap - 0

# Buildroot's genimage helper operates only on regular generated files, as the
# invoking unprivileged builder. There is no block-device/install operation.
support/scripts/genimage.sh -c "$MOOS_ROOT/system/native/genimage.cfg"

# Use GRUB's normal GPT BIOS embedding algorithm, without loop devices or root.
# The device map maps only this generated regular image to GRUB's disk name.
disk_image="$BINARIES_DIR/moos-native-x86_64.img"
[ -f "$disk_image" ] && [ ! -L "$disk_image" ] || exit 1
device_map="$BINARIES_DIR/native-device.map"
printf '(hd0) %s\n' "$disk_image" > "$device_map"
"$HOST_DIR/sbin/grub-bios-setup" --directory="$BINARIES_DIR" \
    --boot-image=boot.img --core-image=grub.img --device-map="$device_map" \
    '(hd0)'
