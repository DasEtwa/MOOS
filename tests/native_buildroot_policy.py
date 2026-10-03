#!/usr/bin/env python3
"""Buildroot preparation accepts only the exact declared tracked patch set."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PREPARE = ROOT / 'scripts/prepare-buildroot-tree.sh'


def run(*args, check=True):
    return subprocess.run(args, check=check, text=True, capture_output=True)


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
            (repo / '.gitignore').write_text('ignored-input\ndl/\n')
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
            run('sh', str(PREPARE), str(repo), str(patches))

            (repo / 'unrelated').write_text('attacker-controlled\n')
            rejected = run('sh', str(PREPARE), str(repo), str(patches), check=False)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn('differ from the declared patch set', rejected.stderr)

            run('git', '-C', str(repo), 'add', 'unrelated')
            rejected = run('sh', str(PREPARE), str(repo), str(patches), check=False)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn('staged Buildroot changes', rejected.stderr)


if __name__ == '__main__':
    unittest.main()
