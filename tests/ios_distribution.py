#!/usr/bin/env python3
"""Tests for deterministic MOOS iOS release and AltSource metadata."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
PROJECT = REPO_ROOT / "ios" / "MOOSApp" / "MOOSApp.xcodeproj" / "project.pbxproj"
TEMPLATE = REPO_ROOT / "distribution" / "ios" / "source-template.json"
ICON = REPO_ROOT / "distribution" / "ios" / "icon.png"
APP_ICON = (
    REPO_ROOT
    / "ios"
    / "MOOSApp"
    / "MOOSApp"
    / "Resources"
    / "Assets.xcassets"
    / "AppIcon.appiconset"
    / "AppIcon-1024.png"
)
APP_ICON_CONTENTS = APP_ICON.parent / "Contents.json"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ios.yml"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ios-release.yml"
sys.path.insert(0, str(SCRIPTS))


def load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


metadata = load_script("ios_release_metadata", SCRIPTS / "ios_release_metadata.py")
alt_source = load_script("generate_ios_alt_source", SCRIPTS / "generate-ios-alt-source.py")


class ReleaseMetadataTests(unittest.TestCase):
    def test_tag_parsing(self) -> None:
        self.assertEqual(metadata.parse_release_tag("ios-v0.1.1"), "0.1.1")
        self.assertEqual(metadata.parse_release_tag("ios-v12.34.56"), "12.34.56")

    def test_malformed_versions_are_rejected(self) -> None:
        for value in ("0.1", "v0.1.1", "01.1.1", "1.0.0-beta", "1.0.0.0"):
            with self.subTest(value=value):
                with self.assertRaises(metadata.MetadataError):
                    metadata.parse_semantic_version(value)

        for tag in ("v0.1.1", "ios-0.1.1", "ios-v01.1.1", "ios-v1.0"):
            with self.subTest(tag=tag):
                with self.assertRaises(metadata.MetadataError):
                    metadata.parse_release_tag(tag)

    def test_project_metadata_and_bundle_identifier(self) -> None:
        project_metadata = metadata.load_project_metadata(PROJECT)
        self.assertEqual(project_metadata.version, "0.1.3")
        self.assertEqual(project_metadata.build_version, "4")
        self.assertEqual(project_metadata.bundle_identifier, "dev.moos.shell")
        self.assertEqual(project_metadata.min_os_version, "17.0")
        metadata.validate_project_tag(PROJECT, "ios-v0.1.3")

        with self.assertRaises(metadata.MetadataError):
            metadata.validate_project_tag(PROJECT, "ios-v0.1.2")

    def test_invalid_or_inconsistent_build_number_is_rejected(self) -> None:
        original = PROJECT.read_text(encoding="utf-8")
        for replacement in ("0", "01", "abc"):
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory() as directory:
                project = Path(directory) / "project.pbxproj"
                project.write_text(
                    original.replace("CURRENT_PROJECT_VERSION = 4;", f"CURRENT_PROJECT_VERSION = {replacement};"),
                    encoding="utf-8",
                )
                with self.assertRaises(metadata.MetadataError):
                    metadata.load_project_metadata(project)

        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project.pbxproj"
            project.write_text(
                original.replace(
                    "CURRENT_PROJECT_VERSION = 4;",
                    "CURRENT_PROJECT_VERSION = 5;",
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaises(metadata.MetadataError):
                metadata.load_project_metadata(project)

    def test_previous_release_selection_uses_semantic_order(self) -> None:
        self.assertEqual(
            metadata.select_previous_release_tag(
                "1.0.0", ["ios-v0.2.0", "ios-v0.10.0", "ios-v0.9.5"]
            ),
            "ios-v0.10.0",
        )
        self.assertEqual(metadata.select_previous_release_tag("0.1.0", []), "")
        with self.assertRaises(metadata.MetadataError):
            metadata.select_previous_release_tag("0.2.0", ["ios-v0.2.0"])
        with self.assertRaises(metadata.MetadataError):
            metadata.select_previous_release_tag("0.2.0", ["ios-v0.3.0"])


class AltSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.template = json.loads(TEMPLATE.read_text(encoding="utf-8"))

    def generate(
        self,
        base: dict | None = None,
        *,
        version: str = "0.1.0",
        build_version: str = "1",
        ipa_size: int = 123456,
    ) -> dict:
        return alt_source.generate_source(
            self.template if base is None else base,
            version=version,
            build_version=build_version,
            release_date="2026-08-17T12:34:56+00:00",
            ipa_size=ipa_size,
            min_os_version="17.0",
            icon_url="https://example.test/ios/icon.png",
            localized_description=f"MOOS {version} release.",
        )

    def test_required_schema_and_release_url(self) -> None:
        source = self.generate()
        alt_source.validate_source(source)

        app = source["apps"][0]
        self.assertEqual(app["bundleIdentifier"], "dev.moos.shell")
        self.assertEqual(app["developerName"], "DasEtwa")
        self.assertEqual(app["appPermissions"], {"entitlements": [], "privacy": {}})
        release = app["versions"][0]
        self.assertEqual(release["version"], "0.1.0")
        self.assertEqual(release["buildVersion"], "1")
        self.assertEqual(release["size"], 123456)
        self.assertEqual(release["minOSVersion"], "17.0")
        self.assertEqual(
            release["downloadURL"],
            "https://github.com/DasEtwa/MOOS/releases/download/ios-v0.1.0/MOOS.ipa",
        )

    def test_history_is_preserved_newest_first(self) -> None:
        first = self.generate()
        second = self.generate(first, version="0.1.1", build_version="2", ipa_size=234567)
        versions = second["apps"][0]["versions"]
        self.assertEqual([entry["version"] for entry in versions], ["0.1.1", "0.1.0"])
        self.assertEqual([entry["buildVersion"] for entry in versions], ["2", "1"])
        self.assertEqual(versions[1], first["apps"][0]["versions"][0])

    def test_non_monotonic_versions_and_builds_are_rejected(self) -> None:
        first = self.generate()
        with self.assertRaises(alt_source.SourceError):
            self.generate(first, version="0.1.0", build_version="2")
        with self.assertRaises(alt_source.SourceError):
            self.generate(first, version="0.1.1", build_version="1")

    def test_marketplace_and_notarization_fields_are_rejected(self) -> None:
        source = self.generate()
        for field in alt_source.FORBIDDEN_MARKETPLACE_KEYS:
            candidate = copy.deepcopy(source)
            candidate["apps"][0][field] = "forbidden"
            with self.subTest(field=field):
                with self.assertRaises(alt_source.SourceError):
                    alt_source.validate_source(candidate)

    def test_mutable_or_temporary_download_urls_are_rejected(self) -> None:
        source = self.generate()
        rejected_urls = (
            "https://github.com/DasEtwa/MOOS/releases/latest/download/MOOS.ipa",
            "https://api.github.com/repos/DasEtwa/MOOS/actions/artifacts/123/zip",
            "https://example.test/MOOS.ipa",
        )
        for url in rejected_urls:
            candidate = copy.deepcopy(source)
            candidate["apps"][0]["versions"][0]["downloadURL"] = url
            with self.subTest(url=url):
                with self.assertRaises(alt_source.SourceError):
                    alt_source.validate_source(candidate)

    def test_invalid_history_order_is_rejected(self) -> None:
        first = self.generate()
        second = self.generate(first, version="0.1.1", build_version="2")
        second["apps"][0]["versions"].reverse()
        with self.assertRaises(alt_source.SourceError):
            alt_source.validate_source(second)

    def test_output_is_deterministic_and_atomic(self) -> None:
        source = self.generate()
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.json"
            second = Path(directory) / "second.json"
            alt_source.write_json_atomic(first, source)
            alt_source.write_json_atomic(second, source)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertTrue(first.read_bytes().endswith(b"\n"))

    def test_committed_icon_is_valid_and_shared_with_the_app(self) -> None:
        content = ICON.read_bytes()
        self.assertEqual(content, APP_ICON.read_bytes())
        self.assertEqual(content[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(int.from_bytes(content[16:20], "big"), 1024)
        self.assertEqual(int.from_bytes(content[20:24], "big"), 1024)
        self.assertEqual(content[24], 8)
        self.assertEqual(content[25], 2, "app icon must be RGB without alpha")

        catalog = json.loads(APP_ICON_CONTENTS.read_text(encoding="utf-8"))
        self.assertEqual(
            catalog["images"],
            [
                {
                    "filename": "AppIcon-1024.png",
                    "idiom": "universal",
                    "platform": "ios",
                    "size": "1024x1024",
                }
            ],
        )


class ReleaseWorkflowTests(unittest.TestCase):
    def test_normal_ci_cannot_publish(self) -> None:
        workflow = CI_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('branches:\n      - "**"', workflow)
        self.assertIn("pull_request:", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("workflow_call:", workflow)
        self.assertIn("python3 tests/ios_client.py", workflow)
        self.assertIn("python3 tests/ios_distribution.py", workflow)
        self.assertIn("build-for-testing", workflow)
        self.assertIn("test-without-building", workflow)
        self.assertIn("./scripts/build-ios-ipa.sh", workflow)
        self.assertNotIn("contents: write", workflow)
        self.assertNotIn("gh release create", workflow)
        self.assertNotIn("deploy-pages", workflow)

    def test_release_is_tag_only_and_least_privilege(self) -> None:
        workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('tags:\n      - "ios-v*"', workflow)
        self.assertNotIn("pull_request:", workflow)
        self.assertNotIn("pull_request_target:", workflow)
        self.assertNotIn("workflow_dispatch:", workflow)
        self.assertEqual(workflow.count("contents: write"), 1)
        self.assertEqual(workflow.count("pages: write"), 1)
        self.assertEqual(workflow.count("id-token: write"), 1)
        self.assertIn("uses: ./.github/workflows/ios.yml", workflow)
        self.assertIn('[[ "$RELEASE_SHA" != "$DEFAULT_SHA" ]]', workflow)
        self.assertIn("scripts/validate-ios-app.sh", workflow)
        self.assertIn("scripts/generate-ios-alt-source.py", workflow)
        self.assertIn("gh release create", workflow)
        self.assertIn("--generate-notes", workflow)
        self.assertIn("--draft", workflow)
        self.assertIn('"$PAYLOAD_ROOT/MOOS.ipa"', workflow)
        self.assertIn('"$PAYLOAD_ROOT/MOOS-alt-source.json"', workflow)
        self.assertNotIn('gh release upload "$RELEASE_TAG"', workflow)
        self.assertIn('releases/$RELEASE_ID', workflow)
        self.assertIn("-F draft=false", workflow)
        self.assertIn("actual_assets == expected_assets", workflow)
        self.assertIn("actions/configure-pages@v6", workflow)
        self.assertIn("actions/upload-pages-artifact@v5", workflow)
        self.assertIn("actions/deploy-pages@v5", workflow)
        self.assertIn("actions/download-artifact@v8", workflow)
        self.assertNotIn("secrets.", workflow.lower())
        self.assertNotIn("latest/download", workflow)
        self.assertNotIn("actions/artifacts", workflow)


if __name__ == "__main__":
    unittest.main()
