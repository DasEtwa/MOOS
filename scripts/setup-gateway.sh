#!/bin/sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SOURCE_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
GATEWAY_USER='moos-gateway'
GATEWAY_GROUP='moos-gateway'
CONTROL_GROUP='moos-control'
LIBEXEC_ROOT='/usr/lib/moos'
ADMIN_ROOT='/usr/sbin'
STATE_ROOT='/var/lib/moos-gateway'
CONFIG_ROOT='/etc/moos'
UNIT_ROOT='/etc/systemd/system'
DOC_ROOT='/usr/share/doc/moos'
LISTEN_ADDRESS=''
PORT='7411'
BINARY_ROOT=''
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
  --binary-dir DIR        Prebuilt release binaries (default: target/release).
  --dry-run               Validate artifacts and print the non-mutating plan.
  -h, --help              Show this help.

Build release binaries as an unprivileged user with scripts/build-gateway.sh.
The installer never runs Cargo or downloads dependencies. Device keys are
created separately with moos-gateway-device and are never printed here.
EOF
}

fail() {
    echo "error: $*" >&2
    exit 1
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
        --binary-dir)
            [ "$#" -ge 2 ] || { echo 'error: binary directory is required' >&2; exit 2; }
            BINARY_ROOT="$2"
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

[ -n "$LISTEN_ADDRESS" ] || { echo 'error: --tailscale-address is required' >&2; exit 2; }
SOURCE_ROOT=$(CDPATH= cd -- "$SOURCE_ROOT" && pwd)
[ -n "$BINARY_ROOT" ] || BINARY_ROOT="$SOURCE_ROOT/target/release"
[ -d "$BINARY_ROOT" ] || fail "binary directory is missing: $BINARY_ROOT; run scripts/build-gateway.sh first"
BINARY_ROOT=$(CDPATH= cd -- "$BINARY_ROOT" && pwd)
command -v stat >/dev/null 2>&1 || fail 'required host command is missing: stat'

for source_file in GATEWAY.md systemd/moos-gateway.service; do
    [ -f "$SOURCE_ROOT/$source_file" ] || fail "source file is missing: $source_file"
done
for gateway_binary in moos-gateway moos-gateway-device; do
    binary_path="$BINARY_ROOT/$gateway_binary"
    [ -f "$binary_path" ] && [ ! -L "$binary_path" ] && [ -x "$binary_path" ] || \
        fail "safe prebuilt release binary is missing: $binary_path"
    [ "$(stat -c %a "$binary_path")" = '755' ] && \
    [ "$(stat -c %h "$binary_path")" -eq 1 ] || \
        fail "release binary must have mode 0755 and one link: $binary_path"
done

GATEWAY_VERSION_LINE=$("$BINARY_ROOT/moos-gateway" --version) || fail 'moos-gateway --version failed'
DEVICE_VERSION_LINE=$("$BINARY_ROOT/moos-gateway-device" --version) || fail 'moos-gateway-device --version failed'
case "$GATEWAY_VERSION_LINE" in
    'moos-gateway '[0-9]* ) GATEWAY_VERSION=${GATEWAY_VERSION_LINE#moos-gateway } ;;
    *) fail 'invalid moos-gateway version output' ;;
esac
[ "$DEVICE_VERSION_LINE" = "moos-gateway-device $GATEWAY_VERSION" ] || \
    fail 'Gateway release binaries have mismatched versions'
case "$GATEWAY_VERSION" in
    *[!0-9A-Za-z.+-]*|'') fail 'invalid Gateway release version' ;;
esac

"$BINARY_ROOT/moos-gateway" validate-address \
    --listen-address "$LISTEN_ADDRESS" --port "$PORT"

if [ "$DRY_RUN" -eq 1 ]; then
    printf 'gateway identity: %s:%s (locked, nologin)\n' "$GATEWAY_USER" "$GATEWAY_GROUP"
    printf 'listen: %s:%s (Tailscale-only)\n' "$LISTEN_ADDRESS" "$PORT"
    printf 'device store: %s/devices.json (root:%s, mode 0640)\n' "$STATE_ROOT" "$GATEWAY_GROUP"
    printf 'moosd access: supplementary group %s, typed Protocol v1 only\n' "$CONTROL_GROUP"
    printf 'remote grants: status only, deny by default\n'
    printf 'runtime: Rust release %s, built without root\n' "$GATEWAY_VERSION"
    printf 'sudo policy: none\n'
    printf 'host mutation: none (dry-run)\n'
    exit 0
