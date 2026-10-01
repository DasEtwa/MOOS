# MOOS Host package

The initial package target is Ubuntu 26.04 amd64 with systemd 257+ and cgroup v2.
Real installation on that baseline is NOT VERIFIED. Other distributions are
not advertised as supported by this bootstrap.

Build as an ordinary user:

```sh
./scripts/build-host-package.py --version 0.1.0 \
  --public-key /path/to/public-verification-key.pub \
  --image-dir output/images --runtime-dir output/host \
  --output moos-host_0.1.0_amd64.deb
```

The public key comes from the release publisher. There is no default publisher
identity, key generator, private-key option, signing command, or installer in
this build tool. Build the release-profile image and Gateway first. Omitting
both image/runtime arguments produces a Host-control-only package: setup
explicitly reports Personal image and guest shell as unavailable.

The artifact is unsigned. Do not install a checkout-built package as root to
bootstrap its own trust. Transfer it to the independently trusted publisher
for review and distribution through an authenticated OS package channel or an
equivalent independently authenticated administrator channel. No production
channel is published by this repository yet. Once that channel is provisioned,
its normal OS package installation installs `moos-host`; then run `moos setup`
as the local user. The OS package installation and first-run authorization are
separate administrator actions; routine operation uses no sudo.

The package has no maintainer scripts. Before applying it, the coordinator
parses the canonical package manifest through the shared verification core and
snapshots exact member-hash-verified payload bytes into protected private staging; later
extraction, helper imports and runtime copying use only that snapshot. It installs a fixed Python-isolated
coordinator, the existing administrator helper and its separately installed
root-owned verification core at `/usr/libexec/moos/moos_verification.py`, a bootstrap CLI, a public-key
candidate, and a manifest-bound administrator archive. Optional Personal
kernel/rootfs and the matching Buildroot QEMU dependency closure and fixed x86
firmware have the same package provenance. Archive hashes protect installed
integrity-only `packageMembership` evidence; authenticity comes from the
external OS channel, never those hashes or root ownership alone. Setup does not
report a local package signature as VALID or VERIFIED. The public key candidate is not installed over an
existing trust anchor by the package manager. Setup creates the protected trust
file only when absent and refuses any difference when one exists.

Setup authorizes one fixed installed operation through sudo. It verifies the
local sudo invoker, package integrity and existing state, prepares the runtime,
stages Personal if supplied, installs local control and grants only the
`moos-control` group. It does not start Personal, configure networking or pair a
device. Log out and back in after a new access grant; setup verifies actual
local access from the refreshed session. A stopped Personal is not proof of
boot or login. Release root remains locked and guest shell is NOT AVAILABLE.

Package versions use numeric MAJOR.MINOR.PATCH. Setup records the highest
accepted version before mutation and rejects older versions or changed bytes
under that version, including after interruption. It refuses to replace a
different active administrator release or existing Personal data. It preserves
administrator rollback state on repeat. This slice is first-run preparation,
not automatic upgrades: a future explicit update operation must coordinate
package version, admin activation, Personal migration and deliberate rollback.
No automatic trust rotation, credential changes, or data cleanup are provided.

An interrupted setup can leave protected trust, a locked runtime account, a
staged release/image, or local control prepared. Rerunning the same package
revalidates those steps and resumes; it does not undo a successful grant or
destroy staged state. Existing control-plane activation retains its own rollback
transaction. A failed step is reported as BLOCKED rather than installation
READY. Foreign/mismatched state requires administrator investigation.

The Buildroot runtime must be built for this Host baseline. The builder
inspects ELF dependency metadata without executing it, bundles dependencies
from the declared runtime directory, and leaves baseline C/C++ runtime
libraries to the OS. Staging validates QEMU only as the locked runtime identity.
Guest-rootfs lock validation is performed on copied image bytes without mounting
or executing the guest. Real ABI, QEMU boot and managed lifecycle acceptance
remain required before publication. Publisher review must also provide the
redistribution licenses/source offers for the bundled third-party binaries.

Verify the actual packaged runtime as an ordinary user (no installation):

```sh
python3 tests/host_package_smoke.py --package moos-host_0.1.0_amd64.deb
```

This extracts only into a temporary directory and boots the package's own QEMU,
firmware, libraries and release image through the existing isolated launcher.
It checks release boot and rejection of blank root login, not a usable guest
shell or privileged managed installation. Keep this acceptance in the publisher
checklist: an ordinary image smoke using `output/host` would miss absent
packaged firmware. The package includes `efi-e1000.rom` because QEMU loads it
even with guest networking disabled.
