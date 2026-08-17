#!/usr/bin/env python3
"""Validate the native client structure without requiring Xcode on Linux."""

from pathlib import Path
from xml.etree import ElementTree


REPO_ROOT = Path(__file__).resolve().parents[1]
IOS_ROOT = REPO_ROOT / "ios" / "MOOSApp"
PROJECT = IOS_ROOT / "MOOSApp.xcodeproj" / "project.pbxproj"
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
    }
    assert component_sources <= {source.name for source in swift_sources}

    forbidden_client_details = (
        "qemu",
        "systemd",
        "bubblewrap",
        "/var/lib/moos",
        "console.sock",
    )
    combined_sources = "\n".join(source.read_text().lower() for source in swift_sources)
    for detail in forbidden_client_details:
        assert detail not in combined_sources, f"client leaks host detail: {detail}"

    assert "longpressgesture" in combined_sources
    assert ".contextmenu" in combined_sources
    assert "timelineview" in combined_sources
    assert "mock controls" in combined_sources

    assert not list(IOS_ROOT.rglob("Package.resolved")), "unexpected dependency lockfile"
    print("MOOS iOS client structure test: PASS")
    print(f"  Swift sources referenced by project: {len(swift_sources)}")
    print("  host implementation details absent from client: ok")
    print("  third-party dependencies: none")


if __name__ == "__main__":
    main()
