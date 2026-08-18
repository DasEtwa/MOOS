#!/bin/sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SOURCE_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
GATEWAY_USER='moos-gateway'
GATEWAY_GROUP='moos-gateway'
CONTROL_GROUP='moos-control'
LIBEXEC_ROOT='/usr/lib/moos'
STATE_ROOT='/var/lib/moos-gateway'
CONFIG_ROOT='/etc/moos'
UNIT_ROOT='/etc/systemd/system'
DOC_ROOT='/usr/share/doc/moos'
LISTEN_ADDRESS=''
PORT='7411'
DRY_RUN=0

usage() {
    cat <<'EOF'
Usage: scripts/setup-gateway.sh --tailscale-address ADDRESS [options]

Install the authenticated MOOS Gateway bound to one explicit Tailscale IP.
The local moosd control plane and Tailscale must already be installed.

Options:
  --tailscale-address IP  Required literal 100.64.0.0/10 or Tailscale IPv6 address.
  --port PORT             Gateway TCP port (default: 7411).
  --source-root DIR       MOOS repository to install from.
  --dry-run               Print the plan without modifying the host.
  -h, --help              Show this help.

The setup creates a locked, nologin gateway user and no sudo policy. Device
keys are created separately with moos-gateway-device and are never printed by
this installer.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --tailscale-address)
            [ "$#" -ge 2 ] || { echo 'error: address is required' >&2; exit 2; }
            LISTEN_ADDRESS="$2"
            shift 2
            ;;
        --port)
            [ "$#" -ge 2 ] || { echo 'error: port is required' >&2; exit 2; }
            PORT="$2"
            shift 2
            ;;
        --source-root)
            [ "$#" -ge 2 ] || { echo 'error: source root is required' >&2; exit 2; }
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

[ -n "$LISTEN_ADDRESS" ] || {
    echo 'error: --tailscale-address is required' >&2
    exit 2
}

SOURCE_ROOT=$(CDPATH= cd -- "$SOURCE_ROOT" && pwd)
for source_file in \
    GATEWAY.md \
    host/moos_gateway.py \
    host/moos_gateway_auth.py \
    host/moos_protocol.py \
    scripts/moos-gateway.py \
    scripts/moos-gateway-device.py \
    systemd/moos-gateway.service
do
    [ -f "$SOURCE_ROOT/$source_file" ] || {
        echo "error: source file is missing: $source_file" >&2
        exit 1
    }
done

python3 - "$LISTEN_ADDRESS" "$PORT" "$SOURCE_ROOT/host" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[3])
from moos_gateway import validate_tailscale_address
try:
    validate_tailscale_address(sys.argv[1])
    port = int(sys.argv[2])
    if not 1 <= port <= 65535:
        raise ValueError("port must be from 1 to 65535")
except ValueError as error:
    print(f"error: {error}", file=sys.stderr)
    raise SystemExit(2)
PY

if [ "$DRY_RUN" -eq 1 ]; then
    printf 'gateway identity: %s:%s (locked, nologin)\n' "$GATEWAY_USER" "$GATEWAY_GROUP"
    printf 'listen: %s:%s (Tailscale-only)\n' "$LISTEN_ADDRESS" "$PORT"
    printf 'device store: %s/devices.json (root:%s, mode 0640)\n' "$STATE_ROOT" "$GATEWAY_GROUP"
    printf 'moosd access: supplementary group %s, typed Protocol v1 only\n' "$CONTROL_GROUP"
    printf 'remote grants: status only, deny by default\n'
    printf 'sudo policy: none\n'
    printf 'host mutation: none (dry-run)\n'
    exit 0
fi

[ "$(id -u)" -eq 0 ] || {
    echo 'error: gateway setup must run as root' >&2
    echo '       use --dry-run as a normal user to inspect the plan' >&2
    exit 1
}
for command_name in awk chmod chown getent groupadd passwd stat useradd install systemctl; do
    command -v "$command_name" >/dev/null 2>&1 || {
        echo "error: required host command is missing: $command_name" >&2
        exit 1
    }
done
getent group "$CONTROL_GROUP" >/dev/null 2>&1 || {
    echo 'error: moos-control is missing; install the local control plane first' >&2
    exit 1
}

python3 - "$LISTEN_ADDRESS" "$SOURCE_ROOT/host" <<'PY'
import sys
sys.path.insert(0, sys.argv[2])
from moos_gateway import validate_tailscale_interface_address
try:
    validate_tailscale_interface_address(sys.argv[1])
except ValueError as error:
    print(f"error: {error}", file=sys.stderr)
    raise SystemExit(1)
PY

[ ! -L "$STATE_ROOT" ] && { [ ! -e "$STATE_ROOT" ] || [ -d "$STATE_ROOT" ]; } || {
    echo "error: unsafe gateway state directory: $STATE_ROOT" >&2
    exit 1
}
if [ -L "$STATE_ROOT/devices.json" ] || {
    [ -e "$STATE_ROOT/devices.json" ] && [ ! -f "$STATE_ROOT/devices.json" ]
}; then
    echo "error: unsafe gateway device store: $STATE_ROOT/devices.json" >&2
    exit 1
fi
if [ -e "$STATE_ROOT/devices.json" ] &&
   [ "$(stat -c %h "$STATE_ROOT/devices.json")" -ne 1 ]; then
    echo "error: gateway device store must not have hard links" >&2
    exit 1
