#!/bin/sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SOURCE_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
CONTROL_GROUP='moos-control'
LIBEXEC_ROOT='/usr/lib/moos'
UNIT_ROOT='/etc/systemd/system'
DOC_ROOT='/usr/share/doc/moos'
DRY_RUN=0

usage() {
    cat <<'EOF'
Usage: scripts/setup-control-plane.sh [options]

Install the local socket-activated moosd control plane. The daemon retains the
root identity required to ask systemd for the fixed managed Personal unit;
normal clients receive only the narrow socket API through moos-control.

Options:
  --source-root DIR  MOOS repository to install from.
  --dry-run          Print the planned changes without modifying the host.
  -h, --help         Show this help.

This script creates no sudo policy and does not add users to moos-control.
An administrator must explicitly grant a trusted local user group membership.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --source-root)
            [ "$#" -ge 2 ] || {
                echo 'error: --source-root requires a directory' >&2
                exit 2
            }
            SOURCE_ROOT="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "error: unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

SOURCE_ROOT=$(CDPATH= cd -- "$SOURCE_ROOT" && pwd)
for source_file in \
    host/moos_protocol.py \
    host/moos_runtime.py \
    host/moosd.py \
    scripts/moos \
    scripts/moosd.py \
    scripts/run-instance.sh \
    systemd/moosd.service \
    systemd/moosd.socket \
    HOST_GUEST_ISOLATION.md
do
    [ -f "$SOURCE_ROOT/$source_file" ] || {
        echo "error: source file is missing: $source_file" >&2
        exit 1
    }
done

if [ "$DRY_RUN" -eq 1 ]; then
    printf 'control group: %s (no automatic members)\n' "$CONTROL_GROUP"
    printf 'daemon identity: root:%s (fixed typed operations only)\n' "$CONTROL_GROUP"
    printf 'socket: /run/moos/moosd.sock (root:%s, mode 0660)\n' "$CONTROL_GROUP"
    printf 'runtime: systemd socket activation, restart on failure, journald logging\n'
    printf 'installed code: %s (root-owned, read-only)\n' "$LIBEXEC_ROOT"
    printf 'sudo policy: none\n'
    printf 'host mutation: none (dry-run)\n'
    exit 0
fi

[ "$(id -u)" -eq 0 ] || {
    echo 'error: control-plane setup must run as root' >&2
    echo '       use --dry-run as a normal user to inspect the plan' >&2
    exit 1
}
for command_name in getent groupadd install systemctl; do
    command -v "$command_name" >/dev/null 2>&1 || {
        echo "error: required host command is missing: $command_name" >&2
        exit 1
    }
done
getent passwd moos-runtime >/dev/null 2>&1 || {
    echo 'error: moos-runtime is not installed; run setup-runtime-user.sh first' >&2
    exit 1
}

if ! getent group "$CONTROL_GROUP" >/dev/null 2>&1; then
    groupadd --system "$CONTROL_GROUP"
fi

install -d -o root -g root -m 0755 "$LIBEXEC_ROOT" "$DOC_ROOT"
install -o root -g root -m 0644 \
    "$SOURCE_ROOT/host/moos_protocol.py" \
    "$SOURCE_ROOT/host/moos_runtime.py" \
    "$SOURCE_ROOT/host/moosd.py" \
    "$LIBEXEC_ROOT/"
install -o root -g root -m 0755 \
    "$SOURCE_ROOT/scripts/moosd.py" "$LIBEXEC_ROOT/moosd"
install -o root -g root -m 0755 \
    "$SOURCE_ROOT/scripts/run-instance.sh" "$LIBEXEC_ROOT/run-instance.sh"
install -o root -g root -m 0755 "$SOURCE_ROOT/scripts/moos" /usr/bin/moos
install -o root -g root -m 0644 \
    "$SOURCE_ROOT/systemd/moosd.service" \
    "$SOURCE_ROOT/systemd/moosd.socket" \
    "$UNIT_ROOT/"
install -o root -g root -m 0644 \
    "$SOURCE_ROOT/HOST_GUEST_ISOLATION.md" "$DOC_ROOT/HOST_GUEST_ISOLATION.md"

systemctl daemon-reload
systemctl enable --now moosd.socket

echo 'MOOS control plane installed'
echo "  socket group: $CONTROL_GROUP"
echo '  grant access explicitly with: usermod -aG moos-control USER'
echo '  logs: journalctl -u moosd.service'
