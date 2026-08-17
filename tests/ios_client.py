#!/usr/bin/env python3
"""Validate the native client structure without requiring Xcode on Linux."""

from pathlib import Path
from xml.etree import ElementTree


REPO_ROOT = Path(__file__).resolve().parents[1]
IOS_ROOT = REPO_ROOT / "ios" / "MOOSApp"
PROJECT = IOS_ROOT / "MOOSApp.xcodeproj" / "project.pbxproj"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ios.yml"
DEVICE_BUILDER = REPO_ROOT / "scripts" / "build-ios-ipa.sh"
DEVICE_VALIDATOR = REPO_ROOT / "scripts" / "validate-ios-app.sh"
SCHEME = (
    IOS_ROOT
    / "MOOSApp.xcodeproj"
    / "xcshareddata"
    / "xcschemes"
    / "MOOSApp.xcscheme"
)


def main() -> None:
    assert PROJECT.is_file(), "Xcode project is missing"
    project_text = PROJECT.read_text()
    assert "IPHONEOS_DEPLOYMENT_TARGET = 17.0" in project_text
    assert "PRODUCT_BUNDLE_IDENTIFIER = dev.moos.shell" in project_text
    assert project_text.count("{") == project_text.count("}"), "unbalanced project"
    ElementTree.parse(SCHEME)

    swift_sources = sorted(IOS_ROOT.rglob("*.swift"))
    assert swift_sources, "iOS sources are missing"
    for source in swift_sources:
        assert source.name in project_text, f"project does not reference {source.name}"
        assert f"{source.name} in Sources" in project_text, (
            f"project does not compile {source.name}"
        )

    component_sources = {
        "AppGridView.swift",
        "WidgetGridView.swift",
        "RadialMenuView.swift",
        "PowerMenuView.swift",
        "SystemBarView.swift",
        "ConnectionBannerView.swift",
        "LocalShellPreferences.swift",
        "RemoteMetadataCache.swift",
        "LiveMOOSStateProviding.swift",
        "MOOSProtocolV1.swift",
    }
    assert component_sources <= {source.name for source in swift_sources}

    forbidden_client_details = (
        "qemu",
        "systemd",
        "bubblewrap",
        "/var/lib/moos",
        "console.sock",
        "urlsession",
        "nwconnection",
    )
    combined_sources = "\n".join(source.read_text().lower() for source in swift_sources)
    for detail in forbidden_client_details:
        assert detail not in combined_sources, f"client leaks host detail: {detail}"

    assert "longpressgesture" in combined_sources
    assert ".contextmenu" in combined_sources
    assert "timelineview" in combined_sources
    assert "mock controls" in combined_sources
    assert "asyncstream" in combined_sources
    assert "contenthash" in combined_sources
    assert "options: .atomic" in combined_sources
    assert "cached desktop remains available" in combined_sources
    assert "static let version = 1" in combined_sources

    assert not list(IOS_ROOT.rglob("Package.resolved")), "unexpected dependency lockfile"

    workflow_text = WORKFLOW.read_text()
    device_builder_text = DEVICE_BUILDER.read_text()
    device_validator_text = DEVICE_VALIDATOR.read_text()
    for requirement in (
        "runs-on: macos-15",
        "/Applications/Xcode_16.4.app/Contents/Developer",
        "actions/checkout@v6",
        "persist-credentials: false",
        "python3 tests/ios_client.py",
        "CODE_SIGNING_ALLOWED=NO",
        "build-for-testing",
        "test-without-building",
    ):
        assert requirement in workflow_text, f"iOS CI is missing {requirement}"

    for device_requirement in (
        "build-unsigned-device:",
        "./scripts/build-ios-ipa.sh",
        "MOOS.ipa",
        "actions/upload-artifact@v7",
        "MOOS-unsigned-iphoneos-arm64",
    ):
        assert device_requirement in workflow_text, (
            f"iOS device CI is missing {device_requirement}"
        )

    for build_requirement in (
        "-sdk iphoneos",
        "-destination 'generic/platform=iOS'",
        "Release-iphoneos/MOOSApp.app",
        "ARCHS=arm64",
        "CODE_SIGNING_ALLOWED=NO",
        "Payload/MOOSApp.app",
    ):
        assert build_requirement in device_builder_text, (
            f"canonical IPA builder is missing {build_requirement}"
        )

    for validation_requirement in (
        "xcrun lipo -archs",
        "xcrun vtool -show-build",
        "platform IOS$",
        "platform IOSSIMULATOR$",
        "DTPlatformName",
        "CFBundleIdentifier",
        "CFBundleShortVersionString",
        "CFBundleVersion",
    ):
        assert validation_requirement in device_validator_text, (
            f"canonical iOS validator is missing {validation_requirement}"
        )
    assert "secrets." not in workflow_text.lower()
    assert "brew " not in workflow_text.lower()

    print("MOOS iOS client structure test: PASS")
    print(f"  Swift sources referenced by project: {len(swift_sources)}")
    print("  host implementation details absent from client: ok")
    print("  third-party dependencies: none")
    print("  unsigned simulator and arm64 iPhoneOS CI: configured")


if __name__ == "__main__":
    main()
