# MOOS iOS distribution

This directory contains the repository-owned metadata for the unsigned MOOS
iOS release channel. GitHub builds and hosts the unsigned app and its update
metadata; SideStore remains responsible for signing, installation, refresh,
and update installation on the iPhone.

No Apple certificate, provisioning profile, Apple account, signing secret, or
private key belongs in this repository or in GitHub Actions.

## Release architecture

Normal pushes, pull requests, and manual runs execute only
`.github/workflows/ios.yml`. That reusable workflow keeps its existing
simulator build and unit tests, validates the client boundary and distribution
tools, and uses `scripts/build-ios-ipa.sh` for the one canonical unsigned
physical-device packaging path. Its device artifact remains
`MOOS-unsigned-iphoneos-arm64`.

Only a pushed tag matching `ios-v*` starts `.github/workflows/ios-release.yml`.
The release workflow:

1. requires the tag to be exactly at the current default-branch tip;
2. requires its version to match the Xcode `MARKETING_VERSION`;
3. runs the complete reusable iOS CI, including simulator tests;
4. downloads that run's canonical unsigned `MOOS.ipa` artifact;
5. rechecks the app identifier, version, build, unsigned state, iPhoneOS
   platform, and exact arm64 architecture;
6. generates and validates a SideStore-compatible AltSource while preserving
   the previous source history from the latest semantic iOS Release;
7. creates the immutable GitHub Release with `MOOS.ipa` and
   `MOOS-alt-source.json`; and
8. deploys the stable `ios/source.json` and `ios/icon.png` through GitHub
   Pages.

All preparation jobs have read-only repository access. Only the Release job
has `contents: write`. The Pages deployment has only `pages: write` and
`id-token: write`; no pull-request workflow receives write access.

GitHub restricts Release creation when the target differs from the current
default branch in workflow-file content. Requiring the tag SHA to be the exact
default-branch tip both avoids that limitation and prevents a release from an
unmerged branch. The workflow checks this once before CI and again immediately
before publication.

## One-time GitHub Pages setting

Before the first release, open **Repository Settings → Pages → Build and
deployment** and set **Source** to **GitHub Actions**. No external host, PAT,
custom domain, or Pages branch is required.

Pages is not enabled for `DasEtwa/MOOS` at the time this documentation was
written, so its final public base URL must not be guessed. After enabling Pages,
retrieve GitHub's authoritative URL with:

```bash
PAGES_BASE_URL="$(gh api repos/DasEtwa/MOOS/pages --jq .html_url)"
SOURCE_URL="${PAGES_BASE_URL%/}/ios/source.json"
printf '%s\n' "$SOURCE_URL"
python3 - "$SOURCE_URL" <<'PY'
import sys
import urllib.parse

print("sidestore://source?" + urllib.parse.urlencode({"url": sys.argv[1]}))
PY
```

The first successful release workflow also prints both exact values in its job
summary. The stable source will not exist until that first Pages deployment.

## Version bump and first release

Xcode project metadata is authoritative. In the `MOOSApp` target's **General →
Identity** settings, update both values for every app build configuration:

- **Version** / `MARKETING_VERSION`: semantic `MAJOR.MINOR.PATCH`, for example
  `0.1.1`.
- **Build** / `CURRENT_PROJECT_VERSION`: a positive integer greater than every
  published build, for example `2`.

For the first update from `0.1.0` build `1` to `0.1.1` build `2`, commit the
project change and let normal CI pass on the default branch before tagging:

```bash
python3 scripts/ios_release_metadata.py validate-project \
  --project ios/MOOSApp/MOOSApp.xcodeproj/project.pbxproj \
  --tag ios-v0.1.1

git add ios/MOOSApp/MOOSApp.xcodeproj/project.pbxproj
git commit -m "chore(ios): bump version to 0.1.1"
git push origin main

# Wait for the normal iOS workflow on this exact commit to pass.
git tag ios-v0.1.1
git push origin ios-v0.1.1
```

Do not tag a feature branch, reuse a published tag, silently rewrite Xcode
versions in CI, or move a tag after a Release exists. Avoid advancing `main`
while the short release run is publishing because the final tip check is
deliberately fail-closed.

After success, the human-facing Release and stable asset are at:

```text
https://github.com/DasEtwa/MOOS/releases/tag/ios-v0.1.1
https://github.com/DasEtwa/MOOS/releases/download/ios-v0.1.1/MOOS.ipa
```

The first URL changes per release page and the second is the immutable URL
stored in the AltSource. GitHub Actions artifact URLs are temporary and are
never written into update metadata.

## SideStore updates and rollback

Add the exact `sidestore://source?url=...` URI produced above to SideStore once.
Each later iOS tag prepends its version to the source history, and SideStore can
then discover the newer version without manually moving an IPA file. SideStore
and its LocalDevVPN requirements remain device-side concerns; GitHub does not
sign, install, or refresh the app.

Release assets and tags are treated as immutable. To roll back bad application
behavior for the update channel, revert the code and publish a new, higher
marketing version and build number. An older Release IPA remains available for
manual recovery, but lowering the newest source version would make update
ordering ambiguous and is intentionally rejected.

If the Release succeeds but the Pages deployment fails, use GitHub Actions'
**Re-run failed jobs** operation. The successfully prepared Pages artifact and
immutable Release do not need to be replaced.

## AltSource maintenance

`source-template.json` is the empty seed and is not the live source. The
release workflow takes the previous Release's `MOOS-alt-source.json` when one
exists, prepends the new version, validates it, publishes the snapshot as a
Release asset, and deploys the same JSON to Pages. `icon.png` is committed and
its deterministic bytes are covered by:

```bash
python3 tests/ios_distribution.py
```

The format was checked on 2026-08-17 against SideStore's current
[app-source documentation](https://docs.sidestore.io/docs/advanced/app-sources),
[URL scheme](https://docs.sidestore.io/docs/advanced/url-schema), current
[Classic source decoder](https://github.com/SideStore/SideStore/blob/e3f3a5b941ce657723a4939c89f2eea63bcfe263/AltStore/Core/Model/Source.swift),
and AltStore's official
[source guide](https://github.com/altstoreio/FAQ/blob/14e5b63aac7900496ddd783618c51c970c84186c/developers/make-a-source.md).
The generator implements that Classic AltSource shape. It rejects malformed or
non-monotonic versions/builds, mutable or Actions-artifact download URLs, and
marketplace/notarization fields. MOOS is a sideloaded app, not an
Apple-notarized marketplace app.
