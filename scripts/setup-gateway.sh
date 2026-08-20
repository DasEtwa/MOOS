#!/bin/sh

set -eu

PATH='/usr/sbin:/usr/bin:/sbin:/bin'
export PATH
unset CDPATH ENV BASH_ENV PYTHONHOME PYTHONPATH

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
EXPECTED_GATEWAY_UNIT_SHA256='a3c54b0d3667387a5670059f51a6cedd370bbbc8a897084058f304dd41e94108'

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
for command_name in od stat tr; do
    command -v "$command_name" >/dev/null 2>&1 || \
        fail "required host command is missing: $command_name"
done

for source_file in GATEWAY.md systemd/moos-gateway.service; do
    [ -f "$SOURCE_ROOT/$source_file" ] && \
    [ ! -L "$SOURCE_ROOT/$source_file" ] || \
        fail "safe source file is missing: $source_file"
done
for gateway_binary in moos-gateway moos-gateway-device; do
    binary_path="$BINARY_ROOT/$gateway_binary"
    [ -f "$binary_path" ] && [ ! -L "$binary_path" ] && [ -x "$binary_path" ] || \
        fail "safe prebuilt release binary is missing: $binary_path"
    binary_size=$(stat -c %s "$binary_path")
    [ "$binary_size" -ge 64 ] && [ "$binary_size" -le 67108864 ] || \
        fail "release binary size is unsafe: $binary_path"
    [ "$(od -An -tx1 -N4 "$binary_path" | tr -d ' \n')" = '7f454c46' ] || \
        fail "release binary is not an ELF artifact: $binary_path"
done

if [ "$DRY_RUN" -eq 1 ]; then
    printf 'gateway identity: %s:%s (locked, nologin)\n' "$GATEWAY_USER" "$GATEWAY_GROUP"
    printf 'listen: %s:%s (Tailscale-only)\n' "$LISTEN_ADDRESS" "$PORT"
    printf 'device store: %s/devices.json (root:%s, mode 0640)\n' "$STATE_ROOT" "$GATEWAY_GROUP"
    printf 'moosd access: supplementary group %s, typed Protocol v1 only\n' "$CONTROL_GROUP"
    printf 'remote grants: status only, deny by default\n'
    printf 'runtime: prebuilt Rust artifacts; executable validation occurs after root-owned staging\n'
    printf 'artifact input: regular ELF; Cargo hardlinks and checkout modes are accepted before staging\n'
    printf 'sudo policy: none\n'
    printf 'host mutation: none (dry-run)\n'
    exit 0
fi

[ "$(id -u)" -eq 0 ] || fail 'gateway setup must run as root; use --dry-run to inspect the plan'
for command_name in awk chmod chown dd getent groupadd install mktemp mv passwd rm sha256sum stat systemctl systemd-run tr useradd; do
    command -v "$command_name" >/dev/null 2>&1 || fail "required host command is missing: $command_name"
done
getent group "$CONTROL_GROUP" >/dev/null 2>&1 || \
    fail 'moos-control is missing; install the local control plane first'
[ "$(sha256sum "$SOURCE_ROOT/systemd/moos-gateway.service" | awk '{print $1}')" = \
    "$EXPECTED_GATEWAY_UNIT_SHA256" ] || \
    fail 'systemd unit does not match the reviewed policy'
PRIVATE_PIDS_SUPPORT=$(systemctl show --property=PrivatePIDs --value \
    systemd-journald.service 2>/dev/null || :)
case "$PRIVATE_PIDS_SUPPORT" in
    yes|no) ;;
    *) fail 'this systemd version lacks the required PrivatePIDs sandbox' ;;
esac

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
trap 'rm -f "$UID_TEMP"' EXIT
trap 'exit 1' HUP INT TERM
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

STAGED_GATEWAY=''
STAGED_DEVICE=''
STAGED_UNIT=''
STAGED_CONFIG=''
STAGED_DOC=''
BACKUP_GATEWAY=''
BACKUP_DEVICE=''
BACKUP_UNIT=''
BACKUP_CONFIG=''
BACKUP_DOC=''
VALIDATION_ROOT=''
PRESERVE_ROLLBACK=0
HAD_GATEWAY=0; HAD_DEVICE=0; HAD_UNIT=0; HAD_CONFIG=0; HAD_DOC=0

