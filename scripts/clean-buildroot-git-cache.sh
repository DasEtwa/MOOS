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

symlinks=$(find "$dl_dir" -type l -print)
if [ -n "$symlinks" ]; then
    echo 'error: refusing to clean a Buildroot download tree containing symlinks:' >&2
    printf '%s\n' "$symlinks" >&2
    exit 1
fi

# Buildroot stores a reusable VCS checkout at dl/<package>/git/. Never follow
# symlinks and never remove package archives or other download-cache content.
find "$dl_dir" -mindepth 2 -maxdepth 2 -type d -name git -prune \
    -exec rm -rf -- {} +
