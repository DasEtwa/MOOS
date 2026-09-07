# MOOS rules

Rules version: 2.0
Status: normative project constitution

These rules define the project-wide security, trust, architecture, release,
quality, and evolution boundaries for MOOS.

`AGENTS.md` is the operating manual for coding agents.
Implementation documents such as `PROTOCOL.md`, `GATEWAY.md`,
`HOST_GUEST_ISOLATION.md`, `ADMIN_RELEASES.md`, and distribution contracts
define more specific behavior within these rules.

Specific contracts may strengthen these rules.
They must not silently weaken them.

---

## 1. How to interpret these rules

The words **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT**, and **MAY** are
intentional.

- **MUST / MUST NOT** define hard project boundaries.
- **SHOULD / SHOULD NOT** define the normal design direction. Deviations need
  a concrete reason and appropriate verification.
- **MAY** describes an allowed choice, not a requirement.

Security rules are constraints, not implementation instructions.

These rules deliberately define the safe envelope without fixing every future
implementation detail.

A missing implementation detail is not automatically an architecture blocker.

---

## 2. Normative authority and implementation truth

Keep two questions separate.

### What may MOOS do?

Normative authority is:

    rules.md
        ↓
    security / protocol / release / isolation contracts
        ↓
    implementation

Code or tests that violate a normative boundary are defective.
Existing behavior does not automatically legitimize itself.

### What does MOOS currently do?

Observed implementation truth comes from:

    current code + tests
        ↓
    production documentation
        ↓
    STEPS.md
        ↓
    durable project context
        ↓
    historical discussion or assumptions

A roadmap item, issue, design proposal, old test, or previous implementation is
not permission to cross a current security boundary.

`rules.md` MUST NOT be weakened merely to make an existing implementation,
test, or release process pass.

---

## 3. Progress rule — security must enable safe progress

MOOS security must fail closed at trust boundaries without turning every
unfinished prerequisite into a project-wide stop condition.

An unavailable external prerequisite blocks only the action or acceptance claim
that actually depends on it.

Examples include:

- a publisher release key;
- an authenticated package repository;
- root access on a real acceptance Host;
- physical iPhone acceptance;
- hardware-specific evidence;
- publication credentials.

Their absence MUST NOT by itself prevent work on:

- deterministic unsigned artifacts;
- package layouts;
- verification code;
- bootstrap contracts;
- public-key handling;
- externally signed test fixtures;
- failure and tamper tests;
- install transaction logic;
- recovery logic;
- documentation;
- other code that can be safely verified before that external boundary.

If the task is specifically to implement a missing prerequisite, the fact that
the prerequisite does not exist yet is not a reason to stop.

Work MUST stop only when continuing would require one of the following:

1. violating a MUST or MUST NOT rule;
2. inventing trust that cannot actually be established;
3. broadening privilege or authority without a defined security model;
4. destroying or replacing user state without explicit authorization;
5. falsely claiming verification that was not performed;
6. making an irreversible core trust decision when multiple materially
   different security models remain unresolved.

When several safe implementations satisfy the rules, prefer the smallest,
reversible, testable baseline rather than stopping for an arbitrary preference
decision.

A safe implementation MAY be completed while external acceptance remains
explicitly pending.

---

## 4. The Host is the primary security boundary

> **MOOS Host controls Instances. Instances never control the MOOS Host.**

Guest root is never Host root.

An Instance MUST NOT automatically gain access to Host:

- files;
- processes;
- devices;
- sockets;
- credentials;
- user home directories;
- repositories;
- package managers;
- system services;
- clipboard;
- GPU;
- USB;
- IPC;
- other Host capabilities.

Capabilities crossing the Host/Guest boundary MUST be:

- explicit;
- narrowly scoped;
- authenticated where identity matters;
- authorized where authority matters;
- validated;
- revocable where persistent;
- deny-by-default.

Future capabilities such as GPU, USB, shared folders, networking, clipboard, or
audio are not forbidden.

They MUST be introduced as explicit capabilities with their own permission and
isolation model instead of becoming implicit Host access.

The Host MUST remain the only external control entry point. Instances MUST NOT
run Tailscale. They MUST NOT expose public guest control ports by default.

A Guest MUST NOT need to know whether a client reaches the Host through LAN,
Tailscale, peer-to-peer, relay, or another replaceable transport.

