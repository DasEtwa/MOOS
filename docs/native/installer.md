# MOOS Native N3 installer

Status: tested and independently reviewed experimental implementation on a
stacked N3 branch; see [N3 acceptance](acceptance/N3.md). This
is not production installation readiness, physical-hardware acceptance or a
publisher-authenticated release. [N2](acceptance/N2.md) remains dated evidence.

## Authority and deployment boundary

Native is one backend; the installer adds no Protocol v1 operation, listener,
HTTP, mobile pairing, cloud dependency or arbitrary exec/shell interface. Its
substantial local core is Rust (`native/installer`); Python remains build/test
tooling. No installer-only tools or code are added to the installed Core.
The installer runs as guest root inside a disposable, rootless isolated QEMU
environment. It is never executed as Host root from the checkout.

N3 accepts only QEMU DMI plus virtio_blk targets with an explicit 20-byte
`MOOS-N3-TARGET-` serial and five random hexadecimal suffix characters. Virtio's
serial field is limited to 20 bytes. Serial uniqueness is checked across
discovered candidates; it is not an authentication claim. Physical disks,
other virtual devices and readonly disks remain ineligible. The Host runner
permits only fixed private single-link 0600 regular target files in a test-owned
0700 `/tmp/moos-native-installer-*` workspace. No Host block device, symlink,
hardlink, canonical output or source inode is a writable target. Source media
and firmware are bound readonly; target writes stay in disposable files.
The runner pins checked file inodes with O_PATH/O_NOFOLLOW and Bubblewrap's
`--bind-fd`/`--ro-bind-fd` before sandbox setup; supporting Bubblewrap is required.
There is no QEMU monitor, device passthrough, shared Host folder or network.
Hotplug/concurrent disk mutation during destructive commit is unsupported;
device generation is checked again at mutation boundaries. This restriction
must receive a separate hardware-enablement review before being widened.

## Media and offline payload

`scripts/build.sh --target native-installer --jobs 2` first builds and validates
the Native Core, then uses its pinned Buildroot C toolchain for a separate
installer target. Rust uses the repository's pinned toolchain and Cargo.lock,
linked against the Buildroot target sysroot. Buildroot remains the image builder.

The USB-style raw GPT image has a protective MBR, 1 MiB BIOS_GRUB and a 128 MiB
BOOT/ESP. GRUB boots the installer kernel and compressed initramfs; its root is
ephemeral RAM. Payload bytes remain on the readonly source FAT partition rather
than being loaded wholesale into RAM. Expected disk size is 131 MiB; measured
runtime/artifact sizes belong in the acceptance record. BIOS and UEFI use real
disk boot. UEFI uses the fallback loader without firmware NVRAM writes. ISO and
Secure Boot are deferred. The console launches the installer, no login/getty
or root-shell fallback; root credentials remain locked.

The bounded schema-1 manifest contains version and exact role/file/size/SHA256
for BOOT, SYSTEM_A, BIOS boot/core, kernel and EFI fallback loader. These are
allowlisted files, not scripts. All payload bytes are checked before destructive
mutation and checked again before COMPLETE. SHA256 establishes integrity only;
reproducible bytes and co-located hashes establish no publisher authentication.
No signing key or unmerged PR #41 code is used.

## Discovery, plan and confirmation

Discovery uses sysfs/kernel facts: device name, available model/serial/WWN,
size, sector sizes, removable/readonly flags, canonical sysfs device path,
major:minor and diskseq. First/last-MiB hashes bind the current layout observation.
Bounded partition role/range summaries are visible. Recognized installed N3
state is probed readonly without journal replay; its valid local installation
ID is displayed where available. Invalid or foreign state is not preserve-capable.
The kernel command line identifies the exact installer partition PARTUUID;
one source disk must resolve and be readonly. It is never an eligible target.
There is no assumption about enumeration order or `/dev/sda`.

Local operations are inspect, plan by observed serial with fresh/preserve mode,
confirm, cancel before commit, and poweroff. The in-memory typed plan binds schema,
mode, full target/fingerprint, payload/version/hashes, intended GPT roles and
GUIDs, random nonce, installation ID, preserve decision and destructive summary.
Preserve plans additionally bind the full DATA hash. Plans expire after 120 seconds
and are consumed on a confirmation attempt; changing or cancelling a plan
invalidates its confirmation. No client-authored serialized plan is applied.

The console displays exact model (or unavailable), serial, size, mode and whether
DATA survives. Fresh mode displays **ALL DATA ON THIS TARGET WILL BE ERASED**.
It requires `confirm ERASE <16-hex-plan-digest>`; preserve mode requires a distinct
`confirm PRESERVE <16-hex-plan-digest>`. A generic yes/constant token is insufficient.
The installer re-inspects the target and payload before writes. It retains the
whole disk and partition descriptors and repeats device-generation checks.
Subprocesses use fixed executables/direct argument arrays, clean environments,
bounded output/time and child cleanup; never shell-expanded device strings.
Before observing or verifying an eligible unmounted target, the installer
flushes and invalidates whole-device and partition caches through retained
descriptors using `blockdev --flushbufs`. Mounted candidates fail closed.
Source and ineligible devices are excluded from this operation. This prevents
cached layout observations from accepting an externally changed backing file;
it is not support for concurrent writers or live disk replacement.

