#!/bin/sh

set -eu

usage() {
    echo "usage: $0 --app PATH [--bundle-id ID] [--version VERSION] [--build BUILD]" >&2
}

app_path=
expected_bundle_id=dev.moos.shell
expected_version=
expected_build=

while [ "$#" -gt 0 ]; do
    case "$1" in
        --app)
            [ "$#" -ge 2 ] || { usage; exit 2; }
            app_path=$2
            shift 2
            ;;
        --bundle-id)
            [ "$#" -ge 2 ] || { usage; exit 2; }
            expected_bundle_id=$2
            shift 2
            ;;
        --version)
            [ "$#" -ge 2 ] || { usage; exit 2; }
            expected_version=$2
            shift 2
            ;;
        --build)
            [ "$#" -ge 2 ] || { usage; exit 2; }
            expected_build=$2
            shift 2
            ;;
        *)
            usage
            exit 2
            ;;
    esac
done

[ -n "$app_path" ] || { usage; exit 2; }
[ -d "$app_path" ] || { echo "iOS app bundle not found: $app_path" >&2; exit 1; }

app_binary=$app_path/MOOSApp
info_plist=$app_path/Info.plist
[ -x "$app_binary" ] || { echo "iOS app executable not found: $app_binary" >&2; exit 1; }
[ -f "$info_plist" ] || { echo "iOS app Info.plist not found: $info_plist" >&2; exit 1; }

architectures=$(xcrun lipo -archs "$app_binary")
[ "$architectures" = arm64 ] || {
    echo "expected exactly arm64, found: $architectures" >&2
    exit 1
}

build_info=$(mktemp "${TMPDIR:-/tmp}/moos-device-build.XXXXXX")
trap 'rm -f "$build_info"' EXIT HUP INT TERM
xcrun vtool -show-build "$app_binary" | tee "$build_info"
grep -Eq '^[[:space:]]*platform IOS$' "$build_info" || {
    echo "Mach-O does not target the IOS platform" >&2
    exit 1
}
if grep -Eq '^[[:space:]]*platform IOSSIMULATOR$' "$build_info"; then
    echo "refusing a simulator binary" >&2
    exit 1
fi

platform_name=$(/usr/libexec/PlistBuddy -c 'Print :DTPlatformName' "$info_plist")
bundle_id=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$info_plist")
version=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$info_plist")
build=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$info_plist")

[ "$platform_name" = iphoneos ] || {
    echo "expected DTPlatformName iphoneos, found: $platform_name" >&2
    exit 1
}
[ "$bundle_id" = "$expected_bundle_id" ] || {
    echo "expected CFBundleIdentifier $expected_bundle_id, found: $bundle_id" >&2
    exit 1
}
if [ -n "$expected_version" ] && [ "$version" != "$expected_version" ]; then
    echo "expected CFBundleShortVersionString $expected_version, found: $version" >&2
    exit 1
fi
if [ -n "$expected_build" ] && [ "$build" != "$expected_build" ]; then
    echo "expected CFBundleVersion $expected_build, found: $build" >&2
    exit 1
fi

printf 'Validated iPhoneOS app: bundle=%s version=%s build=%s architecture=%s\n' \
    "$bundle_id" "$version" "$build" "$architectures"
