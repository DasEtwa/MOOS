#!/bin/sh
# Require either a pristine pinned Buildroot tree or exactly the declared patch set.
set -eu

if [ "$#" -ne 2 ]; then
    echo 'usage: prepare-buildroot-tree.sh BUILDROOT_DIR PATCH_DIR' >&2
    exit 2
fi

buildroot_dir=$1
patch_dir=$2

# Passive hash-checked downloads live in dl/. Reusable Git repositories there
# are active inputs: Git can run local hooks/config while refreshing them, before
# Buildroot verifies the resulting source archive. Reject those and any symlink.
dl_dir="$buildroot_dir/dl"
if [ -L "$dl_dir" ]; then
    echo 'error: refusing symlinked Buildroot download directory' >&2
    exit 1
fi
if [ -d "$dl_dir" ]; then
    symlinks=$(find "$dl_dir" -type l -print)
    git_caches=$(find "$dl_dir" -mindepth 2 -maxdepth 2 -type d -name git -print)
    if [ -n "$symlinks" ]; then
        echo 'error: refusing symlinks in Buildroot download cache:' >&2
        printf '%s\n' "$symlinks" >&2
        exit 1
    fi
    if [ -n "$git_caches" ]; then
        echo 'error: refusing reusable Buildroot Git caches:' >&2
        printf '%s\n' "$git_caches" >&2
        exit 1
    fi
fi

# Force quoted paths so newline/control-character paths cannot be split into
# records that look like permitted root-relative download-cache paths.
untracked_inputs=$(
    git -c core.quotePath=true -C "$buildroot_dir" \
        ls-files --others --exclude-standard -- . ':(exclude,top,glob)dl/**' |
        sed '/^dl\//d'
)
ignored_inputs=$(
    git -c core.quotePath=true -C "$buildroot_dir" \
        ls-files --others --ignored --exclude-standard -- . ':(exclude,top,glob)dl/**' |
        sed '/^dl\//d'
)
if [ -n "$untracked_inputs$ignored_inputs" ]; then
    echo 'error: refusing untracked Buildroot inputs:' >&2
    printf '%s\n%s\n' "$untracked_inputs" "$ignored_inputs" >&2
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