## Installed layout and identities

Targets are 192 MiB–64 GiB, a whole MiB in size, with 512-byte logical sectors.
N3 deliberately supports this bounded virtual test range, not arbitrary hardware.
Partitions align to 1 MiB: BIOS_GRUB 1 MiB at 1, BOOT 16 MiB at 2, SYSTEM_A
60 MiB at 18, SYSTEM_B 60 MiB at 78, DATA at 138 MiB through the last whole MiB
before backup GPT. DATA therefore grows with the target; a 512 MiB disk gets
373 MiB DATA. System/BOOT sizes remain the tested tiny Core baseline.

Fresh installation creates random UUIDv4 disk and five partition GUIDs from
OS getrandom; no hardware/timestamp/cloud derivation. Prototype GUIDs are
explicitly rejected as installed identities. BOOT has a derived random FAT ID
and an exact full-BOOT-GUID marker. Supported GRUB file search selects that
marker; kernel root uses the unique SYSTEM_A PARTUUID. The full-GUID marker
avoids depending solely on a 32-bit FAT ID for multi-disk selection.
BIOS core starts with `(,gpt2)/boot/grub`, using the firmware-selected disk
before loading its configuration; it does not initially force `hd0`.

DATA filesystem UUID/label remain the N2 fixed filesystem contract. This is
safe only with the existing same-actual-system-disk role selection: neither
installer nor Core may fall back to globally finding another DATA by label.
GPT GUIDs and software installation identity are distinct. Fresh installation
creates one random installation ID in DATA (root:root 0600), schema `1\n`,
and root:root 0700 DATA/identity/config. First boot validates/reuses it; it is
not a credential. Distribution media contains no installation identity.
Cloning initialized DATA retains identity; automatic cloning repair is absent.

SYSTEM_A is writable and receives the exact locked-release Core rootfs.
SYSTEM_B is zero-filled/unformatted and no-automount, reserved for future U1.
No slot switching, try boot, health confirmation, rollback or fallback exists.

GRUB's normal GPT BIOS embedding runs on a private sparse regular staging image
using the existing reviewed regular-image patch. Only MBR and BIOS_GRUB are
then copied/verified through the retained whole-target descriptor. GRUB receives
no guest block device; its path canonicalization cannot reopen a target name.
GPT construction/inspection uses standard libfdisk/sfdisk, not a new format writer.
After libfdisk CRC checks, a bounded read-only check requires matching standard
primary/backup headers and entry arrays; independently CRC-valid divergent
copies are rejected too. No production custom GPT writer or repair exists.

## Preserve DATA and failures

Preserve mode is offered only for the exact recognized installed GPT roles,
supported DATA schema/ownership/metadata and valid installation ID.
N2's fixed prototype GUID distribution is not an installed N3 preserve target.
Fresh erase of such a disposable distribution copy requires its own erase plan.
Preserve mode retains all GPT identities and the complete DATA filesystem bytes. It replaces BOOT,
SYSTEM_A and reserved SYSTEM_B only. No DATA formatter is called. Foreign,
damaged or ambiguous layout/state makes preserve unavailable, without falling
back to erase. A fresh erase needs a separate plan and confirmation.

Phases are PLANNED, CONFIRMED, WRITING_LAYOUT (fresh only), WRITING_SYSTEM,
WRITING_BOOT, INITIALIZING_DATA (fresh only), VERIFYING and COMPLETE. Errors
report FAILED; startup safety failures report BLOCKED. COMPLETE requires final
GPT/roles/GUID verification, system hash, blank reserved B, validated DATA/ID,
exact generated boot configs and kernel/EFI content. Independent installed
disk boot is an acceptance gate, not an implication of a completed write.
The written SYSTEM_A is inspected readonly for the exact Core version/backend,
locked root metadata and absence of installer/test-only files and Python runtime.

Interrupted fresh installation may leave an incomplete/unbootable target.
Retry needs new inspection/plan; some failures require rebooting the ephemeral
installer. Preserve failures may damage replaceable system/boot slots, but the
implementation never formats DATA or replaces its ID. Full power-loss recovery,
crash durability, repair, authentic boot/provenance and real hardware remain
separate gates. No update can infer health merely from kernel boot.

## Later work

N3 installs only Core: no Tailscale, Sandboxes, Store/ms, Servers, Java, database
or GUI. Optional Apps & Features and first-boot UI remain later work. N4 may
expose narrowly typed inspect/create-plan/confirm/status/cancel operations over
an independently reviewed LAN-first authenticated encrypted ephemeral session.
The installer retains disk-safety authority; the app never receives exec/shell.
QR pairing/cloud/relay are not implemented. Permanent device pairing remains
separate. Package engine, Store catalog UX and system/security/product/workload
update planes retain the [N2 direction](README.md); security maintenance must
not depend on Store/manual checks alone. N3 implements none of those channels.
