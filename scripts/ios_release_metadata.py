#!/usr/bin/env python3
"""Read and validate the authoritative MOOS Xcode release metadata."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path


EXPECTED_BUNDLE_IDENTIFIER = "dev.moos.shell"
SEMANTIC_VERSION_PATTERN = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
)
TAG_PATTERN = re.compile(
    r"ios-v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
)
BUILD_PATTERN = re.compile(r"[1-9][0-9]*")
OS_VERSION_PATTERN = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\.(0|[1-9][0-9]*))?"
)
CONFIGURATION_PATTERN = re.compile(
    r"[A-Z0-9]+ /\* (?P<configuration>Debug|Release) \*/ = \{\s*"
    r"isa = XCBuildConfiguration;\s*"
    r"buildSettings = \{(?P<settings>.*?)^\s*\};\s*"
    r"name = (?P=configuration);\s*\};",
    re.MULTILINE | re.DOTALL,
)
SETTING_PATTERN = re.compile(r"^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*;\s*$", re.MULTILINE)


class MetadataError(ValueError):
    """Raised when release metadata is missing, ambiguous, or invalid."""


@dataclass(frozen=True)
class ProjectMetadata:
    version: str
    build_version: str
    bundle_identifier: str
    min_os_version: str


def parse_semantic_version(value: str) -> tuple[int, int, int]:
    match = SEMANTIC_VERSION_PATTERN.fullmatch(value)
    if match is None:
        raise MetadataError(f"invalid semantic version: {value!r}")
    return tuple(int(part) for part in match.groups())


def parse_release_tag(tag: str) -> str:
    match = TAG_PATTERN.fullmatch(tag)
    if match is None:
        raise MetadataError(f"invalid iOS release tag: {tag!r}")
    return ".".join(match.groups())


def validate_build_version(value: str) -> int:
    if BUILD_PATTERN.fullmatch(value) is None:
        raise MetadataError(f"build version must be a positive integer: {value!r}")
    return int(value)


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def _parse_settings(block: str) -> dict[str, str]:
    return {key: _unquote(value) for key, value in SETTING_PATTERN.findall(block)}


def load_project_metadata(
    project_path: Path,
    expected_bundle_identifier: str = EXPECTED_BUNDLE_IDENTIFIER,
) -> ProjectMetadata:
    project_text = project_path.read_text(encoding="utf-8")
    app_configurations: dict[str, dict[str, str]] = {}

    for match in CONFIGURATION_PATTERN.finditer(project_text):
        settings = _parse_settings(match.group("settings"))
        if settings.get("PRODUCT_BUNDLE_IDENTIFIER") != expected_bundle_identifier:
            continue

        configuration = match.group("configuration")
        if configuration in app_configurations:
            raise MetadataError(f"duplicate {configuration} app build configuration")
        app_configurations[configuration] = settings

    if set(app_configurations) != {"Debug", "Release"}:
        found = ", ".join(sorted(app_configurations)) or "none"
        raise MetadataError(
            "expected exactly Debug and Release configurations for "
            f"{expected_bundle_identifier}; found {found}"
        )

    required_keys = (
        "MARKETING_VERSION",
        "CURRENT_PROJECT_VERSION",
        "PRODUCT_BUNDLE_IDENTIFIER",
        "IPHONEOS_DEPLOYMENT_TARGET",
    )
    for key in required_keys:
        values = {settings.get(key) for settings in app_configurations.values()}
        if None in values:
            raise MetadataError(f"missing {key} in an app build configuration")
        if len(values) != 1:
            raise MetadataError(f"Debug and Release disagree on {key}: {sorted(values)}")

    release_settings = app_configurations["Release"]
    version = release_settings["MARKETING_VERSION"]
    build_version = release_settings["CURRENT_PROJECT_VERSION"]
    bundle_identifier = release_settings["PRODUCT_BUNDLE_IDENTIFIER"]
    min_os_version = release_settings["IPHONEOS_DEPLOYMENT_TARGET"]

    parse_semantic_version(version)
    validate_build_version(build_version)
    if bundle_identifier != expected_bundle_identifier:
        raise MetadataError(
            f"expected bundle identifier {expected_bundle_identifier}, found {bundle_identifier}"
        )
    if OS_VERSION_PATTERN.fullmatch(min_os_version) is None:
        raise MetadataError(f"invalid iOS deployment target: {min_os_version!r}")

    return ProjectMetadata(
        version=version,
        build_version=build_version,
        bundle_identifier=bundle_identifier,
        min_os_version=min_os_version,
    )


def validate_project_tag(project_path: Path, tag: str) -> ProjectMetadata:
    metadata = load_project_metadata(project_path)
    tag_version = parse_release_tag(tag)
    if metadata.version != tag_version:
        raise MetadataError(
            f"tag version {tag_version} does not match MARKETING_VERSION {metadata.version}"
        )
    return metadata


def select_previous_release_tag(current_version: str, release_tags: list[str]) -> str:
    current = parse_semantic_version(current_version)
    previous: list[tuple[tuple[int, int, int], str]] = []
    for tag in release_tags:
        version = parse_semantic_version(parse_release_tag(tag))
        if version >= current:
            raise MetadataError(
                f"existing release {tag} is not older than ios-v{current_version}"
            )
        previous.append((version, tag))
    return max(previous)[1] if previous else ""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser(
        "validate-project", help="validate project metadata against an ios-v tag"
    )
    validate_parser.add_argument("--project", required=True, type=Path)
    validate_parser.add_argument("--tag", required=True)
    validate_parser.add_argument(
        "--format", choices=("json", "github"), default="json", dest="output_format"
    )

    previous_parser = subparsers.add_parser(
        "select-previous-release",
        help="select the greatest older semantic version from published release tags",
    )
    previous_parser.add_argument("--current-version", required=True)
    previous_parser.add_argument("--tags-file", required=True, type=Path)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        if args.command == "select-previous-release":
            tags = [
                line.strip()
                for line in args.tags_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            print(select_previous_release_tag(args.current_version, tags))
            return 0

        metadata = validate_project_tag(args.project, args.tag)
    except (MetadataError, OSError) as error:
        raise SystemExit(f"release metadata validation failed: {error}") from error

    if args.output_format == "github":
        for key, value in asdict(metadata).items():
            print(f"{key}={value}")
    else:
        print(json.dumps(asdict(metadata), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
