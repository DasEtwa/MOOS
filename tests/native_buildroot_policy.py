#!/usr/bin/env python3
"""Buildroot preparation rejects undeclared inputs except the checked dl cache."""
from pathlib import Path
import os
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


if __name__ == '__main__':
    unittest.main()
