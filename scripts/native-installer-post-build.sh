#!/bin/sh
set -eu
target_dir=$1
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
MOOS_ROOT=$(CDPATH='' cd -- "$SCRIPT_DIR/.." && pwd)
command -v cargo >/dev/null 2>&1 || { echo 'error: pinned Rust toolchain required' >&2; exit 1; }
# The Rust target has the same triple as the x86_64 Linux build Host. Cargo also
# uses this linker for executable build scripts/proc macros, which must run on
# the Host. Linking those against the Buildroot sysroot can make them require a
# newer glibc than the build Host provides. Use the native Host linker, and
# fail closed unless its glibc ABI is supported by the installed rootfs.
host_linker=$(command -v cc) || { echo 'error: native Host C linker required' >&2; exit 1; }
host_machine=$("$host_linker" -dumpmachine)
case "$host_machine" in
    x86_64-linux-gnu|x86_64-pc-linux-gnu) ;;
    *) echo "error: installer build Host linker must target x86_64 Linux (got $host_machine)" >&2; exit 1 ;;
esac
host_glibc_line=$(getconf GNU_LIBC_VERSION 2>/dev/null || true)
host_glibc=$(printf '%s\n' "$host_glibc_line" | awk '$1 == "glibc" { print $2 }')
target_libc="$target_dir/lib/libc.so.6"
if [ -z "$host_glibc" ] || [ ! -f "$target_libc" ]; then
    echo 'error: cannot establish installer Host/target glibc compatibility' >&2
    exit 1
fi
target_glibc=$(LC_ALL=C strings "$target_libc" |
    sed -n 's/^GLIBC_\([0-9][0-9.]*\)$/\1/p' | sort -V | tail -n 1)
if [ -z "$target_glibc" ] ||
    [ "$(printf '%s\n%s\n' "$host_glibc" "$target_glibc" | sort -V | tail -n 1)" != "$target_glibc" ]; then
    echo "error: installer Host glibc $host_glibc is newer than target glibc $target_glibc" >&2
    exit 1
fi
cd "$MOOS_ROOT"
CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER=$host_linker
export CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER
# Buildroot's make flags may contain jobserver descriptors that are not
# inherited by this post-build process. Do not pass stale descriptors to Cargo;
# Cargo will create its own jobserver for build scripts.
unset MAKEFLAGS MFLAGS GNUMAKEFLAGS CARGO_MAKEFLAGS
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
