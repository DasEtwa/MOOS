# MOOS Native N3 independent security review

Status: audit fixes under review on `codex/moos-native-security-review`; this is
not merged production truth.

Frozen target: PR #45, `codex/moos-native-n3-installer`, exact head
`57072cd733cf7faf5e3cc498e204e38ea2f242dd`. Exact N2 base/merge-base:
`0816d1837bcecfd7592d450fabc5b9ff4d7e5d33`. The reviewed head's immediate Git
parent is `748520f498a383be61345236827d8e0ade108ac0`.

Six independent initial specialist reviews covered destructive storage,
boot/root, payload and supply chain, Rust implementation, architecture, and a
fresh-eyes red-team pass. A separate coordinator deduplicated and challenged
their results before source changes began.

## Initial findings before fixes

| ID | Severity | Initial result |
| --- | --- | --- |
| N3-01 | HIGH | Byte-cloned installed disks duplicated both GRUB's marker and Linux's SYSTEM_A PARTUUID; simultaneous attachment could select the wrong clone instead of failing closed. |
| N3-02 | MEDIUM | Bounded sysfs loops silently truncated enumeration, so excess entries could be omitted from ambiguity and mounted-partition checks. |
| N3-03 | MEDIUM | BIOS inputs were verified, reopened by path, and compared only with transient staging; a verify/use/restore race could make COMPLETE describe BIOS bytes not bound to the plan. |
| N3-04 | MEDIUM | A Buildroot checkout already at the pinned commit could contain arbitrary tracked changes and still build self-consistently hashed artifacts. |
| N3-05 | LOW | Mutation guards masked expected layout changes in a way that also weakened concurrent GPT-change detection. |
| N3-06 | LOW | The Host harness lacked an explicit target-file size bound. |
| N3-07 | LOW | Fixed temporary paths can require reboot after interruption. |
| N3-08 | LOW | Fresh-install wording could be read as secure raw-media erasure, which mkfs does not guarantee. |

Initial coordinator counts: CRITICAL 0, HIGH 1, MEDIUM 3, LOW 4, design
limitations 3. The initial decision was **blocked** pending N3-01 through N3-04.

Design limitations recorded separately from code defects: no publisher-authenticated
Native provenance or anti-rollback, no authenticated boot/Secure Boot, and no
Core/DATA compatibility epoch for preserve reinstall. Boot-console control can
edit GRUB and request `init=/bin/sh` or `rdinit=/bin/sh`; this is not a normal or
remote N3 root path, but locked root must not be described as physical boot trust.

## Audit changes awaiting final evidence

- Linux early `PARTUUID` lookup rejects a second match before root mount; a BIOS
  and UEFI cloned-disk negative test requires the exact kernel diagnostic and no
  DATA/login acceptance.
- Sysfs enumeration consumes the complete bounded directory or errors; it never
  silently accepts a prefix.
- Private BIOS staging is rehashed against the planned role digests before GRUB.
- Buildroot preparation accepts either pristine pinned source or the exact
  declared patch-set bytes, rejecting extra staged or unstaged tracked changes.
- Host acceptance files are exactly 512 MiB, and documentation distinguishes
  filesystem replacement from secure media sanitization.
- Five focused workflows cover policy, real BIOS/UEFI end-to-end installation,
  adversarial cases, x86_64/ARM64 Host portability, and independent clean-runner
  reproducibility. ARM64 Host evidence is not ARM64 MOOS target support.

Final fix commits, second-review results, CI run evidence and remaining NOT
VERIFIED boundaries will be added only after they are observed.
