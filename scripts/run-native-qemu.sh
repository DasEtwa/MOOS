#!/bin/sh
# Development acceptance runner; never an installer or managed Host backend.
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MOOS_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
IMAGE="$MOOS_ROOT/output/native/images/moos-native-x86_64.img"
QEMU="$MOOS_ROOT/output/host/bin/qemu-system-x86_64"
BOOT=bios
NETWORK=none
CODE=''
VARS=''
DRY_RUN=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --image|--qemu|--boot|--network|--uefi-code|--uefi-vars)
            [ "$#" -ge 2 ] || { echo "error: $1 requires a value" >&2; exit 2; }
            case "$1" in
                --image) IMAGE=$2 ;; --qemu) QEMU=$2 ;; --boot) BOOT=$2 ;;
                --network) NETWORK=$2 ;; --uefi-code) CODE=$2 ;; --uefi-vars) VARS=$2 ;;
            esac
            shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help)
            echo 'Usage: scripts/run-native-qemu.sh [--image FILE] [--qemu FILE] [--boot bios|uefi] [--uefi-code FILE --uefi-vars FILE] [--network none|user] [--dry-run]'
            exit 0 ;;
        *) echo "error: unknown option: $1" >&2; exit 2 ;;
    esac
done
case "$BOOT" in bios|uefi) ;; *) echo 'error: boot must be bios or uefi' >&2; exit 2 ;; esac
case "$NETWORK" in none|user) ;; *) echo 'error: network must be none or user' >&2; exit 2 ;; esac
[ -f "$IMAGE" ] && [ ! -b "$IMAGE" ] || { echo 'error: Native image must be a regular file' >&2; exit 1; }
case "$IMAGE$CODE$VARS" in *,*) echo 'error: QEMU file paths must not contain commas' >&2; exit 2 ;; esac
if [ ! -x "$QEMU" ]; then
    echo 'error: QEMU executable missing; build Personal or provide --qemu' >&2; exit 1
fi
QEMU_DIR=$(CDPATH= cd -- "$(dirname -- "$QEMU")" && pwd)
LIB=$(CDPATH= cd -- "$QEMU_DIR/../lib" && pwd)
SHARE="$QEMU_DIR/../share/qemu"
[ -d "$SHARE" ] || SHARE=/usr/share/qemu
[ -d "$SHARE" ] || { echo 'error: QEMU firmware directory missing' >&2; exit 1; }
if [ "$BOOT" = uefi ]; then
    [ -f "$CODE" ] && [ -f "$VARS" ] || { echo 'error: UEFI requires explicit OVMF code and variable template files' >&2; exit 2; }
elif [ -n "$CODE$VARS" ]; then
    echo 'error: UEFI firmware options require --boot uefi' >&2; exit 2
fi
if [ "$DRY_RUN" -eq 1 ]; then
    printf 'boot: %s (disk/GRUB; no direct kernel)\nnetwork: %s\n' "$BOOT" "$NETWORK"
    echo 'isolation: rootless bubblewrap; TCG; 256M; one CPU'
    echo 'disk and firmware: read-only backing files with temporary snapshots'
    echo 'shared folders/devices/host forwards: none; serial console only'
    exit 0
fi
[ "$(id -u)" -ne 0 ] || { echo 'error: Native QEMU requires an unprivileged user' >&2; exit 2; }
command -v bwrap >/dev/null 2>&1 || { echo 'error: bubblewrap required' >&2; exit 1; }
# Positional arguments hold only fixed internal options and quoted file paths.
set -- --unshare-all --die-with-parent --new-session --clearenv --uid 65534 --gid 65534
if [ "$NETWORK" = user ]; then set -- "$@" --share-net; fi
set -- "$@" --dev /dev --proc /proc --size 134217728 --tmpfs /tmp --chmod 1777 /tmp \
    --dir /var --size 134217728 --tmpfs /var/tmp --chmod 1777 /var/tmp \
    --dir /etc --dir /lib --dir /lib64 --dir /usr --dir /usr/lib \
    --dir /opt --dir /opt/moos-qemu --dir /opt/moos-qemu/bin --dir /opt/moos-qemu/share --dir /moos \
    --ro-bind "$QEMU" /opt/moos-qemu/bin/qemu \
    --ro-bind "$LIB" /opt/moos-qemu/lib --ro-bind "$SHARE" /opt/moos-qemu/share/qemu \
    --ro-bind /lib /lib --ro-bind /lib64 /lib64 --ro-bind /usr/lib /usr/lib \
    --ro-bind-try /usr/lib64 /usr/lib64 --ro-bind "$IMAGE" /moos/native.img
if [ "$NETWORK" = user ]; then set -- "$@" --ro-bind-try /etc/resolv.conf /etc/resolv.conf; fi
if [ "$BOOT" = uefi ]; then
    set -- "$@" --ro-bind "$CODE" /moos/code.fd --ro-bind "$VARS" /moos/vars.fd
fi
set -- "$@" --setenv PATH /usr/bin:/bin --setenv HOME /nonexistent --setenv TMPDIR /tmp \
    --setenv LC_ALL C --setenv QEMU_AUDIO_DRV none /opt/moos-qemu/bin/qemu \
    -M pc -accel tcg -m 256M -smp 1 -nodefaults -no-reboot \
    -drive file=/moos/native.img,if=virtio,snapshot=on,format=raw \
    -nographic -chardev stdio,id=native-console,mux=on,signal=off \
    -serial chardev:native-console -monitor none -L /opt/moos-qemu/share/qemu
if [ "$BOOT" = uefi ]; then
    set -- "$@" -drive if=pflash,format=raw,readonly=on,file=/moos/code.fd \
        -drive if=pflash,format=raw,snapshot=on,file=/moos/vars.fd
fi
if [ "$NETWORK" = user ]; then
    set -- "$@" -netdev user,id=native-net -device virtio-net-pci,netdev=native-net
fi
exec bwrap "$@"
