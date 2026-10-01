# Authenticated administrator releases

MOOS never supports running a privileged program directly from a developer
checkout. Checkout files are untrusted input, including installer scripts,
manifests, documentation, and Git metadata.

The administrator trust chain is:

~~~text
externally provisioned root-owned helper + root-owned public key
                              ↓
                 opaque copy of bundle/signature
                              ↓
                 signature over the exact copied bundle
                              ↓
             authenticated archive parsing and allowlisted extraction
                             ↓
             root-owned /usr/lib/moos/admin-releases/<sha256>
                             ↓
             atomic admin-current + admin-rollback pointers
~~~

The trust anchor consists of three independently provisioned components:

- `/usr/libexec/moos/moos-admin-installer`
- `/usr/libexec/moos/moos_verification.py`
- `/etc/moos/trust/admin-release.pem`

They must be provisioned by an operating-system package or another
administrator-controlled channel whose authenticity is established outside
the checkout. When provisioning these files separately, the public-key
fingerprint must be checked out of band. In the package route below, the
independently authenticated package channel authenticates the public-key
candidate as part of the package. Neither route permits a checkout `sudo`
self-install command: that would recreate the circular bootstrap trust problem.

`host/moos_admin_installer.py` and `host/moos_verification.py` are source for
the trusted package, not files to import or execute from a clone. A production
package installs them at the fixed paths above, owned by root and not writable
by group or other users. The helper refuses to run from any other path, checks
the verification module's ownership, mode and full ancestry before importing
it, and accepts no alternate key or verification-module path.

## Package-owned first-run bootstrap

Rules v2 also permits authenticated OS-package membership as initial payload
provenance. The `moos-host` package described in
[HOST_ONBOARDING.md](HOST_ONBOARDING.md) installs the fixed
`/usr/libexec/moos/moos-host-setup` coordinator and a public-key candidate.
After independent package authentication, `moos setup` can authorize that
installed operation to prepare local Host components. It copies the entire
manifest-bound payload into protected private staging, checks the exact copied
bytes, and applies the same administrator archive allowlist before activation.
The package's authenticated membership authorizes this bootstrap archive;
its content hash alone does not. The standalone helper's detached-signature
verification remains required for separately supplied administrator updates.

The OS package does not overwrite `/etc/moos/trust/admin-release.pem`.
The coordinator provisions it only when absent and refuses an existing mismatch.
It accepts no source directory, executable path, key path or target username.
It preserves differing active releases and rollback state instead of treating
first-run setup as an implicit update operation. Personal image and QEMU payloads
are optional together and have the same authenticated package membership.

Building the unsigned package does not authenticate or authorize its installation.
Production channel/public trust and real Host acceptance remain external gates;
private release signing never enters the checkout or normal Host workflow.

## Build and sign

An unprivileged build creates a deterministic unsigned bundle after the Rust
Gateway release binaries have been built:

~~~bash
./scripts/build-gateway.sh
./scripts/build-admin-release.py --version 0.1.0 --output moos-admin-release.tar
~~~

The RSA or ECDSA release signing key must never be passed to checkout code.
Move the exact bundle into the isolated release-signing environment, review it
there, and sign it with trusted tooling, for example:

~~~bash
openssl dgst -sha256 -sign /secure/moos-admin-release-private.pem \
  -out moos-admin-release.tar.sig moos-admin-release.tar
~~~

Only the public key is provisioned on a MOOS host. Private keys, production
release signatures, and provisioning credentials are not committed to this
repository. Immutable public non-production test-vector and fixture signatures
are the explicit test-only exception documented below.

The production verification path is tested without creating a signing identity
in the checkout. Every newly built bundle contains one canonical
`moos-manifest.json` describing only the current allowlisted release members,
with fixed `ADMIN_RELEASE`, Linux/x86_64 and stable-channel policy. The
historical pre-manifest fixture remains immutable regression evidence and is
never rewritten or treated as installable. Current-source determinism,
manifest metadata, member hashes/sizes, opaque staging, tamper rejection, the
archive allowlist, activation, rollback and malicious-member rejection remain
covered.

## Authenticate and install

After the helper and public key have been provisioned independently:

~~~bash
sudo /usr/libexec/moos/moos-admin-installer \
  --bundle "$PWD/moos-admin-release.tar" \
  --signature "$PWD/moos-admin-release.tar.sig"
~~~

The helper treats both paths as opaque, attacker-controlled byte sources. It
opens them without following symlinks, requires regular single-link files,
copies them into a private root-owned directory, and verifies the detached
signature over that copied bundle. It does not open the tar archive, inspect a
manifest, import Python from it, or execute any contained file before that
verification succeeds.

After authentication, extraction uses an exact path allowlist, accepts regular
files only, applies fixed modes, and atomically switches `admin-current` while
preserving the previous release in `admin-rollback`. Only the active release
and that rollback target are retained; older digest directories are removed
after the pointer switch. Root operations then use only that authenticated
release:

The manifest is parsed only from the same privately staged bytes that passed
detached-signature verification. Before extraction, the helper verifies the
canonical manifest, every archive member's exact hash and size, and the fixed
role/platform/channel policy. An archive without `moos-manifest.json` is
rejected by the normal installer; the old fixture path is regression-only.

~~~bash
ADMIN_ROOT=/usr/lib/moos/admin-current
sudo "$ADMIN_ROOT/scripts/setup-runtime-user.sh" --source-root "$ADMIN_ROOT"
sudo "$ADMIN_ROOT/scripts/stage-instance.sh" \
  --id personal --image-dir "$PWD/output/images"
sudo "$ADMIN_ROOT/scripts/setup-control-plane.sh" --source-root "$ADMIN_ROOT"
~~~

The kernel, root filesystem, and staged QEMU files passed to
`stage-instance.sh` remain data copied for the unprivileged `moos-runtime`
identity; they are not executed or imported by root. The scripts interpreting
those paths come from the authenticated administrator release.

Never replace these commands with `sudo ./scripts/...` or `sudo python3
tests/...` from a checkout.

## Operator onboarding and signing identities

`moos doctor` and checkout `moos setup` inspect this boundary without changing
it. Setup from the authenticated Host package can explicitly prepare it through
the installed coordinator described above.
`moos setup --explain trust` explains the user/publisher distinction even on an
unprovisioned Host. Missing helper, public key, or active release is surfaced
as an installation step requiring an administrator, not an invitation to run
checkout scripts as root. The unsigned package builder implements the bootstrap
layout; a production authenticated distribution channel is not published here.

A Host operator consumes signed releases and needs only the public key.
A release publisher must deliberately choose between creating a new RSA/ECDSA
identity and importing an existing identity in the isolated trusted signing
environment. Use that environment's trusted key-management tools, keep the
private key and its backup there, and export only the public key. Setup does
not accept a private-key path, create/import keys, search for a missing key,
or regenerate an identity. Provisioning the helper and public key remains an
independently authenticated administrator action.

With trusted tooling in that environment, obtain the public-key fingerprint:

```sh
openssl pkey -pubin -in admin-release-public.pem -outform DER | openssl dgst -sha256
```

Transfer the 64 hexadecimal digits through a separate trusted channel, then
compare on the Host with `moos doctor --expect-fingerprint HEX_DIGITS`.
A mismatch is a stop point for release installation. Doctor neither replaces
the key nor determines the authenticity of the helper. Its file/parent
ownership and mode checks and active-release pointer check are observations,
not signature revalidation or permission to install. The diagnostic report
omits even the public fingerprint and never reads the private device store.