fi

[ "$(id -u)" -eq 0 ] || fail 'gateway setup must run as root; use --dry-run to inspect the plan'
for command_name in awk chmod chown getent groupadd install mktemp mv passwd rm stat systemctl useradd; do
    command -v "$command_name" >/dev/null 2>&1 || fail "required host command is missing: $command_name"
done
getent group "$CONTROL_GROUP" >/dev/null 2>&1 || \
    fail 'moos-control is missing; install the local control plane first'

"$BINARY_ROOT/moos-gateway" validate-interface \
    --listen-address "$LISTEN_ADDRESS" --port "$PORT"

[ ! -L "$STATE_ROOT" ] && { [ ! -e "$STATE_ROOT" ] || [ -d "$STATE_ROOT" ]; } || \
    fail "unsafe gateway state directory: $STATE_ROOT"
if [ -L "$STATE_ROOT/devices.json" ] || {
    [ -e "$STATE_ROOT/devices.json" ] && [ ! -f "$STATE_ROOT/devices.json" ]
}; then
    fail "unsafe gateway device store: $STATE_ROOT/devices.json"
fi
if [ -e "$STATE_ROOT/devices.json" ] && [ "$(stat -c %h "$STATE_ROOT/devices.json")" -ne 1 ]; then
    fail 'gateway device store must not have hard links'
fi

if ! getent group "$GATEWAY_GROUP" >/dev/null 2>&1; then
    groupadd --system "$GATEWAY_GROUP"
fi
if ! getent passwd "$GATEWAY_USER" >/dev/null 2>&1; then
    NOLOGIN_SHELL=$(command -v nologin || printf '/usr/sbin/nologin')
    useradd --system --gid "$GATEWAY_GROUP" --groups "$CONTROL_GROUP" \
        --home-dir "$STATE_ROOT" --no-create-home --shell "$NOLOGIN_SHELL" "$GATEWAY_USER"
else
    [ "$(id -u "$GATEWAY_USER")" -ne 0 ] || fail 'existing moos-gateway account must not be root'
    [ "$(id -gn "$GATEWAY_USER")" = "$GATEWAY_GROUP" ] || \
        fail 'existing moos-gateway account has an unexpected primary group'
    case " $(id -Gn "$GATEWAY_USER") " in *" $CONTROL_GROUP "*) ;; *) fail 'existing moos-gateway account lacks moos-control access' ;; esac
    for account_group in $(id -Gn "$GATEWAY_USER"); do
        case "$account_group" in "$GATEWAY_GROUP"|"$CONTROL_GROUP") ;; *) fail "existing moos-gateway account has unexpected group: $account_group" ;; esac
    done
    GATEWAY_SHELL=$(getent passwd "$GATEWAY_USER" | awk -F: '{print $7}')
    case "$GATEWAY_SHELL" in */nologin) ;; *) fail 'existing moos-gateway account has a login shell' ;; esac
    [ "$(passwd -S "$GATEWAY_USER" | awk '{print $2}')" = 'L' ] || \
        fail 'existing moos-gateway account is not locked'
fi

install -d -o root -g root -m 0755 "$LIBEXEC_ROOT" "$CONFIG_ROOT" "$DOC_ROOT"
if [ ! -e "$STATE_ROOT" ]; then
    install -d -o root -g "$GATEWAY_GROUP" -m 0750 "$STATE_ROOT"
else
    [ "$(stat -c %a "$STATE_ROOT")" = '750' ] && \
    [ "$(stat -c %U:%G "$STATE_ROOT")" = "root:$GATEWAY_GROUP" ] || \
        fail 'existing gateway state directory has unsafe ownership or mode'
fi

