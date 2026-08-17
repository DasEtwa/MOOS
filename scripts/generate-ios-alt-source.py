#!/usr/bin/env python3
"""Generate and validate the MOOS SideStore-compatible AltSource."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

from ios_release_metadata import (
    MetadataError,
    parse_semantic_version,
    validate_build_version,
)


APP_NAME = "MOOS"
BUNDLE_IDENTIFIER = "dev.moos.shell"
DEVELOPER_NAME = "DasEtwa"
REPOSITORY = "DasEtwa/MOOS"
IPA_ASSET_NAME = "MOOS.ipa"
FORBIDDEN_MARKETPLACE_KEYS = {
    "Build",
    "marketplaceID",
    "marketplaceIdentifier",
    "notarization",
    "notarizationTicket",
    "notarized",
}
DOWNLOAD_URL_PATTERN = re.compile(
    r"https://github\.com/DasEtwa/MOOS/releases/download/"
    r"ios-v(?P<version>(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*))/MOOS\.ipa"
)


class SourceError(ValueError):
    """Raised when an AltSource violates the MOOS distribution contract."""


def construct_download_url(version: str) -> str:
    parse_semantic_version(version)
    tag = quote(f"ios-v{version}", safe="")
    return f"https://github.com/{REPOSITORY}/releases/download/{tag}/{IPA_ASSET_NAME}"


def _require_string(container: dict, key: str, path: str) -> str:
    value = container.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SourceError(f"{path}.{key} must be a non-empty string")
    return value


def _validate_https_url(value: str, path: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise SourceError(f"{path} must be an absolute HTTPS URL")
    if parsed.query or parsed.fragment:
        raise SourceError(f"{path} must not contain a query or fragment")


def _validate_date(value: str, path: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise SourceError(f"{path} must be an ISO 8601 date") from error
    if parsed.tzinfo is None:
        raise SourceError(f"{path} must include a timezone")


def normalize_release_date(value: str) -> str:
    _validate_date(value, "release date")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _reject_marketplace_fields(value: object, path: str = "source") -> None:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            if key in FORBIDDEN_MARKETPLACE_KEYS:
                raise SourceError(f"forbidden marketplace/notarization field at {path}.{key}")
            _reject_marketplace_fields(nested_value, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested_value in enumerate(value):
            _reject_marketplace_fields(nested_value, f"{path}[{index}]")


def validate_source(
    source: dict,
    *,
    allow_empty_versions: bool = False,
    allow_missing_icons: bool = False,
) -> None:
    if not isinstance(source, dict):
        raise SourceError("source must be a JSON object")
    _reject_marketplace_fields(source)

    _require_string(source, "name", "source")
    apps = source.get("apps")
    news = source.get("news")
    if not isinstance(apps, list) or len(apps) != 1:
        raise SourceError("source.apps must contain exactly the MOOS app")
    if not isinstance(news, list):
        raise SourceError("source.news must be an array")

    source_icon = source.get("iconURL")
    if source_icon is None and not allow_missing_icons:
        raise SourceError("source.iconURL is required")
    if source_icon is not None:
        if not isinstance(source_icon, str):
            raise SourceError("source.iconURL must be a string")
        _validate_https_url(source_icon, "source.iconURL")

    app = apps[0]
    if not isinstance(app, dict):
        raise SourceError("source.apps[0] must be an object")
    if _require_string(app, "name", "source.apps[0]") != APP_NAME:
        raise SourceError(f"app name must be {APP_NAME}")
    if _require_string(app, "bundleIdentifier", "source.apps[0]") != BUNDLE_IDENTIFIER:
        raise SourceError(f"bundle identifier must be {BUNDLE_IDENTIFIER}")
    if _require_string(app, "developerName", "source.apps[0]") != DEVELOPER_NAME:
        raise SourceError(f"developer name must be {DEVELOPER_NAME}")
    _require_string(app, "localizedDescription", "source.apps[0]")

    app_icon = app.get("iconURL")
    if app_icon is None and not allow_missing_icons:
        raise SourceError("source.apps[0].iconURL is required")
    if app_icon is not None:
        if not isinstance(app_icon, str):
            raise SourceError("source.apps[0].iconURL must be a string")
        _validate_https_url(app_icon, "source.apps[0].iconURL")
        if source_icon is not None and app_icon != source_icon:
            raise SourceError("source and app icon URLs must match")

    permissions = app.get("appPermissions")
    if not isinstance(permissions, dict):
        raise SourceError("source.apps[0].appPermissions must be an object")
    if not isinstance(permissions.get("entitlements"), list):
        raise SourceError("appPermissions.entitlements must be an array")
    if not isinstance(permissions.get("privacy"), dict):
        raise SourceError("appPermissions.privacy must be an object")

    versions = app.get("versions")
    if not isinstance(versions, list):
        raise SourceError("source.apps[0].versions must be an array")
    if not versions and not allow_empty_versions:
        raise SourceError("source.apps[0].versions must not be empty")

    seen_versions: set[str] = set()
    semantic_versions: list[tuple[int, int, int]] = []
    build_versions: list[int] = []
    for index, version_entry in enumerate(versions):
        path = f"source.apps[0].versions[{index}]"
        if not isinstance(version_entry, dict):
            raise SourceError(f"{path} must be an object")

        version = _require_string(version_entry, "version", path)
        try:
            semantic_version = parse_semantic_version(version)
        except MetadataError as error:
            raise SourceError(f"{path}.version is invalid: {version!r}") from error
        if version in seen_versions:
            raise SourceError(f"duplicate app version: {version}")
        seen_versions.add(version)
        semantic_versions.append(semantic_version)

        build_version = _require_string(version_entry, "buildVersion", path)
        try:
            build_versions.append(validate_build_version(build_version))
        except MetadataError as error:
            raise SourceError(f"{path}.buildVersion is invalid: {build_version!r}") from error

        release_date = _require_string(version_entry, "date", path)
        _validate_date(release_date, f"{path}.date")

        download_url = _require_string(version_entry, "downloadURL", path)
        _validate_https_url(download_url, f"{path}.downloadURL")
        match = DOWNLOAD_URL_PATTERN.fullmatch(download_url)
        if match is None or match.group("version") != version:
            raise SourceError(
                f"{path}.downloadURL must be the versioned MOOS GitHub Release asset"
            )

        size = version_entry.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise SourceError(f"{path}.size must be a positive byte count")

        min_os_version = _require_string(version_entry, "minOSVersion", path)
        if re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:\."
                        r"(?:0|[1-9][0-9]*))?", min_os_version) is None:
            raise SourceError(f"{path}.minOSVersion is invalid")

    if semantic_versions != sorted(semantic_versions, reverse=True):
        raise SourceError("app versions must be newest first")
    if len(set(semantic_versions)) != len(semantic_versions):
        raise SourceError("app semantic versions must be unique")
    if build_versions != sorted(build_versions, reverse=True):
        raise SourceError("app build versions must be newest first")
    if len(set(build_versions)) != len(build_versions):
        raise SourceError("app build versions must be unique")


def generate_source(
    base_source: dict,
    *,
    version: str,
    build_version: str,
    release_date: str,
    ipa_size: int,
    min_os_version: str,
    icon_url: str,
    localized_description: str,
) -> dict:
    parse_semantic_version(version)
    new_build = validate_build_version(build_version)
    if isinstance(ipa_size, bool) or ipa_size <= 0:
        raise SourceError("IPA size must be a positive integer")
    _validate_https_url(icon_url, "icon URL")
    description = localized_description.strip()
    if not description:
        raise SourceError("release description must not be empty")
    if len(description) > 10_000:
        raise SourceError("release description exceeds 10000 characters")

    source = copy.deepcopy(base_source)
    existing_versions = source.get("apps", [{}])[0].get("versions", [])
    validate_source(
        source,
        allow_empty_versions=not existing_versions,
        allow_missing_icons=not existing_versions,
    )

    app = source["apps"][0]
    source["iconURL"] = icon_url
    app["iconURL"] = icon_url
    versions = app["versions"]

    new_semantic_version = parse_semantic_version(version)
    if versions:
        latest_version = parse_semantic_version(versions[0]["version"])
        if new_semantic_version <= latest_version:
            raise SourceError(
                f"new version {version} must be newer than {versions[0]['version']}"
            )
        highest_build = max(int(entry["buildVersion"]) for entry in versions)
        if new_build <= highest_build:
            raise SourceError(
                f"new build {build_version} must be greater than previous build {highest_build}"
            )

    versions.insert(
        0,
        {
            "version": version,
            "buildVersion": build_version,
            "date": normalize_release_date(release_date),
            "localizedDescription": description,
            "downloadURL": construct_download_url(version),
            "size": ipa_size,
            "minOSVersion": min_os_version,
        },
    )
    validate_source(source)
    return source


def write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise SourceError(f"{path} must contain a JSON object")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser("generate")
    generate_parser.add_argument("--base-source", required=True, type=Path)
    generate_parser.add_argument("--output", required=True, type=Path)
    generate_parser.add_argument("--version", required=True)
    generate_parser.add_argument("--build-version", required=True)
    generate_parser.add_argument("--release-date", required=True)
    generate_parser.add_argument("--ipa-size", required=True, type=int)
    generate_parser.add_argument("--min-os-version", required=True)
    generate_parser.add_argument("--icon-url", required=True)
    generate_parser.add_argument("--release-notes-file", required=True, type=Path)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("source", type=Path)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        if args.command == "validate":
            validate_source(_load_json(args.source))
            print(f"AltSource validation passed: {args.source}")
            return 0

        source = generate_source(
            _load_json(args.base_source),
            version=args.version,
            build_version=args.build_version,
            release_date=args.release_date,
            ipa_size=args.ipa_size,
            min_os_version=args.min_os_version,
            icon_url=args.icon_url,
            localized_description=args.release_notes_file.read_text(encoding="utf-8"),
        )
        write_json_atomic(args.output, source)
        print(f"Generated SideStore AltSource: {args.output}")
        return 0
    except (MetadataError, SourceError, OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"AltSource generation failed: {error}") from error


if __name__ == "__main__":
    raise SystemExit(main())