---

## 5. No arbitrary Host execution

MOOS MUST NOT expose a generic Host shell or arbitrary Host-command API.

Interfaces such as:

    exec(command)
    runShell(string)
    POST /exec?cmd=...

are forbidden as general Host control surfaces.

Host operations MUST use bounded, typed capabilities such as:

    status
    start
    stop

and similarly narrow future operations.

A future capability may internally invoke a fixed program when needed, but
externally controlled input MUST NOT become unrestricted command execution.

The Host and Guest command authorities MUST remain distinct.

---

## 6. Runtime isolation

Normal MOOS Instance execution MUST use an unprivileged dedicated runtime
identity.

The runtime identity MUST:

- not be root;
- have a non-login shell;
- have a locked account credential;
- have no unnecessary supplemental groups;
- receive only the Host access required by the runtime contract.

Host resource limits MUST be applied to normal Instance execution and enforced
outside the guest.

At minimum, normal Instance execution MUST bound:

- CPU;
- memory;
- task/process count;
- I/O.

Guest-local reporting is not a substitute for Host enforcement.

An Instance OOM or resource-exhaustion failure MUST be contained instead of
becoming a Host OOM or taking down unrelated Instances and services.

The default runtime SHOULD remain rootless and deny-by-default.

Device passthrough, networking, acceleration, mounts, or other expanded
capabilities MAY be added later, but MUST be explicit and separately reviewed.

---

## 7. Instance lifecycle and persistence

An Instance MUST be containable.

An Instance MUST be disposable: it must be deletable and recreatable without
damaging the Host or unrelated Instances.

A crash or failure in one Instance MUST NOT normally crash:

- another Instance;
- `moosd`;
- the Gateway;
- the Host.

Persistent state and transient runtime state MUST remain conceptually separate.

Persistent state may include:

- Instance data;
- images;
- configuration;
- stable identity.

Instances MUST use stable identifiers rather than IP addresses as identity.
Network addresses are replaceable transport details.

Transient state includes things such as:

- PIDs;
- temporary sockets;
- runtime handles;
- ephemeral ports;
- process-local state.

Snapshots are not backups.

Critical Host configuration MUST NOT exist only inside a guest.

Important updates, configuration changes, and metadata changes MUST be atomic
and recoverable. They SHOULD use write, validate, and atomic replacement or an
equivalent transaction appropriate to the storage boundary.

---

## 8. Privileged code never comes from an untrusted checkout

A developer checkout is untrusted input for privileged execution.

MOOS MUST NOT instruct or require a user to run repository code directly as
root, including patterns such as:

    sudo ./scripts/...
    sudo python3 ...
    sudo <program-from-checkout>

A privileged installer, coordinator, helper, or updater MUST originate from an
independently authenticated installed trust boundary.

Privileged code MUST NOT:

- import executable code from the developer checkout;
- execute checkout scripts;
- trust checkout ownership as authenticity;
- trust a checksum supplied by the same untrusted source;
- accept an arbitrary executable path from an unprivileged caller.

Privileged operations SHOULD use fixed installed entry points, bounded inputs,
clean execution environments, and protected storage.

---

## 9. Trusted bootstrap

The first trusted installation cannot derive its own authenticity solely from
the untrusted MOOS checkout it is installing.

The initial trust source MUST be authenticated independently of that checkout.

Valid future mechanisms may include:

- an authenticated operating-system package channel;
- another independently authenticated administrator-controlled distribution;
- another reviewed mechanism that establishes equivalent provenance.

No specific package format or Linux distribution is permanently mandated by
these rules.

MOOS MAY deliberately support one narrow Host baseline first.

A first supported baseline SHOULD be fully tested before claiming broader
distribution support.

A local `.deb`, archive, checksum, public key, or installer copied from the same
untrusted checkout does not by itself establish bootstrap authenticity.

---

## 10. Release-signing private-key boundary

The MOOS release private signing key MUST NEVER exist inside:

- the MOOS repository;
- any MOOS checkout or worktree;
- the project directory;
- a build tree;
- generated-output directories;
- repository-controlled temporary directories;
- repository CI;
- a normal Host installation;
- any location controlled by normal MOOS checkout tooling.

This applies even when the path is ignored by Git.

MOOS checkout tooling MUST NOT:

