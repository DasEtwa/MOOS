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
        /usr/lib/moos/admin-releases/*/scripts/setup-runtime-user.sh) ;;
        *)
            echo 'error: never run runtime setup as root from a checkout' >&2
            exit 1
            ;;
    esac
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
RUNTIME_USER='moos-runtime'
RUNTIME_GROUP='moos-runtime'
STATE_ROOT='/var/lib/moos'
LIBEXEC_ROOT='/usr/lib/moos'
SOURCE_ROOT="$REPO_ROOT"
DRY_RUN=0

usage() {
    cat <<'EOF'
Usage: scripts/setup-runtime-user.sh [options]

Create the dedicated unprivileged MOOS runtime account and its private host
storage. The real setup must be run as root. Use --dry-run to inspect it first.

Options:
  --source-root DIR  Repository containing scripts/run-qemu.sh.
  --dry-run          Print the planned changes without modifying the host.
  -h, --help         Show this help.

The account is locked, has /usr/sbin/nologin (or the host's nologin path), has
no supplementary groups, and cannot use the developer's home as runtime
storage. This script does not create sudo rules or grant device groups.
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

if [ ! -d "$SOURCE_ROOT" ]; then
    echo "error: source root does not exist: $SOURCE_ROOT" >&2
    exit 1
fi
SOURCE_ROOT=$(CDPATH= cd -- "$SOURCE_ROOT" && pwd -P)
[ -f "$SOURCE_ROOT/scripts/run-qemu.sh" ] || {
    echo 'error: source root does not contain scripts/run-qemu.sh' >&2
    exit 1
}

NOLOGIN=$(command -v nologin || true)
if [ -z "$NOLOGIN" ]; then
    echo 'error: could not find a nologin shell' >&2
    exit 1
fi

if [ "$DRY_RUN" -eq 1 ]; then
    printf 'runtime user: %s\n' "$RUNTIME_USER"
    printf 'runtime group: %s\n' "$RUNTIME_GROUP"
    printf 'login shell: %s\n' "$NOLOGIN"
    printf 'account state: locked, no supplementary groups\n'
    printf 'state root: %s (root:%s, mode 0750)\n' "$STATE_ROOT" "$RUNTIME_GROUP"
    printf 'instance storage: %s/instances (root:%s, mode 0750)\n' "$STATE_ROOT" "$RUNTIME_GROUP"
    printf 'runtime files: %s (root:%s, mode 0750)\n' "$STATE_ROOT/runtime" "$RUNTIME_GROUP"
    printf 'installed launcher: %s/run-qemu.sh (root:root, mode 0755)\n' "$LIBEXEC_ROOT"
    printf 'host mutation: none (dry-run)\n'
    exit 0
fi

[ "$(id -u)" -eq 0 ] || {
    echo 'error: the real runtime-user setup must run as root' >&2
    echo '       use --dry-run as a normal user to inspect the plan' >&2
    exit 1
}
case "$SOURCE_ROOT" in
    /usr/lib/moos/admin-releases/*) ;;
    *)
        echo "error: privileged source is not an authenticated administrator release: $SOURCE_ROOT" >&2
        exit 1
        ;;
esac

for command_name in awk cut getent id install passwd useradd usermod; do
    command -v "$command_name" >/dev/null 2>&1 || {
        echo "error: required host command is missing: $command_name" >&2
        exit 1
    }
done

if getent passwd "$RUNTIME_USER" >/dev/null 2>&1; then
    passwd_entry=$(getent passwd "$RUNTIME_USER")
    existing_home=$(printf '%s\n' "$passwd_entry" | cut -d: -f6)
    existing_shell=$(printf '%s\n' "$passwd_entry" | cut -d: -f7)
    [ "$existing_home" = "$STATE_ROOT" ] || {
        echo "error: existing $RUNTIME_USER account has unexpected home: $existing_home" >&2
        exit 1
    }
    [ "$existing_shell" = "$NOLOGIN" ] || {
        echo "error: existing $RUNTIME_USER account has unexpected shell: $existing_shell" >&2
        exit 1
    }
else
    useradd \
        --system \
        --user-group \
        --no-create-home \
        --home-dir "$STATE_ROOT" \
        --shell "$NOLOGIN" \
        "$RUNTIME_USER"
fi

[ "$(id -gn "$RUNTIME_USER")" = "$RUNTIME_GROUP" ] || {
    echo "error: $RUNTIME_USER does not have the expected primary group" >&2
    exit 1
}
runtime_groups=$(id -nG "$RUNTIME_USER")
[ "$runtime_groups" = "$RUNTIME_GROUP" ] || {
    echo "error: $RUNTIME_USER has unexpected supplementary groups: $runtime_groups" >&2
    echo '       remove them manually and rerun this setup' >&2
    exit 1
}

usermod --lock "$RUNTIME_USER"
password_state=$(passwd -S "$RUNTIME_USER" | awk '{print $2}')
case "$password_state" in
    L|LK)
        ;;
    *)
        echo "error: could not verify a locked password for $RUNTIME_USER" >&2
        exit 1
        ;;
esac

install -d -o root -g "$RUNTIME_GROUP" -m 0750 "$STATE_ROOT"
install -d -o root -g "$RUNTIME_GROUP" -m 0750 "$STATE_ROOT/instances"
install -d -o root -g "$RUNTIME_GROUP" -m 0750 "$STATE_ROOT/runtime"
install -d -o root -g root -m 0755 "$LIBEXEC_ROOT"
install -o root -g root -m 0755 \
    "$SOURCE_ROOT/scripts/run-qemu.sh" "$LIBEXEC_ROOT/run-qemu.sh"

echo "MOOS runtime account ready: $RUNTIME_USER"
echo "  shell: $NOLOGIN"
echo "  groups: $runtime_groups"
echo "  state: $STATE_ROOT"
echo "  launcher: $LIBEXEC_ROOT/run-qemu.sh"