UID_TEMP=$(mktemp "$CONFIG_ROOT/.gateway.uid.XXXXXX")
trap 'rm -f "$UID_TEMP"' EXIT HUP INT TERM
printf '%s\n' "$(id -u "$GATEWAY_USER")" > "$UID_TEMP"
chown root:root "$UID_TEMP"
chmod 0644 "$UID_TEMP"
mv -f "$UID_TEMP" "$CONFIG_ROOT/gateway.uid"
trap - EXIT HUP INT TERM

if [ ! -e "$STATE_ROOT/devices.json" ]; then
    install -o root -g "$GATEWAY_GROUP" -m 0640 /dev/null "$STATE_ROOT/devices.json"
    printf '{"devices":[],"schemaVersion":1}\n' > "$STATE_ROOT/devices.json"
else
    [ "$(stat -c %a "$STATE_ROOT/devices.json")" = '640' ] && \
    [ "$(stat -c %U:%G "$STATE_ROOT/devices.json")" = "root:$GATEWAY_GROUP" ] || \
        fail 'existing gateway device store has unsafe ownership or mode'
fi
"$BINARY_ROOT/moos-gateway-device" --devices "$STATE_ROOT/devices.json" list >/dev/null

STAGED_GATEWAY=$(mktemp "$LIBEXEC_ROOT/.moos-gateway.new.XXXXXX")
STAGED_DEVICE=$(mktemp "$ADMIN_ROOT/.moos-gateway-device.new.XXXXXX")
STAGED_UNIT=$(mktemp "$UNIT_ROOT/.moos-gateway.service.new.XXXXXX")
STAGED_CONFIG=$(mktemp "$CONFIG_ROOT/.gateway.conf.new.XXXXXX")
BACKUP_GATEWAY=$(mktemp "$LIBEXEC_ROOT/.moos-gateway.rollback.XXXXXX")
BACKUP_DEVICE=$(mktemp "$ADMIN_ROOT/.moos-gateway-device.rollback.XXXXXX")
BACKUP_UNIT=$(mktemp "$UNIT_ROOT/.moos-gateway.service.rollback.XXXXXX")
BACKUP_CONFIG=$(mktemp "$CONFIG_ROOT/.gateway.conf.rollback.XXXXXX")
HAD_GATEWAY=0; HAD_DEVICE=0; HAD_UNIT=0; HAD_CONFIG=0

cleanup_install_files() {
    rm -f "$STAGED_GATEWAY" "$STAGED_DEVICE" "$STAGED_UNIT" "$STAGED_CONFIG" \
        "$BACKUP_GATEWAY" "$BACKUP_DEVICE" "$BACKUP_UNIT" "$BACKUP_CONFIG"
}
trap cleanup_install_files EXIT HUP INT TERM

install -o root -g root -m 0755 "$BINARY_ROOT/moos-gateway" "$STAGED_GATEWAY"
install -o root -g root -m 0755 "$BINARY_ROOT/moos-gateway-device" "$STAGED_DEVICE"
install -o root -g root -m 0644 "$SOURCE_ROOT/systemd/moos-gateway.service" "$STAGED_UNIT"
{
    printf 'MOOS_GATEWAY_LISTEN_ADDRESS=%s\n' "$LISTEN_ADDRESS"
    printf 'MOOS_GATEWAY_PORT=%s\n' "$PORT"
} > "$STAGED_CONFIG"
chown root:root "$STAGED_CONFIG"
chmod 0644 "$STAGED_CONFIG"

[ "$("$STAGED_GATEWAY" --version)" = "$GATEWAY_VERSION_LINE" ] || fail 'staged Gateway version validation failed'
[ "$("$STAGED_DEVICE" --version)" = "$DEVICE_VERSION_LINE" ] || fail 'staged device tool version validation failed'

for installed_binary in "$LIBEXEC_ROOT/moos-gateway" "$ADMIN_ROOT/moos-gateway-device"; do
    if [ -e "$installed_binary" ]; then
        [ -f "$installed_binary" ] && [ ! -L "$installed_binary" ] && \
        [ "$(stat -c %h "$installed_binary")" -eq 1 ] || fail "unsafe installed binary: $installed_binary"
    fi
