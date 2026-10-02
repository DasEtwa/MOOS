#!/bin/sh
set -eu
target_dir=$1
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
MOOS_ROOT=$(CDPATH='' cd -- "$SCRIPT_DIR/.." && pwd)
command -v cargo >/dev/null 2>&1 || { echo 'error: pinned Rust toolchain required' >&2; exit 1; }
# Cargo uses the repository rust-toolchain and lock; link against this Buildroot
# target's sysroot, never against the build machine's glibc.
cd "$MOOS_ROOT"
CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER="$HOST_DIR/bin/x86_64-buildroot-linux-gnu-gcc"
export CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER
cargo build --locked --release --target x86_64-unknown-linux-gnu \
    --target-dir "$BUILD_DIR/moos-installer-cargo" -p moos-native-installer
cp "$BUILD_DIR/moos-installer-cargo/x86_64-unknown-linux-gnu/release/moos-native-installer" "$target_dir/usr/bin/moos-native-installer"
cp "$MOOS_ROOT/system/native-installer/grub.cfg" "$target_dir/boot/grub/grub.cfg"
# No login/getty/shell entry point. Tiny initramfs is writable ephemeral RAM;
# payload is mounted read-only from hardware-readonly installer media by Rust.
mkdir -p "$target_dir/run/moos-installer/data"
chmod 700 "$target_dir/run/moos-installer"
if grep -q '^root:[!*]' "$target_dir/etc/shadow"; then :; else
    echo 'error: installer root must be locked' >&2; exit 1
fi