- generate a release private signing key;
- accept a release private-key path;
- discover one;
- read one;
- copy one;
- move one;
- cache one;
- back one up;
- stage one;
- temporarily hold one;
- invoke release signing while possessing one.

The intended release boundary is:

    MOOS checkout
        ↓
    deterministic unsigned release artifact
        ↓
    artifact leaves the MOOS checkout environment
        ↓
    independently trusted signing environment
        ↓
    signature
        ↓
    signature/public material may return to distribution

The signing environment is not part of normal MOOS checkout tooling.

Only public verification material and signatures may return to MOOS
distribution or consumer flows.

The normal Host MUST never require the publisher private key.

---

## 11. Cryptographic test identities

Release-verification tests MUST NOT solve the private-key boundary by generating
a private release-signing identity from checkout code.

Tests for the production release-verification path SHOULD use:

- immutable externally prepared signed fixtures;
- public verification vectors;
- fixed malformed/tampered fixtures;
- other test material that requires no release private key in the checkout.

Signature, tamper, archive, downgrade, and verification coverage MUST NOT simply
be removed to satisfy this rule.

This rule concerns release/publisher identities.

Clearly separate ephemeral cryptographic identities used for unrelated protocol
or device-authentication unit tests MAY exist when they cannot be mistaken for,
or accepted as, production release trust.

---

## 12. Public trust material

Public keys and fingerprints are not secrets.

They MAY be shipped, stored, compared, and inspected by MOOS.

Public material does not prove its own authenticity.

The authenticity of the initial trust anchor MUST come from the independently
authenticated bootstrap channel or another explicit trusted channel.

Unexpected trust-anchor replacement MUST fail closed.

Trust rotation MUST be an explicit protocol or release operation with a defined
continuity model.

Setup or diagnostics MUST NOT silently replace a trust anchor.

---

## 13. Authenticated releases

Release artifacts consumed by privileged Host components MUST be authenticated
before executable content from them is trusted or run.

Verification SHOULD occur over the exact bytes that will subsequently be
processed.

Privileged extraction and activation MUST reject unsafe archive structures,
unexpected members, links, special files, unsafe parents, or other paths
outside the defined release contract.

Installed privileged releases SHOULD be:

- root-owned;
- protected from modification by normal users;
- immutable in identity after activation;
- selected through atomic pointers or equivalent safe state;
- rollback-capable where practical.

A valid signature proves publisher authenticity, not freshness.

Distribution MUST eventually define how current versions, upgrades, downgrades,
and deliberate rollback are distinguished.

---

## 14. Personal image and runtime provenance

A privileged Host installation MUST NOT treat an unauthenticated guest image,
kernel, runtime binary, or equivalent executable/runtime payload as trusted
merely because the Host installer itself was authenticated.

Every production payload that materially affects the executed Personal runtime
must have a defined provenance contract.

That contract MAY use:

- inclusion in an authenticated release manifest;
- authenticated package membership;
- authenticated content digests;
- another reviewed equivalent.

The exact format is an implementation decision.

The important invariant is that authenticated Host code MUST NOT silently
activate attacker-substituted production runtime payloads.

---

## 15. First-run setup

`moos setup` should behave as product onboarding, not as an administrator
tutorial.

It SHOULD:

- detect facts automatically where safe;
- ask only when a real user decision is required;
- explain meaningful security or system consequences;
- request administrator authorization only for narrow operations that actually
  need it;
- be safe to repeat;
- recover explicitly from interrupted state;
- preserve existing user data and trust;
- keep optional remote setup separate from local readiness.

One explained local administrator authorization during initial trusted setup is
acceptable.

Routine MOOS operation SHOULD NOT require sudo.

The remote iPhone MUST NOT receive sudo or equivalent Host authority.

---

## 16. Readiness is capability-specific

MOOS MUST NOT treat installation as one giant boolean.

Readiness SHOULD be reported independently for capabilities such as:

- trusted Host installation;
- runtime installation;
- local control;
- Personal runtime;
- guest login/shell;
- remote transport;
- device pairing;
- remote grants.

One incomplete optional or future capability MUST NOT make unrelated verified
capabilities appear broken.

Likewise, one working capability MUST NOT imply that another is ready.

Examples:

    Host installation READY
    Local control READY
    Personal runtime READY
    Guest shell NOT AVAILABLE
    Remote access NOT CONFIGURED

