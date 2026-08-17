#!/bin/sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
CONFIG_FILE="$SCRIPT_DIR/../configs/personal-instance.conf"

usage() {
    cat <<'EOF'
Usage: scripts/personal-instance.sh [id|display-name|role|all]

Print the stable identity of the single Personal MOOS system used by the
mobile MVP. This does not enumerate or manage general Instances.
EOF
}

read_value() {
    key=$1
    awk -F= -v wanted_key="$key" '
        $0 !~ /^[[:space:]]*#/ && $1 == wanted_key {
            print substr($0, index($0, "=") + 1)
            found = 1
            exit
        }
        END { if (!found) exit 1 }
    ' "$CONFIG_FILE"
}

[ -r "$CONFIG_FILE" ] || {
    echo "error: Personal identity config is missing: $CONFIG_FILE" >&2
    exit 1
}

INSTANCE_ID=$(read_value id)
DISPLAY_NAME=$(read_value display_name)
ROLE=$(read_value role)

[ "$INSTANCE_ID" = 'personal' ] || {
    echo 'error: Personal identity must use the stable id personal' >&2
    exit 1
}
[ "$DISPLAY_NAME" != '' ] || {
    echo 'error: Personal display name must not be empty' >&2
    exit 1
}
[ "$ROLE" = 'personal' ] || {
    echo 'error: Personal identity must use the personal role' >&2
    exit 1
}

case "${1:-all}" in
    id) printf '%s\n' "$INSTANCE_ID" ;;
    display-name) printf '%s\n' "$DISPLAY_NAME" ;;
    role) printf '%s\n' "$ROLE" ;;
    all)
        printf 'id: %s\n' "$INSTANCE_ID"
        printf 'display-name: %s\n' "$DISPLAY_NAME"
        printf 'role: %s\n' "$ROLE"
        ;;
    -h|--help) usage ;;
    *)
        echo "error: unknown field: $1" >&2
        usage >&2
        exit 2
        ;;
esac