cleanup_install_files() {
    trap '' HUP INT TERM
    rm -f -- "$STAGED_GATEWAY" "$STAGED_DEVICE" "$STAGED_UNIT" \
        "$STAGED_CONFIG" "$STAGED_DOC"
    if [ "$PRESERVE_ROLLBACK" -eq 0 ]; then
        rm -f -- "$BACKUP_GATEWAY" "$BACKUP_DEVICE" "$BACKUP_UNIT" \
            "$BACKUP_CONFIG" "$BACKUP_DOC"
    fi
    if [ -n "$VALIDATION_ROOT" ]; then
        rm -rf -- "$VALIDATION_ROOT"
    fi
}
trap cleanup_install_files EXIT
trap 'exit 1' HUP INT TERM

STAGED_GATEWAY=$(mktemp "$LIBEXEC_ROOT/.moos-gateway.new.XXXXXX")
STAGED_DEVICE=$(mktemp "$ADMIN_ROOT/.moos-gateway-device.new.XXXXXX")
STAGED_UNIT=$(mktemp "$UNIT_ROOT/.moos-gateway.service.new.XXXXXX")
STAGED_CONFIG=$(mktemp "$CONFIG_ROOT/.gateway.conf.new.XXXXXX")
STAGED_DOC=$(mktemp "$DOC_ROOT/.GATEWAY.md.new.XXXXXX")
BACKUP_GATEWAY=$(mktemp "$LIBEXEC_ROOT/.moos-gateway.rollback.XXXXXX")
BACKUP_DEVICE=$(mktemp "$ADMIN_ROOT/.moos-gateway-device.rollback.XXXXXX")
BACKUP_UNIT=$(mktemp "$UNIT_ROOT/.moos-gateway.service.rollback.XXXXXX")
BACKUP_CONFIG=$(mktemp "$CONFIG_ROOT/.gateway.conf.rollback.XXXXXX")
BACKUP_DOC=$(mktemp "$DOC_ROOT/.GATEWAY.md.rollback.XXXXXX")

stage_checkout_file() {
    source_path=$1
    destination_path=$2
    destination_mode=$3
    maximum_bytes=$4
    copy_bytes=$((maximum_bytes + 1))
    dd if="$source_path" of="$destination_path" \
        iflag=nofollow,nonblock,count_bytes count="$copy_bytes" \
        conv=fsync status=none
    [ "$(stat -c %s "$destination_path")" -le "$maximum_bytes" ] || \
        fail "staged input exceeds its size limit: $source_path"
    chown root:root "$destination_path"
    chmod "$destination_mode" "$destination_path"
}

stage_checkout_file \
    "$BINARY_ROOT/moos-gateway" "$STAGED_GATEWAY" 0755 67108864
stage_checkout_file \
    "$BINARY_ROOT/moos-gateway-device" "$STAGED_DEVICE" 0755 67108864
stage_checkout_file \
    "$SOURCE_ROOT/systemd/moos-gateway.service" "$STAGED_UNIT" 0644 1048576
stage_checkout_file "$SOURCE_ROOT/GATEWAY.md" "$STAGED_DOC" 0644 1048576
[ "$(sha256sum "$STAGED_UNIT" | awk '{print $1}')" = \
    "$EXPECTED_GATEWAY_UNIT_SHA256" ] || \
    fail 'staged systemd unit does not match the reviewed policy'
{
    printf 'MOOS_GATEWAY_LISTEN_ADDRESS=%s\n' "$LISTEN_ADDRESS"
    printf 'MOOS_GATEWAY_PORT=%s\n' "$PORT"
} > "$STAGED_CONFIG"
chown root:root "$STAGED_CONFIG"
chmod 0644 "$STAGED_CONFIG"

VALIDATION_ROOT="$LIBEXEC_ROOT/.gateway-validation.$$"
[ ! -e "$VALIDATION_ROOT" ] && [ ! -L "$VALIDATION_ROOT" ] || \
    fail "stale Gateway validation path exists: $VALIDATION_ROOT"