is a valid successful state.

A missing future guest-login mechanism therefore does not inherently block
trusted Host bootstrap work.

---

## 17. Diagnostics must not lie

A diagnostic state of READY means the relevant observable contract passed.

If a required property cannot be safely observed, diagnostics MUST NOT infer
READY.

Use states such as:

- READY;
- NEEDS ATTENTION;
- BLOCKED;
- UNKNOWN;
- INFO;

or equivalent clearly differentiated states.

A conservative false negative may be acceptable where observation is genuinely
unavailable.

A security-relevant false READY is not acceptable.

Diagnostics MUST distinguish:

- metadata/integrity observations;
- authentication evidence;
- runtime health;
- login availability;
- remote authorization.

A file being root-owned is not proof that it is authentic.

A process running is not proof that guest login works.

Tailscale connectivity is not MOOS authentication.

---

## 18. Release guest login

Production release images MUST keep the default root account locked.

Production releases MUST NOT rely on:

- blank-password root login;
- a shared default password;
- a development-image login shortcut;
- credentials baked into a reusable immutable release image.

A future usable release shell MAY use any reviewed mechanism that preserves the
Host/Guest boundary.

The mechanism must define:

- the Guest principal;
- how access is established;
- credential or capability lifetime;
- storage;
- reboot behavior;
- reconnect behavior;
- revocation;
- recovery;
- relationship to Host authority.

Release-shell readiness MUST require real guest-login acceptance evidence.

Until such a mechanism exists, Host installation and local lifecycle MAY still
be complete while shell capability is reported separately as unavailable.

---

## 19. Development and release profiles are different security products

Development convenience MUST NOT silently become release behavior.

A development image MAY intentionally expose convenience behavior needed for
local testing, including a development-only login mechanism.

A blank-password root login, if provided, MUST be restricted to an explicitly
selected local development QEMU profile. It MUST NOT be used for remotely
exposed guest access or appear in a production release.

Such behavior MUST be:

- clearly distinguishable from release configuration;
- excluded from production release acceptance;
- impossible to accidentally describe as release security;
- tested separately where it remains supported.

Release tests MUST test release behavior.

Development tests MUST NOT require weakening the release profile merely to
remain green.

If a historical development test assumes behavior the release profile no
longer provides, update or reclassify the test instead of weakening release
security.

---

## 20. Protocol and client boundaries

Stable external behavior MUST use deliberate, versioned interfaces.

Clients MUST NOT depend on replaceable Host internals such as:

- PIDs;
- systemd unit names;
- private filesystem paths;
- QEMU implementation details;
- Host-specific runtime directories.

Protocol changes MUST preserve compatibility or include an explicit migration
or version transition.

External input MUST be validated for:

- type;
- size;
- framing;
- state;
- authority;
- operation-specific semantics.

Malformed, truncated, oversized, unauthorized, and unsupported requests MUST
fail predictably.

---

## 21. Remote authentication and authorization

Remote transport security and MOOS application authorization are separate
layers.

Tailscale MAY provide transport isolation and reachability.

It MUST NOT be treated as sufficient MOOS authentication.

Remote MOOS application traffic MUST be encrypted in transit. Tailscale MAY
satisfy transport encryption, but transport encryption does not replace MOOS
authentication or operation-specific authorization.

Remote clients MUST use explicit MOOS device identity.

Persistent device authorization MUST support individual revocation.

A lost device SHOULD be revocable without invalidating every other device.

Authentication MUST occur before privileged backend work where practical.

Authorization MUST be operation-specific.

Adding one remote operation MUST NOT implicitly authorize others.

Remote Terminal, remote lifecycle, file transfer, streaming, or future
capabilities must each receive deliberate authorization semantics.

---

## 22. Local-first and offline operation

MOOS MUST remain usable locally without requiring a MOOS cloud account.

Telemetry, if introduced, MUST be explicit, minimal, and disableable. Local
operation MUST NOT depend on enabling telemetry.

Optional remote infrastructure MUST NOT become the owner of local Instance
identity or local operation.

If cloud services, relays, rendezvous, accounts, or synchronization are added,
they SHOULD learn and control as little as practical.

Remote infrastructure failure SHOULD NOT destroy local operation.

---

## 23. Secrets and credentials

Secrets MUST NOT be committed to Git.

