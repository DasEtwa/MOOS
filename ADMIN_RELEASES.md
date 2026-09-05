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

The trust anchor consists of both:

- `/usr/libexec/moos/moos-admin-installer`
- `/etc/moos/trust/admin-release.pem`

They must be provisioned by an operating-system package or another
administrator-controlled channel whose authenticity is established outside
the checkout. The public-key fingerprint must be checked out of band. This
repository deliberately provides no `sudo` self-install command for either
file: such a command would recreate the circular bootstrap trust problem.

`host/moos_admin_installer.py` is source for the trusted package, not a script
to execute from a clone. A production package installs it at the fixed path
above, owned by root and not writable by group or other users. The helper
refuses to run from any other path and accepts no alternate key path.

## Build and sign

An unprivileged build creates a deterministic unsigned bundle after the Rust
Gateway release binaries have been built:

~~~bash
./scripts/build-gateway.sh
./scripts/build-admin-release.py --output moos-admin-release.tar
~~~

The RSA or ECDSA release signing key must never be passed to checkout code.
Move the exact bundle into the isolated release-signing environment, review it
there, and sign it with trusted tooling, for example:

~~~bash
openssl dgst -sha256 -sign /secure/moos-admin-release-private.pem \
  -out moos-admin-release.tar.sig moos-admin-release.tar
~~~

Only the public key is provisioned on a MOOS host. Private keys, signatures,
and provisioning credentials are not committed to this repository.

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
