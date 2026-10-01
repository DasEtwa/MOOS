# Native N1 acceptance record

Implementation baseline: main `289fe2d955a87080f9e07694fcdee51e612d34d3`.
Work branch: `codex/moos-native-boot`. This record describes branch evidence,
not a published release, installed Host or merged production baseline.

## Observed regression evidence

Executed as the unprivileged builder under Windows/WSL2 Ubuntu:

```sh
python3 tests/native_config.py
python3 tests/moosd_protocol.py
python3 tests/moos_cli.py
python3 tests/operator_ux.py
python3 tests/ios_simulator.py
python3 tests/runtime_control.py
python3 tests/runtime_isolation.py
python3 tests/control_plane_installer.py
python3 tests/release_profile.py
python3 tests/qemu_launcher.py
python3 tests/personal_identity.py
python3 tests/host_regressions.py
python3 tests/admin_release.py
python3 tests/protocol_contract.py
python3 tests/ios_client.py
python3 tests/ios_distribution.py
python3 tests/gateway_auth.py
python3 tests/gateway_protocol.py
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
./scripts/build.sh --profile development --jobs 2
python3 tests/qemu_smoke.py
MOOS_TEST_NETWORK=none python3 tests/qemu_smoke.py
python3 tests/qemu_terminal_bridge.py
./scripts/build.sh --profile release --jobs 2
python3 tests/qemu_release_smoke.py
```

All listed checks passed. Rust ran 30 tests with the pinned 1.97.1 toolchain.
The Personal development smokes observed login, utilities, DHCP/disabled
network, reboot and poweroff. The terminal test observed actual `moos-info`,
daemon restart while QEMU remained alive, reconnect and clean guest shutdown.
The final Personal release rebuild validated locked shadow and rejected blank
root login. Existing Personal configs/launcher/control-plane source are unchanged.

`sh -n` and ShellCheck (`--severity=warning --exclude=SC1007`) passed for all
18 POSIX shell scripts under `scripts` and `system/overlay`. Native policy tests
reject development mode, unrestricted launcher arguments, invalid firmware/network
choices and malformed/truncated disk metadata.

The independent second-agent review inspected the staged source/config/tests/docs
diff and reported no blocking security or architecture finding. Its physical
boot manipulation caveat is recorded in NATIVE.md.

Initial Linux-copy checks caught CRLF-mutated public fixtures and a manifest;
the Linux checkout was restored to exact tracked Git bytes and checks rerun
successfully. No production validator or fixture was weakened. The privileged
`tests/managed_personal.py` invocation refused the unprivileged user; its real
systemd integration was not run as root and is NOT VERIFIED in this slice.

## Observed Native N1 evidence — 2026-10-01

The complete Native Buildroot build succeeded under Windows/WSL2 Ubuntu.
It started with `./scripts/build.sh --target native --jobs 4`; a WSL
`WSAETIMEDOUT` prevented an additional process from starting during kernel
compilation. Restarting that Ubuntu instance preserved the build tree, and
`./scripts/build.sh --target native --jobs 2` completed the build. No WSL-local
paths or environment-specific workaround entered tracked source.

Observed generated kernel configuration has EFI/EFI_STUB, MBR parsing,
SCSI/AHCI/ATA_PIIX, NVMe, virtio block/PCI/network, e1000e/igb/r8169, ext4 and
8250 serial console built in. Generated Buildroot configuration disables root
login, Python runtime and GRUB target tools, and enables both GRUB boot modes.
Driver inclusion is not hardware acceptance.

Executed successfully against the actual disk:

```sh
python3 tests/native_boot.py --boot bios --network user --log native-bios-console.log
python3 tests/native_boot.py --boot uefi --network user \
  --uefi-code /usr/share/OVMF/OVMF_CODE_4M.fd \
  --uefi-vars /usr/share/OVMF/OVMF_VARS_4M.fd --log native-uefi-console.log
python3 tests/native_boot.py --boot bios --network none --log native-bios-none-console.log
python3 tests/native_boot_negative.py
```