Secrets MUST NOT be embedded in reusable guest images or snapshots.

Secrets include, among other things:

- private signing keys;
- passwords;
- device keys;
- Tailscale auth keys;
- Apple signing credentials;
- tokens;
- recovery secrets;
- provisioning credentials.

Credentials MUST NOT appear in normal logs, diagnostics, support reports, or
crash output.

Diagnostics SHOULD expose only the minimum information needed for support.

Credentials stored by clients SHOULD use the platform's appropriate protected
credential store.

---

## 24. User data and upgrades

Installation, upgrade, repair, or rollback MUST NOT silently destroy:

- Personal data;
- credentials;
- pairing state;
- active trust anchors;
- user configuration;
- rollback state.

Destructive migration requires explicit design and appropriate user
authorization.

Changes to persistent formats MUST either:

- remain compatible; or
- include an explicit tested migration.

Safe retries SHOULD inspect actual system state rather than trust a single
"setup complete" flag.

Interrupted mutation SHOULD leave either the prior valid state or a defined
recoverable state.

---

## 25. Dependencies and supply-chain automation

Dependencies are allowed when they solve a demonstrated problem and their cost
is justified.

Automated dependency tooling such as Dependabot MAY:

- detect updates;
- create pull requests;
- rebase its own update branches;
- group updates;
- run normal CI.

A bot-generated pull request is a proposal, not trust.

Automation MUST NOT by itself:

- establish release trust;
- bypass required tests;
- bypass required review;
- widen permissions silently;
- rotate trust anchors;
- authorize a merge merely because it was generated automatically.

External GitHub Actions used by MOOS SHOULD be pinned to immutable full commit
SHAs.

Floating action tags SHOULD NOT be used for security-sensitive workflows.

Workflow credentials SHOULD use least privilege.

`persist-credentials: false` SHOULD remain the default for repository checkout
unless a job has a concrete, reviewed requirement to persist credentials.

A dependency update MUST NOT silently broaden GitHub Actions permissions or
secret access.

Major-version updates are not forbidden.

They require verification proportionate to their compatibility and security
impact.

Low-risk dependency automation MAY eventually use auto-merge only when a
deliberately defined policy, branch protection, required CI, and update class
make that safe.

---

## 26. Build and source integrity

Builds SHOULD be deterministic and reproducible where practical.

The repository SHOULD contain the configuration necessary to reproduce
generated artifacts.

Generated output MUST NOT become the source of truth.

Do not commit normal generated state such as:

- Buildroot output trees;
- compiled products;
- downloaded source archives;
- temporary images;
- logs;
- machine-local runtime state.

Machine-specific absolute paths MUST NOT become persistent project contracts.

Dependency versions that materially affect reproducibility SHOULD be pinned.

---

## 27. CI truth rule

> **Green means the thing we claim to test actually ran and passed.**

Required CI MUST NOT be made green by silently skipping the behavior it claims
to verify.

Do not hide mandatory failures through:

- unconditional `continue-on-error`;
- fake success paths;
- tests that no longer exercise the relevant capability;
- replacing an integration test with a weaker unrelated check.

CI should distinguish:

1. a MOOS regression;
2. a test defect;
3. a hosted-runner or infrastructure problem;
4. an intentionally unavailable external acceptance environment;
5. an unimplemented capability.

Infrastructure-specific failures SHOULD be diagnosed and made robust where
possible rather than repeatedly rerun until green.

A test requiring an unavailable external environment MAY remain explicitly not
verified without invalidating unrelated verified work.

---

## 28. Verification claims

Never claim that a build, test, boot, installation, device acceptance, release,
or security property succeeded unless the corresponding evidence was actually
observed.

Verification must be scoped.

Examples:

    QEMU boot verified

does not imply:

    guest login verified

and:

    unsigned package built

does not imply:

    publisher authenticity verified

and:

    signature verified

does not imply:

    current/fresh release verified

"Not verified" is a valid engineering result.

It is not the same as "failed."

---

## 29. Security-sensitive review

Security-sensitive architecture SHOULD receive independent review before it is
treated as established.

This includes meaningful changes to:

- authentication;
- authorization;
- trust anchors;
- release signing;
- bootstrap;
- update/rollback;
- privilege;
- sandboxing;
- Host/Guest IPC;
- networking;
- remote exposure;
- credentials;
- device identity;
- filesystem sharing;
- protocol authority;
- Guest login.