install -d -o root -g "$GATEWAY_GROUP" -m 0750 "$VALIDATION_ROOT"
install -o root -g "$GATEWAY_GROUP" -m 0640 /dev/null \
    "$VALIDATION_ROOT/devices.json"
printf '{"devices":[],"schemaVersion":1}\n' > "$VALIDATION_ROOT/devices.json"

run_staged_validation() {
    validation_name=$1
    shift
    systemd-run \
        --unit="moos-gateway-validation-$validation_name-$$" \
        --wait --pipe --collect --quiet --service-type=exec \
        --uid="$GATEWAY_USER" \
        --gid="$GATEWAY_GROUP" \
        --working-directory="$LIBEXEC_ROOT" \
        --setenv=PATH=/usr/sbin:/usr/bin \
        --property="SupplementaryGroups=$CONTROL_GROUP" \
        --property=UMask=0077 \
        --property=NoNewPrivileges=yes \
        --property=PrivateTmp=yes \
        --property=PrivateDevices=yes \
        --property=PrivatePIDs=yes \
        --property=ProtectSystem=strict \
        --property=ProtectHome=yes \
        --property=ProtectProc=invisible \
        --property=ProtectHostname=yes \
        --property=ProtectKernelTunables=yes \
        --property=ProtectKernelModules=yes \
        --property=ProtectKernelLogs=yes \
        --property=ProtectClock=yes \
        --property=ProtectControlGroups=yes \
        --property=RestrictNamespaces=yes \
        --property=RestrictRealtime=yes \
        --property=RestrictSUIDSGID=yes \
        --property=LockPersonality=yes \
        --property=MemoryDenyWriteExecute=yes \
        --property=SystemCallArchitectures=native \
        --property='RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6' \
        --property=RestrictNetworkInterfaces=tailscale0 \
        --property=IPAddressDeny=any \
        --property=IPAddressAllow=100.64.0.0/10 \
        --property=IPAddressAllow=fd7a:115c:a1e0::/48 \
        --property=CapabilityBoundingSet= \
        --property=AmbientCapabilities= \
        --property="InaccessiblePaths=$STATE_ROOT -/run/moos" \
        --property=TasksMax=16 \
        --property=MemoryMax=64M \
        --property=CPUQuota=20% \
        --property=TimeoutStartSec=15s \
        --property=RuntimeMaxSec=15s \
        -- "$@"
}

GATEWAY_VERSION_LINE=$(run_staged_validation gateway-version \
    "$STAGED_GATEWAY" --version) || fail 'staged moos-gateway --version failed'
DEVICE_VERSION_LINE=$(run_staged_validation device-version \
    "$STAGED_DEVICE" --version) || fail 'staged moos-gateway-device --version failed'
case "$GATEWAY_VERSION_LINE" in
    'moos-gateway '[0-9]* ) GATEWAY_VERSION=${GATEWAY_VERSION_LINE#moos-gateway } ;;
    *) fail 'invalid staged moos-gateway version output' ;;
esac
[ "$DEVICE_VERSION_LINE" = "moos-gateway-device $GATEWAY_VERSION" ] || \
    fail 'staged Gateway release binaries have mismatched versions'
case "$GATEWAY_VERSION" in
    *[!0-9A-Za-z.+-]*|'') fail 'invalid Gateway release version' ;;
esac
run_staged_validation config "$STAGED_GATEWAY" check-config \
    --listen-address "$LISTEN_ADDRESS" --port "$PORT" \
    --devices "$VALIDATION_ROOT/devices.json"
run_staged_validation interface "$STAGED_GATEWAY" validate-interface \
    --listen-address "$LISTEN_ADDRESS" --port "$PORT"
rm -rf -- "$VALIDATION_ROOT"
VALIDATION_ROOT=''

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
if [ -e "$DOC_ROOT/GATEWAY.md" ]; then install -o root -g root -m 0644 "$DOC_ROOT/GATEWAY.md" "$BACKUP_DOC"; HAD_DOC=1; fi

