#!/bin/sh
set -eu
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
MOOS_ROOT=$(CDPATH='' cd -- "$SCRIPT_DIR/.." && pwd)
SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-1790812800}
export SOURCE_DATE_EPOCH
for name in moos-native-installer-x86_64.img installer-boot.vfat installer-device.map boot.img installer-grub.cfg efi-part/EFI/BOOT/grub.cfg; do
    path="$BINARIES_DIR/$name"
    if [ -e "$path" ] || [ -L "$path" ]; then
        [ -f "$path" ] && [ ! -L "$path" ] && [ "$(stat -c %h "$path")" = 1 ] || {
            echo 'error: unsafe generated installer output' >&2; exit 1;
        }
    fi
done
for name in efi-part efi-part/EFI efi-part/EFI/BOOT; do
    [ -d "$BINARIES_DIR/$name" ] && [ ! -L "$BINARIES_DIR/$name" ] || {
        echo 'error: unsafe generated installer EFI directory' >&2; exit 1;
    }
done
"$MOOS_ROOT/scripts/native-installer-payload.py" --source "$MOOS_ROOT/output/native/images" \
    --output "$BINARIES_DIR/payload"
cp "$BUILD_DIR/grub2-2.12/build-i386-pc/grub-core/boot.img" "$BINARIES_DIR/boot.img"
cp "$MOOS_ROOT/system/native-installer/grub.cfg" "$BINARIES_DIR/installer-grub.cfg"
cp "$MOOS_ROOT/system/native-installer/grub.cfg" "$BINARIES_DIR/efi-part/EFI/BOOT/grub.cfg"
touch -d "@$SOURCE_DATE_EPOCH" "$BINARIES_DIR/bzImage" "$BINARIES_DIR/rootfs.cpio.gz" "$BINARIES_DIR/installer-grub.cfg"
find "$BINARIES_DIR/efi-part" "$BINARIES_DIR/payload" -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +
support/scripts/genimage.sh -c "$MOOS_ROOT/system/native-installer/genimage.cfg"
printf '(hd0) %s\n' "$BINARIES_DIR/moos-native-installer-x86_64.img" > "$BINARIES_DIR/installer-device.map"
"$HOST_DIR/sbin/grub-bios-setup" --directory="$BINARIES_DIR" --boot-image=boot.img \
    --core-image=grub.img --device-map="$BINARIES_DIR/installer-device.map" '(hd0)'