Review should inspect the real diff and intended threat model.

Valid findings must be addressed or explicitly accepted before declaring the
new boundary complete.

Lack of a second reviewer does not automatically block implementation work.

It blocks claiming a new security-sensitive architecture as fully reviewed when
that review is part of its acceptance criteria.

---

## 30. UI and presentation are replaceable

The MOOS core MUST remain independent from any particular:

- SwiftUI layout;
- radial menu;
- desktop shell;
- mobile navigation model;
- streaming protocol;
- GUI toolkit.

UI code must not become the owner of core Host behavior.

Clients should consume stable service/protocol concepts rather than reimplement
Host runtime logic.

Presentation may evolve aggressively as long as core trust and service
boundaries remain stable.

---

## 31. Performance and minimalism

MOOS should remain small enough to understand.

Prefer:

- small components;
- standard-library functionality where reasonable;
- explicit state;
- simple data formats;
- narrow services;
- measurable resource costs.

A framework or dependency is not forbidden merely because it is large.

It should justify the complexity, footprint, attack surface, or maintenance cost
it introduces.

Meaningful changes to:

- rootfs size;
- idle memory;
- boot time;
- binary size;
- persistent storage;
- remote latency

SHOULD be measured when the affected work can materially change them.

---

## 32. Security must not be bypassed for convenience

The following are not acceptable solutions to development friction:

- running the checkout as root;
- unlocking release root;
- weakening signature verification;
- broadening sudo rules;
- giving the Gateway arbitrary Host execution;
- treating Tailscale as authorization;
- disabling failing security tests;
- silently replacing trust material;
- using the release signing key inside checkout tooling;
- turning a development profile into the production profile.

When convenience and a trust boundary conflict, redesign the workflow.

---

## 33. Architecture may evolve

These rules intentionally permit MOOS to grow.

New capabilities are allowed when their authority, isolation, persistence, and
failure behavior are understood.

A new design does not need to preserve an old implementation assumption merely
because a historical test encoded it.

When a new architecture invalidates an old assumption:

1. identify the assumption;
2. preserve any still-valid security property;
3. update the implementation and test contract;
4. document the migration or behavior change;
5. do not weaken the new boundary merely to preserve old test mechanics.

Historical behavior is evidence, not permanent architecture.

---

## 34. Rules changes

`rules.md` itself may evolve.

Rule changes must be deliberate.

Use semantic rule versions:

- **major** — changes a fundamental trust/security/authority model;
- **minor** — adds or materially strengthens a rule without changing the core
  model;
- **patch** — clarification with no intended normative change.

A rules change SHOULD explain why the old rule was insufficient or ambiguous.

Rules MUST NOT be changed opportunistically inside an unrelated feature merely
to bypass a blocker.

If implementation legitimately requires a different fundamental trust model,
propose the rule change explicitly and review it as architecture.

---

## 35. Completion principle

A MOOS change is complete when the capability it claims to implement is:

- within these rules;
- understandable;
- narrowly scoped enough to review;
- tested at the affected boundaries;
- honest about external or unavailable acceptance;
- recoverable where it mutates durable state;
- documented when behavior or architecture materially changed.

Completion does not require every future MOOS capability to exist.

A safe verified foundation may be complete while later capabilities remain:

    NOT CONFIGURED
    NOT AVAILABLE
    NOT VERIFIED
    DEFERRED

The project should prefer honest partial capability readiness over false
all-or-nothing readiness.

---

# Core invariants

If the rest of this document is forgotten, preserve these:

1. **The Host controls Instances; Instances never implicitly control the Host.**
2. **No arbitrary Host shell API.**
3. **Guest root is never Host root.**
4. **Privileges come only from authenticated installed boundaries, never from a
   developer checkout.**
5. **The release private signing key never enters MOOS checkout tooling,
   repository CI, or normal Host operation.**
6. **Production runtime payloads require authenticated provenance.**
7. **Release root remains locked.**
8. **Transport is not authentication; authentication is not authorization.**
9. **READY means the relevant contract was actually observed.**
10. **Green CI means the claimed test actually executed and passed.**
11. **Missing external acceptance blocks only the claim that needs it, not safe
    implementation up to that boundary.**
12. **Security boundaries may evolve deliberately; they are never weakened
    silently for convenience.**
