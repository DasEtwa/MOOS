# MOOS documentation

Current code/tests describe observed behavior. [rules.md](../rules.md) and
production security/protocol/release contracts define permitted behavior.
Acceptance records describe specific observations, not production approval.

| Area | Entry points |
| --- | --- |
| Start and roadmap | [README](../README.md), [STEPS](../STEPS.md), [AGENTS](../AGENTS.md) |
| Native | [Architecture, state and roadmap](native/README.md), [N1 evidence](native/acceptance/N1.md), [N2 evidence](native/acceptance/N2.md) |
| Protocol and Gateway | [Protocol](../PROTOCOL.md), [Gateway](../GATEWAY.md) |
| Host isolation and releases | [Isolation](../HOST_GUEST_ISOLATION.md), [Admin releases](../ADMIN_RELEASES.md), [Onboarding](../HOST_ONBOARDING.md) |
| iOS and distribution | [App](../ios/MOOSApp/README.md), [Distribution contract](../distribution/ios/README.md) |
| Historical context | [Bug audit](../BUG_AUDIT.md), [issue fixes](../ISSUE_FIXES.md) |
| Repository map | [Structure audit](REPOSITORY_STRUCTURE.md) |

Root contracts remain at their existing paths. In particular, the isolation
document is hash-bound to release inputs; immutable fixtures are preserved.
