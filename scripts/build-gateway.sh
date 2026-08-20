#!/bin/sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SOURCE_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

[ "$(id -u)" -ne 0 ] || {
    echo 'error: build the Gateway as an unprivileged user, never as root' >&2
    exit 1
}
command -v cargo >/dev/null 2>&1 || {
    echo 'error: cargo is unavailable; install the pinned rust-toolchain.toml toolchain' >&2
    exit 1
}

cd "$SOURCE_ROOT"
cargo build --workspace --release --locked

for gateway_binary in moos-gateway moos-gateway-device; do
    release_path="$SOURCE_ROOT/target/release/$gateway_binary"
    [ -f "$release_path" ] && [ ! -L "$release_path" ] || {
        echo "error: Cargo did not produce $gateway_binary" >&2
        exit 1
    }
    temporary=$(mktemp "$SOURCE_ROOT/target/release/.${gateway_binary}.XXXXXX")
    trap 'rm -f "$temporary"' EXIT HUP INT TERM
    install -m 0755 "$release_path" "$temporary"
    mv -f "$temporary" "$release_path"
    trap - EXIT HUP INT TERM
done

echo 'MOOS Gateway release binaries ready:'
stat -c '  %n (%s bytes, mode %a, links %h)' \
    "$SOURCE_ROOT/target/release/moos-gateway" \
    "$SOURCE_ROOT/target/release/moos-gateway-device"