WAS_ACTIVE=0
if systemctl is-active --quiet moos-gateway.service; then WAS_ACTIVE=1; fi
WAS_ENABLED=0
if systemctl is-enabled --quiet moos-gateway.service; then WAS_ENABLED=1; fi

rollback_install() {
    rollback_failed=0
    if [ "$HAD_GATEWAY" -eq 1 ]; then mv -f "$BACKUP_GATEWAY" "$LIBEXEC_ROOT/moos-gateway" || rollback_failed=1; else rm -f "$LIBEXEC_ROOT/moos-gateway" || rollback_failed=1; fi
    if [ "$HAD_DEVICE" -eq 1 ]; then mv -f "$BACKUP_DEVICE" "$ADMIN_ROOT/moos-gateway-device" || rollback_failed=1; else rm -f "$ADMIN_ROOT/moos-gateway-device" || rollback_failed=1; fi
    if [ "$HAD_UNIT" -eq 1 ]; then mv -f "$BACKUP_UNIT" "$UNIT_ROOT/moos-gateway.service" || rollback_failed=1; else rm -f "$UNIT_ROOT/moos-gateway.service" || rollback_failed=1; fi
    if [ "$HAD_CONFIG" -eq 1 ]; then mv -f "$BACKUP_CONFIG" "$CONFIG_ROOT/gateway.conf" || rollback_failed=1; else rm -f "$CONFIG_ROOT/gateway.conf" || rollback_failed=1; fi
    if [ "$HAD_DOC" -eq 1 ]; then mv -f "$BACKUP_DOC" "$DOC_ROOT/GATEWAY.md" || rollback_failed=1; else rm -f "$DOC_ROOT/GATEWAY.md" || rollback_failed=1; fi
    if [ "$WAS_ENABLED" -eq 0 ]; then systemctl disable moos-gateway.service || rollback_failed=1; fi
    systemctl daemon-reload || rollback_failed=1
    if [ "$WAS_ACTIVE" -eq 1 ]; then
        systemctl restart moos-gateway.service || rollback_failed=1
        systemctl is-active --quiet moos-gateway.service || rollback_failed=1
    else
        systemctl stop moos-gateway.service || rollback_failed=1
        if systemctl is-active --quiet moos-gateway.service; then rollback_failed=1; fi
    fi
    if [ "$rollback_failed" -ne 0 ]; then
        PRESERVE_ROLLBACK=1
        return 1
    fi
    return 0
}

if ! mv -f "$STAGED_GATEWAY" "$LIBEXEC_ROOT/moos-gateway" || \
   ! mv -f "$STAGED_DEVICE" "$ADMIN_ROOT/moos-gateway-device" || \
   ! mv -f "$STAGED_UNIT" "$UNIT_ROOT/moos-gateway.service" || \
   ! mv -f "$STAGED_CONFIG" "$CONFIG_ROOT/gateway.conf" || \
   ! mv -f "$STAGED_DOC" "$DOC_ROOT/GATEWAY.md"; then
    if rollback_install; then
        fail 'Gateway file replacement failed; previous installation restored'
    fi
    fail 'Gateway file replacement and rollback failed; rollback files were preserved for recovery'
fi

if ! systemctl daemon-reload || \
   ! systemctl enable moos-gateway.service || \
   ! systemctl restart moos-gateway.service || \
   ! systemctl is-active --quiet moos-gateway.service; then
    if rollback_install; then
        fail 'MOOS Gateway failed to become active; previous installation restored'
    fi
    fail 'MOOS Gateway activation and rollback failed; rollback files were preserved for recovery'
fi

rm -f "$LIBEXEC_ROOT/moos_gateway.py" "$LIBEXEC_ROOT/moos_gateway_auth.py"
cleanup_install_files
trap - EXIT HUP INT TERM

echo "MOOS Gateway $GATEWAY_VERSION installed"
echo "  listen: $LISTEN_ADDRESS:$PORT"
echo '  pair a status-only device with:'
echo "    sudo moos-gateway-device add --name 'My iPhone' --allow status"
echo '  logs: journalctl -u moos-gateway.service'
