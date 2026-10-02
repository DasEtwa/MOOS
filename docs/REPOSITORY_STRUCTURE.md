# N2 conservative structure audit

The audit preceded documentation moves. Classification is for navigation;
it does not change normative authority or turn historical evidence into policy.

| Class | Inventory | Treatment in N2 |
| --- | --- | --- |
| A — normative/security/release | `rules.md`, `AGENTS.md`, `PROTOCOL.md`, `GATEWAY.md`, `HOST_GUEST_ISOLATION.md`, `ADMIN_RELEASES.md`, `distribution/ios/README.md` | Retain paths and contract bytes |
| B — active product/operations | `README.md`, `STEPS.md`, `HOST_ONBOARDING.md`, `ios/MOOSApp/README.md`, Native architecture | Root discovery remains; Native architecture moves from `NATIVE.md` to `docs/native/README.md` |
| C — acceptance | Native N1 dated record; new N2 evidence | N1 moves from `NATIVE_ACCEPTANCE.md` to `docs/native/acceptance/N1.md`; its observations remain historical. N2 is a separate record |
| D — historical/audit | `BUG_AUDIT.md`, `ISSUE_FIXES.md`, dated evidence embedded in STEPS | Retain paths; index them without rewriting history |
| E — implementation | `configs/`, `scripts/`, `system/`, `host/` (including Rust Gateway), `systemd/`, `ios/`, `patches/`, `tests/`, workflows, Cargo inputs | No aesthetic code/test moves |

`distribution/ios/` already groups publication contracts and tooling.
`tests/fixtures/` holds externally signed/public fixture inputs; the administrator
release includes exact member paths, and the source manifest pins selected
contract bytes. No manifest, trust anchor, fixture, signature, or release member
is regenerated or relocated here. Moving root security contracts, old audits
with external links, or broad line-ending changes requires a separate review.

Generated/ignored trees include `buildroot/`, `output/` (including Native),
`host-tools/`, `dl/`, Cargo `target/`, image products, logs/caches and runtime
state. They remain generated output. `.gitignore` covers these trees and local
credentials; no image, firmware, toolchain, log or signing key enters this PR.
The generated `native-data.ext4` is under ignored `output/`, not source.

Native disk and persistent-state behavior is separated from this navigation
cleanup in commits. [The documentation index](README.md) and a cheap local-link
check cover the moved entry points. No test/import/script paths are reorganized.
