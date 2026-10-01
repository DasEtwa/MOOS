#!/bin/sh
set -eu

[ "$#" -eq 1 ] || { echo 'error: expected Buildroot target directory' >&2; exit 2; }
target_dir=$1
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MOOS_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
# Replace Buildroot's unused generic BIOS placeholder too; the image contains
# one coherent Native boot configuration, without a device-name root target.
cp "$MOOS_ROOT/system/native/grub.cfg" "$target_dir/boot/grub/grub.cfg"
. "$target_dir/etc/moos-release"
. "$target_dir/etc/moos-platform"

# Visible before authentication; no shell or new control API is needed to
# identify the deployed image. Version is inherited from the shared MOOS source.
printf '%s Native %s\nBackend: %s / Architecture: %s / Release root: locked\n\n' \
    "$MOOS_NAME" "$MOOS_VERSION" "$MOOS_BACKEND" "$MOOS_ARCHITECTURE" \
    > "$target_dir/etc/issue"