QEMU 11.0.3 ran through rootless Bubblewrap with TCG, one CPU, 256 MiB RAM,
disk snapshots and serial output. BIOS used SeaBIOS 1.17.0; UEFI used Ubuntu's
OVMF 2025.11 firmware pair. Both reached the GRUB marker, Linux 6.18.43,
SYSTEM_A ext4 on partition 2, MOOS Native `0.1.0-dev` identity and login.
Explicit NAT obtained DHCP `10.0.2.15`; the no-network BIOS case also booted.
Offline shadow inspection confirmed a locked root field, and actual empty
password login returned to login without a shell. QEMU console quit (Ctrl-A x)
returned exit status zero; disk and OVMF backing hashes remained unchanged.
This is clean emulator termination, not an orderly Native guest shutdown.
The negative test preserved metadata/locked root while replacing only BIOS
boot code in a temporary copy and observed an actual boot deadline failure.

The first boots correctly failed the marker check because GRUB's `echo` module
was missing. After adding it to both built-in module lists, tests exposed an
absent stdio mux under `-nodefaults`. The fixed explicit serial mux retains
`-monitor none`. Complete tests then passed; no assertion or root-lock gate
was relaxed. Independent review covered these corrections with no blocking finding.

### Generated artifacts and reproducibility

Locations below are relative to the checkout, not installation devices:

| Artifact | SHA-256 |
| --- | --- |
| `output/native/images/moos-native-x86_64.img` (77 MiB) | `b7f2a080f6ba4019ff00c15f58a0e3d4ba59916b136c7c6bf359ac9e8a2059d1` |
| `output/native/images/rootfs.ext2` (60 MiB, ext4) | `e1598f6c32b08280e048a8276dbda205bc0260aeff7749df795da55401d5a5d0` |
| `output/native/images/native-boot.vfat` (16 MiB) | `ba5ee2eb120faa18b340aaaa3033a62fa814aac40ff20a88b5af25ee0eb4346b` |
| `output/native/images/bzImage` | `cfe5dcfea7f781a9b0cf3668f71d36ba0c86a94d66346fa4a789c8bc36e91826` |

`SOURCE_DATE_EPOCH=1790812800` and the pinned inputs were retained. After the
successful boots, `./scripts/build.sh --target native --jobs 2` was run twice
more. `sha256sum` and `cmp` confirmed all four artifacts identical to the tested
image and to each other. This verifies repeated image/rootfs generation with
the existing built toolchain; a second independent empty-tree toolchain build
was not performed. Generated artifacts, logs, firmware and tools are not committed.

The ext4 filesystem occupies 15,019 KiB including journal/metadata within its
60 MiB partition (`dumpe2fs`: 61,440 blocks, 46,421 free, 1,024-byte blocks).
The acceptance VM allocation is 256 MiB, not a measured hardware RAM minimum.

### Source scope

Changed: `.github/workflows/host.yml`, `README.md`, `STEPS.md`,
`HOST_GUEST_ISOLATION.md`, `NATIVE.md`, `NATIVE_ACCEPTANCE.md`,
`configs/moos_native_x86_64_defconfig`, `scripts/build.sh`,
`scripts/native-post-build.sh`, `scripts/native-post-image.sh`,
`scripts/run-native-qemu.sh`, `system/native/genimage.cfg`,
`system/native/grub.cfg`, `system/native/linux.config`,
`system/native/overlay/etc/moos-platform`, `tests/native_config.py`,
`tests/native_boot.py` and `tests/native_boot_negative.py`.

BRAIN's MOOS README/ARCHITECTURE/ROADMAP are synchronized locally as unmerged
branch context. No Protocol, Gateway, production Python runtime, privileged
installer, Personal launcher/config or release trust contract is changed.

## Remaining NOT VERIFIED / deferred

Not verified: physical hardware, Secure Boot, installer, persistent DATA,
A/B updates, package manager, WSL runtime backend, macOS virtualization
backend, privileged Native Host provisioning, production image signing.
Also not verified: a second independent clean toolchain build, physical SATA/
NVMe/Ethernet/serial hardware, installed multi-disk identities, boot recovery,
and privileged managed Personal systemd integration in this run.

N0/N1 branch acceptance is complete. The next safe slice is N2: decide GPT,
unique installed identities, persistent system/DATA ownership and recovery before
N3 can introduce any destructive installation path.