fi

if ! getent group "$GATEWAY_GROUP" >/dev/null 2>&1; then
    groupadd --system "$GATEWAY_GROUP"
fi
if ! getent passwd "$GATEWAY_USER" >/dev/null 2>&1; then
    NOLOGIN_SHELL=$(command -v nologin || printf '/usr/sbin/nologin')
    useradd \
        --system \
        --gid "$GATEWAY_GROUP" \
        --groups "$CONTROL_GROUP" \
        --home-dir "$STATE_ROOT" \
        --no-create-home \
        --shell "$NOLOGIN_SHELL" \
        "$GATEWAY_USER"
else
    [ "$(id -u "$GATEWAY_USER")" -ne 0 ] || {
        echo 'error: existing moos-gateway account must not be root' >&2
        exit 1
    }
    [ "$(id -gn "$GATEWAY_USER")" = "$GATEWAY_GROUP" ] || {
        echo 'error: existing moos-gateway account has an unexpected primary group' >&2
        exit 1
    }
    case " $(id -Gn "$GATEWAY_USER") " in
        *" $CONTROL_GROUP "*) ;;
        *)
            echo 'error: existing moos-gateway account lacks moos-control access' >&2
            exit 1
            ;;
    esac
    for account_group in $(id -Gn "$GATEWAY_USER"); do
        case "$account_group" in
            "$GATEWAY_GROUP"|"$CONTROL_GROUP") ;;
            *)
                echo "error: existing moos-gateway account has unexpected group: $account_group" >&2
                exit 1
                ;;
        esac
    done
    GATEWAY_SHELL=$(getent passwd "$GATEWAY_USER" | awk -F: '{print $7}')
    case "$GATEWAY_SHELL" in
        */nologin) ;;
        *)
            echo 'error: existing moos-gateway account has a login shell' >&2
            exit 1
            ;;
    esac
    [ "$(passwd -S "$GATEWAY_USER" | awk '{print $2}')" = 'L' ] || {
        echo 'error: existing moos-gateway account is not locked' >&2
        exit 1
    }
fi

install -d -o root -g root -m 0755 "$LIBEXEC_ROOT" "$CONFIG_ROOT" "$DOC_ROOT"
install -d -o root -g "$GATEWAY_GROUP" -m 0750 "$STATE_ROOT"
UID_TEMP=$(mktemp "$CONFIG_ROOT/.gateway.uid.XXXXXX")
trap 'rm -f "$UID_TEMP"' EXIT HUP INT TERM
printf '%s\n' "$(id -u "$GATEWAY_USER")" > "$UID_TEMP"
chown root:root "$UID_TEMP"
chmod 0644 "$UID_TEMP"
mv -f "$UID_TEMP" "$CONFIG_ROOT/gateway.uid"
trap - EXIT HUP INT TERM
install -o root -g root -m 0644 \
    "$SOURCE_ROOT/host/moos_gateway.py" \
    "$SOURCE_ROOT/host/moos_gateway_auth.py" \
    "$SOURCE_ROOT/host/moos_protocol.py" \
    "$LIBEXEC_ROOT/"
install -o root -g root -m 0755 \
    "$SOURCE_ROOT/scripts/moos-gateway.py" "$LIBEXEC_ROOT/moos-gateway"
install -o root -g root -m 0755 \
    "$SOURCE_ROOT/scripts/moos-gateway-device.py" /usr/sbin/moos-gateway-device
install -o root -g root -m 0644 \
    "$SOURCE_ROOT/systemd/moos-gateway.service" "$UNIT_ROOT/moos-gateway.service"
install -o root -g root -m 0644 "$SOURCE_ROOT/GATEWAY.md" "$DOC_ROOT/GATEWAY.md"

if [ ! -e "$STATE_ROOT/devices.json" ]; then
    install -o root -g "$GATEWAY_GROUP" -m 0640 /dev/null "$STATE_ROOT/devices.json"
    printf '{"devices":[],"schemaVersion":1}\n' > "$STATE_ROOT/devices.json"
fi
chown root:"$GATEWAY_GROUP" "$STATE_ROOT/devices.json"
chmod 0640 "$STATE_ROOT/devices.json"

CONFIG_TEMP=$(mktemp "$CONFIG_ROOT/.gateway.conf.XXXXXX")
trap 'rm -f "$CONFIG_TEMP"' EXIT HUP INT TERM
{
    printf 'MOOS_GATEWAY_LISTEN_ADDRESS=%s\n' "$LISTEN_ADDRESS"
    printf 'MOOS_GATEWAY_PORT=%s\n' "$PORT"
} > "$CONFIG_TEMP"
chown root:root "$CONFIG_TEMP"
chmod 0644 "$CONFIG_TEMP"
mv -f "$CONFIG_TEMP" "$CONFIG_ROOT/gateway.conf"
trap - EXIT HUP INT TERM

systemctl daemon-reload
systemctl enable moos-gateway.service
systemctl restart moos-gateway.service

echo 'MOOS Gateway installed'
echo "  listen: $LISTEN_ADDRESS:$PORT"
echo '  pair a status-only device with:'
echo "    sudo moos-gateway-device add --name 'My iPhone' --allow status"
echo '  logs: journalctl -u moos-gateway.service'