done
if [ -e "$LIBEXEC_ROOT/moos-gateway" ]; then install -o root -g root -m 0755 "$LIBEXEC_ROOT/moos-gateway" "$BACKUP_GATEWAY"; HAD_GATEWAY=1; fi
if [ -e "$ADMIN_ROOT/moos-gateway-device" ]; then install -o root -g root -m 0755 "$ADMIN_ROOT/moos-gateway-device" "$BACKUP_DEVICE"; HAD_DEVICE=1; fi
if [ -e "$UNIT_ROOT/moos-gateway.service" ]; then install -o root -g root -m 0644 "$UNIT_ROOT/moos-gateway.service" "$BACKUP_UNIT"; HAD_UNIT=1; fi
if [ -e "$CONFIG_ROOT/gateway.conf" ]; then install -o root -g root -m 0644 "$CONFIG_ROOT/gateway.conf" "$BACKUP_CONFIG"; HAD_CONFIG=1; fi

WAS_ACTIVE=0
if systemctl is-active --quiet moos-gateway.service; then WAS_ACTIVE=1; fi
WAS_ENABLED=0
if systemctl is-enabled --quiet moos-gateway.service; then WAS_ENABLED=1; fi

rollback_install() {
    if [ "$HAD_GATEWAY" -eq 1 ]; then mv -f "$BACKUP_GATEWAY" "$LIBEXEC_ROOT/moos-gateway"; else rm -f "$LIBEXEC_ROOT/moos-gateway"; fi
    if [ "$HAD_DEVICE" -eq 1 ]; then mv -f "$BACKUP_DEVICE" "$ADMIN_ROOT/moos-gateway-device"; else rm -f "$ADMIN_ROOT/moos-gateway-device"; fi
    if [ "$HAD_UNIT" -eq 1 ]; then mv -f "$BACKUP_UNIT" "$UNIT_ROOT/moos-gateway.service"; else rm -f "$UNIT_ROOT/moos-gateway.service"; fi
    if [ "$HAD_CONFIG" -eq 1 ]; then mv -f "$BACKUP_CONFIG" "$CONFIG_ROOT/gateway.conf"; else rm -f "$CONFIG_ROOT/gateway.conf"; fi
    if [ "$WAS_ENABLED" -eq 0 ]; then systemctl disable moos-gateway.service || true; fi
    systemctl daemon-reload || true
    if [ "$WAS_ACTIVE" -eq 1 ]; then systemctl restart moos-gateway.service || true; else systemctl stop moos-gateway.service || true; fi
}

if ! mv -f "$STAGED_GATEWAY" "$LIBEXEC_ROOT/moos-gateway" || \
   ! mv -f "$STAGED_DEVICE" "$ADMIN_ROOT/moos-gateway-device" || \
   ! mv -f "$STAGED_UNIT" "$UNIT_ROOT/moos-gateway.service" || \
   ! mv -f "$STAGED_CONFIG" "$CONFIG_ROOT/gateway.conf" || \
   ! install -o root -g root -m 0644 "$SOURCE_ROOT/GATEWAY.md" "$DOC_ROOT/GATEWAY.md"; then
    rollback_install
    fail 'Gateway file replacement failed; previous installation restored'
fi

if [ "$("$LIBEXEC_ROOT/moos-gateway" --version)" != "$GATEWAY_VERSION_LINE" ] || \
   [ "$("$ADMIN_ROOT/moos-gateway-device" --version)" != "$DEVICE_VERSION_LINE" ]; then
    rollback_install
    fail 'installed Gateway version validation failed; previous installation restored'
fi

if ! systemctl daemon-reload || \
   ! systemctl enable moos-gateway.service || \
   ! systemctl restart moos-gateway.service || \
   ! systemctl is-active --quiet moos-gateway.service; then
    rollback_install
    fail 'MOOS Gateway failed to become active; previous installation restored'
fi

rm -f "$LIBEXEC_ROOT/moos_gateway.py" "$LIBEXEC_ROOT/moos_gateway_auth.py"
cleanup_install_files
trap - EXIT HUP INT TERM

echo "MOOS Gateway $GATEWAY_VERSION installed"
echo "  listen: $LISTEN_ADDRESS:$PORT"
echo '  pair a status-only device with:'
echo "    sudo moos-gateway-device add --name 'My iPhone' --allow status"
echo '  logs: journalctl -u moos-gateway.service'
