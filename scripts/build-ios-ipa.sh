#!/bin/sh

set -eu

usage() {
    echo "usage: $0 --output IPA_PATH --derived-data PATH" >&2
}

output_path=
derived_data=

while [ "$#" -gt 0 ]; do
    case "$1" in
        --output)
            [ "$#" -ge 2 ] || { usage; exit 2; }
            output_path=$2
            shift 2
            ;;
        --derived-data)
            [ "$#" -ge 2 ] || { usage; exit 2; }
            derived_data=$2
            shift 2
            ;;
        *)
            usage
            exit 2
            ;;
    esac
done

[ -n "$output_path" ] || { usage; exit 2; }
[ -n "$derived_data" ] || { usage; exit 2; }

case "$output_path" in
    /*) ;;
    *) output_path=$PWD/$output_path ;;
esac
case "$derived_data" in
    /*) ;;
    *) derived_data=$PWD/$derived_data ;;
esac

output_directory=$(dirname "$output_path")
[ -d "$output_directory" ] || {
    echo "IPA output directory does not exist: $output_directory" >&2
    exit 1
}
[ ! -e "$output_path" ] || {
    echo "refusing to overwrite existing IPA: $output_path" >&2
    exit 1
}

script_directory=$(CDPATH= cd "$(dirname "$0")" && pwd)
repository_root=$(CDPATH= cd "$script_directory/.." && pwd)
project_path=$repository_root/ios/MOOSApp/MOOSApp.xcodeproj

xcodebuild \
    -project "$project_path" \
    -scheme MOOSApp \
    -configuration Release \
    -sdk iphoneos \
    -destination 'generic/platform=iOS' \
    -derivedDataPath "$derived_data" \
    ARCHS=arm64 \
    ONLY_ACTIVE_ARCH=NO \
    CODE_SIGNING_ALLOWED=NO \
    CODE_SIGNING_REQUIRED=NO \
    CODE_SIGN_IDENTITY="" \
    COMPILER_INDEX_STORE_ENABLE=NO \
    build

app_path=$derived_data/Build/Products/Release-iphoneos/MOOSApp.app
"$script_directory/validate-ios-app.sh" --app "$app_path"

package_root=$(mktemp -d "${TMPDIR:-/tmp}/moos-ipa.XXXXXX")
trap 'rm -rf "$package_root"' EXIT HUP INT TERM
mkdir -p "$package_root/Payload"
cp -R "$app_path" "$package_root/Payload/MOOSApp.app"
(
    cd "$package_root"
    /usr/bin/zip -qry "$output_path" Payload
)

/usr/bin/unzip -t "$output_path"
ipa_contents=$package_root/ipa-contents.txt
/usr/bin/unzip -Z1 "$output_path" > "$ipa_contents"
grep -Fxq 'Payload/MOOSApp.app/MOOSApp' "$ipa_contents"

printf 'Packaged unsigned iPhoneOS IPA: %s\n' "$output_path"
