#!/bin/sh
# Require either a pristine pinned Buildroot tree or exactly the declared patch set.
set -eu

if [ "$#" -ne 2 ]; then
    echo 'usage: prepare-buildroot-tree.sh BUILDROOT_DIR PATCH_DIR' >&2
    exit 2
fi

buildroot_dir=$1
patch_dir=$2

# Buildroot's hash-verified source cache lives in dl/. It is the only allowed
# untracked source-tree content; out-of-tree build output lives elsewhere.
if [ -n "$(git -C "$buildroot_dir" ls-files --others --exclude-standard -- . ':(exclude)dl/**')$(git -C "$buildroot_dir" ls-files --others --ignored --exclude-standard -- . ':(exclude)dl/**')" ]; then
    echo 'error: refusing untracked Buildroot inputs' >&2
    exit 1
fi

git -C "$buildroot_dir" diff --cached --quiet HEAD -- || {
    echo 'error: refusing staged Buildroot changes' >&2
    exit 1
}

if git -C "$buildroot_dir" diff --quiet --; then
    for patch in "$patch_dir"/*.patch; do
        [ -f "$patch" ] || continue
        git -C "$buildroot_dir" apply --check "$patch" || {
            echo "error: Buildroot patch cannot be applied: $patch" >&2
            exit 1
        }
        git -C "$buildroot_dir" apply "$patch"
    done
    exit 0
fi

expected_index=$(mktemp)
rm -f "$expected_index"
trap 'rm -f "$expected_index"' EXIT HUP INT TERM
GIT_INDEX_FILE=$expected_index git -C "$buildroot_dir" read-tree HEAD
for patch in "$patch_dir"/*.patch; do
    [ -f "$patch" ] || continue
    GIT_INDEX_FILE=$expected_index git -C "$buildroot_dir" apply --cached --check "$patch" || {
        echo "error: declared Buildroot patch is invalid at pinned HEAD: $patch" >&2
        exit 1
    }
    GIT_INDEX_FILE=$expected_index git -C "$buildroot_dir" apply --cached "$patch"
done
GIT_INDEX_FILE=$expected_index git -C "$buildroot_dir" diff --quiet -- || {
    echo 'error: Buildroot tracked changes differ from the declared patch set' >&2
    exit 1
}
