#!/bin/sh

set -eu

PATH='/usr/sbin:/usr/bin:/sbin:/bin'
export PATH
unset CDPATH ENV BASH_ENV PYTHONHOME PYTHONPATH
unset LD_PRELOAD LD_LIBRARY_PATH LD_AUDIT GCONV_PATH LOCPATH TMPDIR
LC_ALL=C
export LC_ALL

if [ "$(id -u)" -eq 0 ]; then
    TRUSTED_SELF=$(readlink -f -- "$0")
    case "$TRUSTED_SELF" in
        /usr/lib/moos/admin-releases/*/scripts/setup-control-plane.sh) ;;
        *)
            echo 'error: never run the control-plane installer as root from a checkout' >&2
            exit 1
            ;;
    esac
fi

SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
SOURCE_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
CONTROL_GROUP='moos-control'
VALIDATION_USER='moos-runtime'
LIBEXEC_ROOT='/usr/lib/moos'
RELEASE_ROOT="$LIBEXEC_ROOT/control-releases"
CURRENT_LINK="$LIBEXEC_ROOT/control-current"
ADMIN_RELEASE_ROOT="$LIBEXEC_ROOT/admin-releases"
UNIT_ROOT='/etc/systemd/system'
DOC_ROOT='/usr/share/doc/moos'
DAEMON_VERSION='MOOS control daemon 1'
MANIFEST_RELATIVE='configs/control-plane-manifest.sha256'
MANIFEST_SHA256='ab0bf9d4788fb575c5dc88764d727b153c9b2abf8ef8d31fb88c1490d47ed0d7'
DRY_RUN=0

usage() {
    cat <<'EOF'
Usage: scripts/setup-control-plane.sh [options]

Install the local socket-activated moosd control plane from an externally
authenticated, root-owned administrator release. Activation is atomic and
rolls back on failure. Never run this script as root from a checkout.

Options:
  --source-root DIR  MOOS repository to install from.
  --dry-run          Validate inputs and print the plan without host changes.
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

SOURCE_ROOT=$(cd -- "$SOURCE_ROOT" && pwd -P)
if [ "$(id -u)" -eq 0 ]; then
    case "$SOURCE_ROOT" in
        "$ADMIN_RELEASE_ROOT"/*) ;;
        *)
            echo "error: privileged source is not an authenticated administrator release: $SOURCE_ROOT" >&2
            echo "       provision it with /usr/libexec/moos/moos-admin-installer" >&2
            exit 1
            ;;
    esac
fi

# This verifier is isolated from site customization and never imports or
# executes code from the developer checkout. The reviewed manifest digest is
# embedded in this installer, and every staged artifact must match that
# manifest again when it is copied into root-owned storage.
python3 -I - "$SOURCE_ROOT" "$MANIFEST_RELATIVE" "$MANIFEST_SHA256" "$DRY_RUN" <<'PY'
import ast
import hashlib
import os
import re
import stat
import sys

root, manifest_relative, manifest_hash, dry_run = sys.argv[1:]
require_root_owned = dry_run != "1"
required_files = {
    "host/moos_protocol.py",
    "host/moos_runtime.py",
    "host/moosd.py",
    "scripts/moos",
    "scripts/moosd.py",
    "scripts/run-instance.sh",
    "systemd/moosd.service",
    "systemd/moosd.socket",
    "HOST_GUEST_ISOLATION.md",
}

def read_regular(relative):
    path = os.path.join(root, relative)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise SystemExit(f"error: unsafe or unreadable source file {relative}: {exc}")
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise SystemExit(f"error: source must be a regular, singly-linked file: {relative}")
        if require_root_owned and (before.st_uid != 0 or before.st_mode & 0o022):
            raise SystemExit(f"error: authenticated source has unsafe ownership or mode: {relative}")
        if before.st_size > 1024 * 1024:
            raise SystemExit(f"error: source file exceeds 1 MiB: {relative}")
        data = b""
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            data += chunk
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ) or len(data) != before.st_size:
            raise SystemExit(f"error: source changed while being inspected: {relative}")
    finally:
        os.close(fd)
    return data

manifest_data = read_regular(manifest_relative)
if hashlib.sha256(manifest_data).hexdigest() != manifest_hash:
    raise SystemExit("error: control-plane manifest is not the reviewed version")

files = {}
for line in manifest_data.decode("ascii").splitlines():
    match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_./-]+)", line)
    if match is None or match.group(2) in files:
        raise SystemExit("error: invalid control-plane manifest")
    files[match.group(2)] = match.group(1)
if set(files) != required_files:
    raise SystemExit("error: control-plane manifest has unexpected entries")

for relative, expected_hash in files.items():
    data = read_regular(relative)
    if relative.endswith(".py") or relative in {"scripts/moos", "scripts/moosd.py"}:
        try:
            ast.parse(data, filename=relative)
        except (SyntaxError, ValueError) as exc:
            raise SystemExit(f"error: invalid Python source {relative}: {exc}")
    if hashlib.sha256(data).hexdigest() != expected_hash:
        raise SystemExit(f"error: source does not match reviewed manifest: {relative}")
PY

if [ "$DRY_RUN" -eq 1 ]; then
    printf 'control group: %s (no automatic members)\n' "$CONTROL_GROUP"
    printf 'daemon identity: root:%s (fixed typed operations only)\n' "$CONTROL_GROUP"
    printf 'socket directory: /run/moos (root:root, mode 0711)\n'
    printf 'socket: /run/moos/moosd.sock (root:%s, mode 0660)\n' "$CONTROL_GROUP"
    printf 'runtime: systemd socket activation, restart on failure, journald logging\n'
    printf 'installed code: %s (root-owned, versioned releases)\n' "$RELEASE_ROOT"
    printf 'validation: digest-bound staged code runs sandboxed as %s\n' "$VALIDATION_USER"
    printf 'activation: atomic release pointer with transactional rollback\n'
    printf 'sudo policy: none\n'
    printf 'host mutation: none (dry-run)\n'
    exit 0
fi

[ "$(id -u)" -eq 0 ] || {
    echo 'error: control-plane setup must run as root' >&2
    echo '       use --dry-run as a normal user to inspect the plan' >&2
    exit 1
}
for command_name in cp date getent groupadd install ln mktemp mv python3 rm systemctl systemd-run timeout; do
    command -v "$command_name" >/dev/null 2>&1 || {
        echo "error: required host command is missing: $command_name" >&2
        exit 1
    }
done
getent passwd "$VALIDATION_USER" >/dev/null 2>&1 || {
    echo 'error: moos-runtime is not installed; run setup-runtime-user.sh first' >&2
    exit 1
}
PRIVATE_PIDS_SUPPORT=$(systemctl show \
    --property=PrivatePIDs \
    --value \
    systemd-journald.service 2>/dev/null || :)
case "$PRIVATE_PIDS_SUPPORT" in
    yes|no) ;;
    *)
        echo 'error: this systemd version lacks the required PrivatePIDs sandbox' >&2
        exit 1
        ;;
esac

safe_directory() {
    directory=$1
    if [ -L "$directory" ] || { [ -e "$directory" ] && [ ! -d "$directory" ]; }; then
        echo "error: trusted install path is not a real directory: $directory" >&2
        exit 1
    fi
}

for trusted_directory in "$LIBEXEC_ROOT" "$RELEASE_ROOT" "$UNIT_ROOT" "$DOC_ROOT" /usr/bin /run/moos; do
    safe_directory "$trusted_directory"
done

if ! getent group "$CONTROL_GROUP" >/dev/null 2>&1; then
    groupadd --system "$CONTROL_GROUP"
fi
install -d -o root -g root -m 0755 "$LIBEXEC_ROOT" "$RELEASE_ROOT" "$DOC_ROOT"
install -d -o root -g root -m 0711 /run/moos

STAGING=$(mktemp -d "$RELEASE_ROOT/.staging.XXXXXXXX")
chmod 0700 "$STAGING"
BACKUP=''
FINAL_RELEASE=''
ACTIVATING=0
SUCCESS=0

early_cleanup() {
    status=$?
    trap - EXIT HUP INT TERM
    [ -z "$STAGING" ] || rm -rf -- "$STAGING"
    [ -z "$FINAL_RELEASE" ] || rm -rf -- "$FINAL_RELEASE"
    [ -z "$BACKUP" ] || rm -rf -- "$BACKUP"
    exit "$status"
}
trap early_cleanup EXIT HUP INT TERM

stage_file() {
    source_file=$1
    destination=$2
    mode=$3
    expected_hash=${4-}
    python3 -I - "$source_file" "$destination" "$mode" "$expected_hash" <<'PY'
import ast
import hashlib
import os
import stat
import sys

source, destination, mode_text, expected_hash = sys.argv[1:]
flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
try:
    source_fd = os.open(source, flags)
except OSError as exc:
    raise SystemExit(f"error: unsafe source file {source}: {exc}")
try:
    before = os.fstat(source_fd)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise SystemExit(f"error: source must be a regular, singly-linked file: {source}")
    if before.st_size > 1024 * 1024:
        raise SystemExit(f"error: source file exceeds 1 MiB: {source}")
    data = b""
    while True:
        chunk = os.read(source_fd, 65536)
        if not chunk:
            break
        data += chunk
    after = os.fstat(source_fd)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    ) or len(data) != before.st_size:
        raise SystemExit(f"error: source changed while being staged: {source}")
finally:
    os.close(source_fd)

name = os.path.basename(source)
if name.endswith(".py") or name in {"moos", "moosd"}:
    try:
        ast.parse(data, filename=source)
    except (SyntaxError, ValueError) as exc:
        raise SystemExit(f"error: invalid Python source {source}: {exc}")
if expected_hash and hashlib.sha256(data).hexdigest() != expected_hash:
    raise SystemExit(f"error: unexpected privileged file content: {source}")

destination_fd = os.open(
    destination,
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
    int(mode_text, 8),
)
try:
    view = memoryview(data)
    while view:
        written = os.write(destination_fd, view)
        view = view[written:]
    os.fchmod(destination_fd, int(mode_text, 8))
    os.fchown(destination_fd, 0, 0)
    os.fsync(destination_fd)
finally:
    os.close(destination_fd)
PY
}

stage_file "$SOURCE_ROOT/$MANIFEST_RELATIVE" \
    "$STAGING/control-plane-manifest.sha256" 0644 "$MANIFEST_SHA256"

manifest_hash() {
    python3 -I - "$STAGING/control-plane-manifest.sha256" "$1" <<'PY'
import re
import sys

manifest, requested = sys.argv[1:]
matches = []
with open(manifest, "r", encoding="ascii") as handle:
    for line in handle:
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_./-]+)\n?", line)
        if match is not None and match.group(2) == requested:
            matches.append(match.group(1))
if len(matches) != 1:
    raise SystemExit(f"error: missing or duplicate manifest entry: {requested}")
print(matches[0])
PY
}

stage_file "$SOURCE_ROOT/host/moos_protocol.py" "$STAGING/moos_protocol.py" 0644 \
    "$(manifest_hash host/moos_protocol.py)"
stage_file "$SOURCE_ROOT/host/moos_runtime.py" "$STAGING/moos_runtime.py" 0644 \
    "$(manifest_hash host/moos_runtime.py)"
stage_file "$SOURCE_ROOT/host/moosd.py" "$STAGING/moosd.py" 0644 \
    "$(manifest_hash host/moosd.py)"
stage_file "$SOURCE_ROOT/scripts/moos" "$STAGING/moos" 0755 \
    "$(manifest_hash scripts/moos)"
stage_file "$SOURCE_ROOT/scripts/moosd.py" "$STAGING/moosd" 0755 \
    "$(manifest_hash scripts/moosd.py)"
stage_file "$SOURCE_ROOT/scripts/run-instance.sh" "$STAGING/run-instance.sh" 0755 \
    "$(manifest_hash scripts/run-instance.sh)"
stage_file "$SOURCE_ROOT/systemd/moosd.service" "$STAGING/moosd.service" 0644 \
    "$(manifest_hash systemd/moosd.service)"
stage_file "$SOURCE_ROOT/systemd/moosd.socket" "$STAGING/moosd.socket" 0644 \
    "$(manifest_hash systemd/moosd.socket)"
stage_file "$SOURCE_ROOT/HOST_GUEST_ISOLATION.md" "$STAGING/HOST_GUEST_ISOLATION.md" 0644 \
    "$(manifest_hash HOST_GUEST_ISOLATION.md)"
chmod 0755 "$STAGING"

run_sandboxed() {
    systemd-run --quiet --wait --collect --pipe \
        --uid="$VALIDATION_USER" --gid="$VALIDATION_USER" \
        --property=SupplementaryGroups= \
        --property=NoNewPrivileges=yes \
        --property=PrivateDevices=yes \
        --property=PrivateTmp=yes \
        --property=ProtectSystem=strict \
        --property=ProtectHome=yes \
        --property=ProtectKernelTunables=yes \
        --property=ProtectKernelModules=yes \
        --property=ProtectControlGroups=yes \
        --property=RestrictSUIDSGID=yes \
        --property=RestrictAddressFamilies=AF_UNIX \
        --property=InaccessiblePaths='/var/lib/moos /run/moos' \
        --setenv=PATH=/usr/bin:/bin \
        --setenv=PYTHONHOME= \
        --setenv=PYTHONPATH= \
        --setenv=PYTHONNOUSERSITE=1 \
        --property=PrivatePIDs=yes \
        -- "$@"
}

[ "$(run_sandboxed "$STAGING/moosd" --version)" = "$DAEMON_VERSION" ] || {
    echo 'error: staged daemon reported an unexpected version' >&2
    exit 1
}
run_sandboxed "$STAGING/moosd" --runner "$STAGING/run-instance.sh" --check-config
run_sandboxed /bin/sh -n "$STAGING/run-instance.sh"
run_sandboxed "$STAGING/moos" --help >/dev/null

FINAL_RELEASE="$RELEASE_ROOT/$(date -u +%Y%m%dT%H%M%SZ)-$$"
mv -T "$STAGING" "$FINAL_RELEASE"
STAGING=''

BACKUP=$(mktemp -d "$LIBEXEC_ROOT/.control-backup.XXXXXXXX")
chmod 0700 "$BACKUP"

backup_one() {
    target=$1
    key=$2
    if [ -e "$target" ] || [ -L "$target" ]; then
        cp -a -- "$target" "$BACKUP/$key"
    else
        : >"$BACKUP/$key.absent"
    fi
}

restore_one() {
    target=$1
    key=$2
    rm -f -- "$target"
    if [ ! -e "$BACKUP/$key.absent" ]; then
        cp -a -- "$BACKUP/$key" "$target"
    fi
}

backup_one "$UNIT_ROOT/moosd.service" service
backup_one "$UNIT_ROOT/moosd.socket" socket
backup_one "$DOC_ROOT/HOST_GUEST_ISOLATION.md" documentation
backup_one "$CURRENT_LINK" current
backup_one "$LIBEXEC_ROOT/moosd" daemon
backup_one "$LIBEXEC_ROOT/run-instance.sh" runner
backup_one /usr/bin/moos client

SOCKET_WAS_ACTIVE=0
SERVICE_WAS_ACTIVE=0
SOCKET_WAS_ENABLED=0
systemctl is-active --quiet moosd.socket && SOCKET_WAS_ACTIVE=1 || :
systemctl is-active --quiet moosd.service && SERVICE_WAS_ACTIVE=1 || :
systemctl is-enabled --quiet moosd.socket && SOCKET_WAS_ENABLED=1 || :

rollback() {
    echo 'error: activation failed; restoring the previous control-plane release' >&2
    systemctl stop moosd.service moosd.socket >/dev/null 2>&1 || :
    restore_one "$UNIT_ROOT/moosd.service" service
    restore_one "$UNIT_ROOT/moosd.socket" socket
    restore_one "$DOC_ROOT/HOST_GUEST_ISOLATION.md" documentation
    restore_one "$CURRENT_LINK" current
    restore_one "$LIBEXEC_ROOT/moosd" daemon
    restore_one "$LIBEXEC_ROOT/run-instance.sh" runner
    restore_one /usr/bin/moos client
    systemctl daemon-reload >/dev/null 2>&1 || return 1
    if [ "$SOCKET_WAS_ENABLED" -eq 1 ]; then
        systemctl enable moosd.socket >/dev/null 2>&1 || return 1
    else
        systemctl disable moosd.socket >/dev/null 2>&1 || return 1
    fi
    [ "$SOCKET_WAS_ACTIVE" -eq 0 ] || systemctl start moosd.socket >/dev/null 2>&1 || return 1
    [ "$SERVICE_WAS_ACTIVE" -eq 0 ] || systemctl start moosd.service >/dev/null 2>&1 || return 1
}

cleanup() {
    status=$?
    trap - EXIT HUP INT TERM
    if [ "$SUCCESS" -ne 1 ] && [ "$ACTIVATING" -eq 1 ]; then
        if rollback; then
            rm -rf -- "$BACKUP" "$FINAL_RELEASE"
        else
            echo "error: rollback incomplete; retained recovery data in $BACKUP" >&2
            status=1
        fi
    elif [ "$SUCCESS" -ne 1 ]; then
        [ -z "$STAGING" ] || rm -rf -- "$STAGING"
        [ -z "$FINAL_RELEASE" ] || rm -rf -- "$FINAL_RELEASE"
        [ -z "$BACKUP" ] || rm -rf -- "$BACKUP"
    fi
    exit "$status"
}
trap cleanup EXIT HUP INT TERM

ACTIVATING=1
stop_if_loaded() {
    unit=$1
    load_state=$(systemctl show --property=LoadState --value "$unit" 2>/dev/null || :)
    case "$load_state" in
        ''|not-found) ;;
        *) systemctl stop "$unit" ;;
    esac
}
stop_if_loaded moosd.service
stop_if_loaded moosd.socket

install -o root -g root -m 0644 "$FINAL_RELEASE/moosd.service" "$UNIT_ROOT/.moosd.service.new"
install -o root -g root -m 0644 "$FINAL_RELEASE/moosd.socket" "$UNIT_ROOT/.moosd.socket.new"
install -o root -g root -m 0644 "$FINAL_RELEASE/HOST_GUEST_ISOLATION.md" "$DOC_ROOT/.HOST_GUEST_ISOLATION.md.new"
mv -Tf "$UNIT_ROOT/.moosd.service.new" "$UNIT_ROOT/moosd.service"
mv -Tf "$UNIT_ROOT/.moosd.socket.new" "$UNIT_ROOT/moosd.socket"
mv -Tf "$DOC_ROOT/.HOST_GUEST_ISOLATION.md.new" "$DOC_ROOT/HOST_GUEST_ISOLATION.md"

CURRENT_TEMP="$LIBEXEC_ROOT/.control-current.$$"
DAEMON_TEMP="$LIBEXEC_ROOT/.moosd.$$"
RUNNER_TEMP="$LIBEXEC_ROOT/.run-instance.$$"
CLIENT_TEMP="/usr/bin/.moos.$$"
ln -s "$FINAL_RELEASE" "$CURRENT_TEMP"
ln -s 'control-current/moosd' "$DAEMON_TEMP"
ln -s 'control-current/run-instance.sh' "$RUNNER_TEMP"
ln -s '../lib/moos/control-current/moos' "$CLIENT_TEMP"
mv -Tf "$CURRENT_TEMP" "$CURRENT_LINK"
mv -Tf "$DAEMON_TEMP" "$LIBEXEC_ROOT/moosd"
mv -Tf "$RUNNER_TEMP" "$LIBEXEC_ROOT/run-instance.sh"
mv -Tf "$CLIENT_TEMP" /usr/bin/moos

systemctl daemon-reload
systemctl enable moosd.socket
systemctl restart moosd.socket
systemctl is-active --quiet moosd.socket
timeout 10 /usr/bin/moos --socket /run/moos/moosd.sock status >/dev/null
systemctl is-active --quiet moosd.service
[ "$(systemctl show --property=TasksMax --value moosd.service)" = '64' ]
[ "$(systemctl show --property=MemoryMax --value moosd.service)" = '134217728' ]

SUCCESS=1
ACTIVATING=0
rm -rf -- "$BACKUP"
trap - EXIT HUP INT TERM

echo 'MOOS control plane installed'
echo "  release: $FINAL_RELEASE"
echo "  socket group: $CONTROL_GROUP"
echo '  grant access explicitly with: usermod -aG moos-control USER'
echo '  logs: journalctl -u moosd.service'
