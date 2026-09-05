# Open issue review — 2026-09-05

All 18 open GitHub issues were compared with branch `Low-bugs`, starting at
`0a2e836`. Some findings had already been implemented in that branch. Pull
request #33 subsequently merged those changes into the default branch at
`dacc9f4`. This record distinguishes the implemented resolutions from the
remaining issue state; it does not claim that GitHub issues are closed.

| Issue | Resolution and verification |
| --- | --- |
| #6 | Saved density now changes home/workspace spacing immediately and after reload. Saved animations control power/radial menu transitions, respecting Reduce Motion. |
| #15 | Real Gateway unit sets `PrivatePIDs=yes`; installation verifies the effective property after restart and rolls back on mismatch. Unit-policy regression reads the `[Service]` settings. |
| #16 | `moosd` admits at most 32 clients globally and four per peer UID, disconnects idle control clients after 20 seconds, and releases admission slots on handler exit/start failure. Unit limits are 64 tasks, 128 MiB, 50% CPU. Installer verifies effective task/memory limits. Idle and slot-recovery regressions pass. |
| #17 | Already superseded by the Rust Gateway and authenticated administrator release flow. No Gateway Python payload remains active. Signed exact artifact sets, staging and tamper rejection are covered by administrator/installer tests. |
| #18 | All five privileged shell entry points establish trusted PATH, clear shell/Python/loader/temp overrides and set the C locale before external commands. Environment policy regression covers each entry point. |
| #19 | Incremental UTF-8 decoding on CLI input and guest output preserves split characters without changing local Protocol v1. TTY input is raw; control keys reach the guest. Terminal settings and signal handlers are restored on normal exit, exceptions and catchable termination signals. Real QEMU terminal/reconnect test and split-UTF-8/PTY regressions pass. |
| #20 | Initial device JSON is written to a private temporary file, ownership/mode applied, synced, renamed and directory-synced. Failed initialization cannot expose an empty active store. Root device administration uses the service's expected root/Gateway-group owner policy for both reads and mutations. Failure and unsafe-owner regressions pass. |
| #21 | Already uses one absolute 20-second deadline per authenticated frame, rather than resetting on each read. Added continuous-byte slow-drip regression; existing admission/slot-release test passes. Completed requests can continue on long-lived sessions. |
| #22 | Runtime staging is locked across Instance IDs. Both trees are prepared before activation; the staged QEMU is validated under `moos-runtime` with network/filesystem/resource restrictions. Linux `renameat2(RENAME_EXCHANGE)` switches directories atomically; failed activation restores the prior runtime. Only active and one previous QEMU tree remain after success. Regression injects failure at each activation operation and SIGKILL immediately after exchange. |
| #23 | Added path-filtered host/guest policy CI covering host, scripts, systemd, configs, overlay, patches and tests. Runs behavioral control/CLI/runtime/installer tests and shell checks without installing MOOS services. Real guest builds and privileged deployment checks remain explicit integration checks. |
| #24 | All external workflow actions now use full commit SHAs resolved from GitHub. Version comments remain; weekly Dependabot updates and pin regression are present. Jobs retain scoped permissions. |
| #25 | Already pinned QEMU 11.0.3 with Linux host seccomp. Rebuilt guest, observed emulator version and successfully launched QEMU with sandbox enabled; guest and terminal smoke tests pass. |
| #26 | Already pins stable Buildroot 2026.05.1 at an exact commit. Added release maintenance procedure. Development and release guest builds and QEMU acceptance are checked separately. |
| #27 | Existing trusted Gateway installer lock verified by concurrent-invocation regression. |
| #28 | Current Rust Gateway replaces fixed binary/unit paths transactionally and removes its temporary backups after health verification; it no longer creates Gateway release directories on each upgrade. Administrator releases already retain active plus rollback, with repeated-upgrade tests. |
| #29 | Already wired: app → `HomeViewModel` → `LiveMOOSStateService`; metadata cache/composer, system bar, workspace and settings are consumed by that path. Legacy `LiveMOOSStateProviding` is gone. Mock service is used only by previews/tests. This change completes the appearance controls (#6). |
| #30 | Already limits disk cache to 1 MiB, checking file size and bounding subsequent reads against growth. Existing oversized-file and round-trip XCTest regressions retained. |
| #31 | Existing multicast snapshots retain only the newest value per subscriber and detach on termination. Mock buffering is now explicit too. Added cancellation, surviving subscriber, latest-value and resubscription XCTest coverage. |

## Compatibility and operational notes

- Terminal v1 remains UTF-8 text, not an arbitrary binary transport. Invalid
  sequences use replacement characters; valid split sequences are preserved.
  An attached guest console may stay idle indefinitely; control requests have
  the 20-second idle bound. The single-console runtime policy still applies.
- Runtime activation requires Linux directory-exchange support and the existing
  host Python interpreter (standard library only). It fails closed on unsupported
  filesystems. A SIGKILL after the exchange leaves the new active runtime and
  the previous tree intact; ordinary failures roll back automatically. Stale
  previous trees are pruned after the next successful staging operation.
- Privileged environment normalization happens after the OS starts the shell;
  launchers must still use a trusted interpreter environment.
- There are no added guest packages or protocol changes. Existing outer QEMU
  sandboxing remains the primary isolation boundary. Host limits may reject
  workloads above the newly documented connection/resource bounds.

## Verification

Executed locally: Rust release build, `cargo fmt`, Clippy with warnings denied,
30 Rust tests, 8 focused host regressions, existing Python authentication,
protocol, CLI, runtime, installer, administrator release, guest policy, identity,
iOS structure and distribution tests; shell syntax and ShellCheck warning/error
checks. SC1007 is excluded for the intentional POSIX `CDPATH= cd` idiom.

Development guest build and QEMU boot/login/DHCP/utilities/reboot/poweroff passed.
The real framed terminal test passed including restarting the daemon while
QEMU remained running, reconnection, and guest shutdown. QEMU 11.0.3 accepted
and started with seccomp enabled. The release build passed final-image root
credential validation; its QEMU test booted and rejected blank-password root
login. The stable Buildroot tag was independently resolved through GitHub and
matched the configured commit.

GitHub Actions verification on 2026-09-05:

- [iOS run 33987524083](https://github.com/DasEtwa/MOOS/actions/runs/33987524083)
  passed on `d3beab4`: simulator build, all 35 XCTest cases (including stream
  cancellation/latest-value coverage), and unsigned physical-iPhone IPA build.
- [Gateway run 33987524056](https://github.com/DasEtwa/MOOS/actions/runs/33987524056)
  passed on `d3beab4`: Rust checks, release builds and integration tests.
- [Host run 33989014276](https://github.com/DasEtwa/MOOS/actions/runs/33989014276)
  passed on `ba447b0`. The initial CI run exposed a launcher test dependency on
  local QEMU build artifacts; that dry-run test now creates temporary fixtures.
  Production sources are unchanged from the successful iOS/Gateway runs.
- The post-merge `main` run [33989672458](https://github.com/DasEtwa/MOOS/actions/runs/33989672458)
  passed its unsigned physical-device job, Host, and Gateway jobs. Its iOS
  simulator job failed before compilation because the hosted runner did not
  provide the requested `iPhone 16` / `iOS 18.5` destination; the preceding
  `Low-bugs` iOS run passed. This is runner-image drift, not a newly observed
  product failure.

No privileged MOOS installation was performed: effective post-install properties and staging under
the real service account must also be checked on the deployment host. The
installers now reject/roll back effective-policy mismatches automatically.
