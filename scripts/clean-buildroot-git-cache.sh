#!/bin/sh
# Remove only Buildroot's disposable per-package Git repositories under dl/.
set -eu

if [ "$#" -ne 1 ]; then
    echo 'usage: clean-buildroot-git-cache.sh BUILDROOT_DIR' >&2
    exit 2
fi

buildroot_dir=$1
dl_dir="$buildroot_dir/dl"

if [ -L "$dl_dir" ]; then
    echo 'error: refusing symlinked Buildroot download directory' >&2
    exit 1
fi
[ -d "$dl_dir" ] || exit 0

# Refuse mounted directories before mutation. This stops recursive cleanup from
# crossing a filesystem or bind mount into externally backed content.
# This assumes no privileged process mounts new directories under dl/ during
# cleanup; unprivileged same-user build activity must not mutate dl/ concurrently.
if ! command -v mountpoint >/dev/null 2>&1; then
    echo 'error: mountpoint is required to clean Buildroot Git caches safely' >&2
    exit 1
fi
mounted=$(find "$dl_dir" -type d -exec mountpoint -q -- {} \; -print -quit)
if [ -n "$mounted" ]; then
    echo 'error: refusing to clean through a mounted Buildroot cache directory:' >&2
    printf '%s\n' "$mounted" >&2
    exit 1
fi

# Buildroot stores a reusable VCS checkout at dl/<package>/git/. Symlinks
# within that disposable checkout are legitimate source entries. find -delete
# unlinks them rather than following them and -xdev prevents crossing devices.
# A symlink at the git entry itself is not a directory match and is rejected by
# the post-clean checks.
find "$dl_dir" -mindepth 2 -maxdepth 2 -type d -name git \
    -exec sh -c '
        cache=$1
        if mountpoint -q -- "$cache"; then
            echo "error: refusing mounted Buildroot Git cache: $cache" >&2
            exit 1
        fi
        find "$cache" -xdev -depth -mindepth 1 -delete
        rmdir -- "$cache"
    ' sh {} \;

remaining_git=$(find "$dl_dir" -mindepth 2 -maxdepth 2 -name git -print)
if [ -n "$remaining_git" ]; then
    echo 'error: Buildroot Git cache entry remains after cleanup:' >&2
    printf '%s\n' "$remaining_git" >&2
    exit 1
fi

symlinks=$(find "$dl_dir" -type l -print)
if [ -n "$symlinks" ]; then
    echo 'error: refusing to leave symlinks in the Buildroot download cache:' >&2
    printf '%s\n' "$symlinks" >&2
    exit 1
fi
