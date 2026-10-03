#!/usr/bin/env python3
"""Buildroot preparation rejects undeclared inputs except the checked dl cache."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PREPARE = ROOT / 'scripts/prepare-buildroot-tree.sh'
CLEAN_GIT_CACHE = ROOT / 'scripts/clean-buildroot-git-cache.sh'
BUILD = ROOT / 'scripts/build.sh'


def run(*args, check=True, env=None):
    return subprocess.run(args, check=check, text=True, capture_output=True, env=env)


class BuildrootPolicy(unittest.TestCase):
    def test_pristine_expected_and_extra_dirty_states(self):
        with tempfile.TemporaryDirectory(prefix='moos-buildroot-policy-') as name:
            root = Path(name)
            repo, patches = root / 'buildroot', root / 'patches'
            repo.mkdir(); patches.mkdir()
            run('git', '-C', str(repo), 'init', '-q')
            run('git', '-C', str(repo), 'config', 'user.email', 'test@example.invalid')
            run('git', '-C', str(repo), 'config', 'user.name', 'MOOS test')
            (repo / 'tracked').write_text('base\n')
            (repo / 'unrelated').write_text('base\n')
            (repo / '.gitignore').write_text('ignored-input\ndl/\n**/nested-cache/\n')
            run('git', '-C', str(repo), 'add', 'tracked', 'unrelated', '.gitignore')
            run('git', '-C', str(repo), 'commit', '-qm', 'base')
            (repo / 'tracked').write_text('expected\n')
            patch = run('git', '-C', str(repo), 'diff', '--', 'tracked').stdout
            (patches / '0001-expected.patch').write_text(patch)
            run('git', '-C', str(repo), 'restore', 'tracked')

            run('sh', str(PREPARE), str(repo), str(patches))
            self.assertEqual((repo / 'tracked').read_text(), 'expected\n')
            run('sh', str(PREPARE), str(repo), str(patches))

            (repo / 'untracked').write_text('attacker-controlled\n')
            rejected = run('sh', str(PREPARE), str(repo), str(patches), check=False)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn('untracked Buildroot inputs', rejected.stderr)
            (repo / 'untracked').unlink()

            (repo / 'ignored-input').write_text('attacker-controlled\n')
            rejected = run('sh', str(PREPARE), str(repo), str(patches), check=False)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn('untracked Buildroot inputs', rejected.stderr)
            (repo / 'ignored-input').unlink()

            (repo / 'dl').mkdir()
            (repo / 'dl/cache.tar').write_text('hash-verified cache fixture\n')
            (repo / 'dl/nested').mkdir()
            (repo / 'dl/nested/cache.tar').write_text('nested hash-verified cache fixture\n')

            git_cache = repo / 'dl/glibc/git'
            git_cache.mkdir(parents=True)
            run('git', '-C', str(git_cache), 'init', '-q')
            external = root / 'external-git-cache'
            external.mkdir()
            sentinel = external / 'preserve-me'
            sentinel.write_text('outside the Buildroot tree\n')
            (git_cache / 'legitimate-source-symlink').symlink_to(sentinel)
            marker = root / 'hook-ran'
            hook = git_cache / '.git/hooks/reference-transaction'
            hook.write_text(f'#!/bin/sh\nprintf executed > "{marker}"\n')
            hook.chmod(0o755)
            rejected = run('sh', str(PREPARE), str(repo), str(patches), check=False)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn('refusing reusable Buildroot Git caches', rejected.stderr)
            self.assertFalse(marker.exists(), 'untrusted Git hook executed during inspection')

            run('sh', str(CLEAN_GIT_CACHE), str(repo))
            self.assertFalse(git_cache.exists())
            self.assertTrue((repo / 'dl/cache.tar').is_file())
            self.assertTrue((repo / 'dl/nested/cache.tar').is_file())
            self.assertTrue(sentinel.is_file(), 'cache cleanup followed an internal symlink')
            run('sh', str(PREPARE), str(repo), str(patches))

            symlink_cache = repo / 'dl/attacker/git'
            symlink_cache.parent.mkdir(parents=True)
            symlink_cache.symlink_to(external, target_is_directory=True)
            rejected = run('sh', str(PREPARE), str(repo), str(patches), check=False)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn('refusing symlinks in Buildroot download cache', rejected.stderr)
            cleanup = run('sh', str(CLEAN_GIT_CACHE), str(repo), check=False)
            self.assertNotEqual(cleanup.returncode, 0)
            self.assertTrue(sentinel.is_file(), 'cache cleanup followed a symlink')
            symlink_cache.unlink()

            linked_buildroot = root / 'buildroot-symlink'
            linked_buildroot.mkdir()
            (linked_buildroot / 'dl').symlink_to(external, target_is_directory=True)
            rejected = run('sh', str(PREPARE), str(linked_buildroot), str(patches), check=False)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn('refusing symlinked Buildroot download directory', rejected.stderr)
            cleanup = run('sh', str(CLEAN_GIT_CACHE), str(linked_buildroot), check=False)
            self.assertNotEqual(cleanup.returncode, 0)
            self.assertTrue(sentinel.is_file(), 'cleanup followed a symlinked dl root')

            mounted_cache = repo / 'dl/glibc/git'
            mounted_cache.mkdir(parents=True)
            protected = mounted_cache / 'protected-data'
            protected.write_text('must remain when a mount boundary is reported\n')
            fake_bin = root / 'fake-bin'
            fake_bin.mkdir()
            fake_mountpoint = fake_bin / 'mountpoint'
            fake_mountpoint.write_text(
                '#!/bin/sh\n[ "$1" = "-q" ] && [ "$2" = "--" ] && '
                '[ "$3" = "$TEST_MOUNTPOINT" ]\n'
            )
            fake_mountpoint.chmod(0o755)
            mount_env = os.environ.copy()
            mount_env['PATH'] = str(fake_bin) + os.pathsep + mount_env.get('PATH', '')
            mount_env['TEST_MOUNTPOINT'] = str(mounted_cache)
            rejected = run('sh', str(CLEAN_GIT_CACHE), str(repo), check=False, env=mount_env)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn('mounted Buildroot cache directory', rejected.stderr)
            self.assertTrue(protected.is_file(), 'cleanup crossed a reported mount boundary')
            fake_mountpoint.unlink()
            fake_bin.rmdir()
            protected.unlink()
            mounted_cache.rmdir()
            mounted_cache.parent.rmdir()

            newline_dir = repo / '\ndl'
            newline_dir.mkdir()
            (newline_dir / 'outside-cache').write_text('not a download-cache entry\n')
            rejected = run('sh', str(PREPARE), str(repo), str(patches), check=False)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn('untracked Buildroot inputs', rejected.stderr)
            (newline_dir / 'outside-cache').unlink()
            newline_dir.rmdir()
            run('sh', str(PREPARE), str(repo), str(patches))

            (repo / 'dlx').mkdir()
            (repo / 'dlx/cache.tar').write_text('unverified lookalike\n')
            rejected = run('sh', str(PREPARE), str(repo), str(patches), check=False)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn('untracked Buildroot inputs', rejected.stderr)
            (repo / 'dlx/cache.tar').unlink()
            (repo / 'dlx').rmdir()
            (repo / 'nested/nested-cache').mkdir(parents=True)
            (repo / 'nested/nested-cache/stale.tar').write_text('ignored stale input\n')
            rejected = run('sh', str(PREPARE), str(repo), str(patches), check=False)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn('untracked Buildroot inputs', rejected.stderr)
            (repo / 'nested/nested-cache/stale.tar').unlink()
            (repo / 'nested/nested-cache').rmdir()
            (repo / 'nested').rmdir()

            (repo / 'unrelated').write_text('attacker-controlled\n')
            rejected = run('sh', str(PREPARE), str(repo), str(patches), check=False)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn('differ from the declared patch set', rejected.stderr)

            run('git', '-C', str(repo), 'add', 'unrelated')
            rejected = run('sh', str(PREPARE), str(repo), str(patches), check=False)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn('staged Buildroot changes', rejected.stderr)

    def test_every_buildroot_profile_checks_download_hashes(self):
        for name in [
            'moos_qemu_x86_64_defconfig',
            'moos_qemu_x86_64_release_defconfig',
            'moos_native_x86_64_defconfig',
            'moos_native_installer_x86_64_defconfig',
        ]:
            config = (ROOT / 'configs' / name).read_text()
            self.assertIn('BR2_DOWNLOAD_FORCE_CHECK_HASHES=y', config, name)

    def test_build_pins_and_cleans_the_checked_download_cache(self):
        build = BUILD.read_text()
        cleaner = CLEAN_GIT_CACHE.read_text()
        self.assertIn('BR2_DL_DIR="$BUILDROOT_DIR/dl"', build)
        self.assertIn('export BR2_DL_DIR', build)
        self.assertIn('clean-buildroot-git-cache.sh', build)
        self.assertIn('mountpoint', cleaner)
        self.assertIn('-xdev', cleaner)
        self.assertIn('-delete', cleaner)
        self.assertNotIn('rm -rf', cleaner)
        prepare_at = build.index('prepare-buildroot-tree.sh')
        cleanup_trap_at = build.index('trap cleanup_buildroot_git_cache 0')
        first_build_at = build.index('make -C "$BUILDROOT_DIR"')
        self.assertLess(prepare_at, cleanup_trap_at)
        self.assertLess(cleanup_trap_at, first_build_at)

    def test_installer_rust_build_scripts_use_a_host_runnable_abi(self):
        post_build = (ROOT / 'scripts/native-installer-post-build.sh').read_text()
        self.assertIn('host_linker=$(command -v cc)', post_build)
        self.assertIn('CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER=$host_linker', post_build)
        self.assertIn('unset MAKEFLAGS MFLAGS GNUMAKEFLAGS CARGO_MAKEFLAGS', post_build)
        self.assertIn('getconf GNU_LIBC_VERSION', post_build)
        self.assertIn('target_libc="$target_dir/lib/libc.so.6"', post_build)
        self.assertNotIn('$HOST_DIR/bin/x86_64-buildroot-linux-gnu-gcc', post_build)

    @unittest.skipUnless(shutil.which('sh'), 'POSIX shell required for Buildroot post-build harness')
    def test_installer_rust_build_abi_guard_and_environment(self):
        def run_case(host_glibc='2.35', target_versions='GLIBC_2.2.5\nGLIBC_2.39',
                     machine='x86_64-linux-gnu', include_target_libc=True):
            with tempfile.TemporaryDirectory(prefix='moos-installer-abi-') as name:
                root = Path(name)
                fake_bin = root / 'mock-bin'
                fake_bin.mkdir()
                for tool, body in {
                    'cc': '#!/bin/sh\nprintf \'%s\\n\' "$MOCK_CC_MACHINE"\n',
                    'getconf': '#!/bin/sh\nprintf \'glibc %s\\n\' "$MOCK_HOST_GLIBC"\n',
                    'strings': '#!/bin/sh\nprintf \'%s\\n\' "$MOCK_STRINGS_OUTPUT"\n',
                    'cargo': (
                        '#!/bin/sh\n'
                        'printf "MAKEFLAGS=<%s>\\nMFLAGS=<%s>\\nGNUMAKEFLAGS=<%s>\\n" '
                        '"${MAKEFLAGS-}" "${MFLAGS-}" "${GNUMAKEFLAGS-}" > "$MOCK_CARGO_LOG"\n'
                        'printf "CARGO_MAKEFLAGS=<%s>\\nLINKER=<%s>\\n" '
                        '"${CARGO_MAKEFLAGS-}" "${CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER-}" '
                        '>> "$MOCK_CARGO_LOG"\n'
                    ),
                }.items():
                    path = fake_bin / tool
                    path.write_text(body)
                    path.chmod(0o755)

                target_dir = root / 'target root'
                for relative in ('usr/bin', 'boot/grub', 'etc', 'lib'):
                    (target_dir / relative).mkdir(parents=True, exist_ok=True)
                (target_dir / 'etc/shadow').write_text('root:!:locked fixture\n')
                if include_target_libc:
                    (target_dir / 'lib/libc.so.6').write_text('mock target libc\n')
                build_dir = root / 'build dir'
                cargo_output = (
                    build_dir / 'moos-installer-cargo/x86_64-unknown-linux-gnu/release'
                    / 'moos-native-installer'
                )
                cargo_output.parent.mkdir(parents=True)
                cargo_output.write_bytes(b'mock installer binary')
                cargo_log = root / 'cargo-env.txt'
                env = os.environ.copy()
                env['PATH'] = str(fake_bin) + os.pathsep + env.get('PATH', '')
                env.update({
                    'BUILD_DIR': str(build_dir),
                    'MOCK_CC_MACHINE': machine,
                    'MOCK_HOST_GLIBC': host_glibc,
                    'MOCK_STRINGS_OUTPUT': target_versions,
                    'MOCK_CARGO_LOG': str(cargo_log),
                    'MAKEFLAGS': '--jobserver-auth=3,4 -j',
                    'MFLAGS': '-j',
                    'GNUMAKEFLAGS': '--jobserver-auth=5,6 -j',
                    'CARGO_MAKEFLAGS': '--jobserver-auth=7,8 -j',
                })
                result = run('sh', str(ROOT / 'scripts/native-installer-post-build.sh'),
                             str(target_dir), check=False, env=env)
                copied = (target_dir / 'usr/bin/moos-native-installer').is_file()
                return (
                    result,
                    cargo_log.read_text() if cargo_log.exists() else None,
                    copied,
                    str(fake_bin / 'cc'),
                )

        accepted, cargo_env, copied, mock_linker = run_case()
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertIsNotNone(cargo_env)
        self.assertIn('MAKEFLAGS=<>', cargo_env)
        self.assertIn('MFLAGS=<>', cargo_env)
        self.assertIn('GNUMAKEFLAGS=<>', cargo_env)
        self.assertIn('CARGO_MAKEFLAGS=<>', cargo_env)
        self.assertIn(f'LINKER=<{mock_linker}>', cargo_env)
        self.assertTrue(copied)

        rejected_cases = (
            {'host_glibc': '2.39', 'target_versions': 'GLIBC_2.2.5\nGLIBC_2.35'},
            {'machine': 'aarch64-linux-gnu'},
            {'include_target_libc': False},
            {'target_versions': 'GLIBC_2.2.5\nGLIBC_PRIVATE'},
        )
        for case in rejected_cases:
            with self.subTest(case=case):
                result, cargo_env, _, _ = run_case(**case)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIsNone(cargo_env, 'Cargo must not run when ABI compatibility is unknown')

    def test_reproducibility_runs_when_native_build_scripts_change(self):
        workflow = (ROOT / '.github/workflows/native-reproducibility.yml').read_text()
        self.assertIn("'scripts/native-*'", workflow)


if __name__ == '__main__':
    unittest.main()
