#!/usr/bin/env python3
"""Static GitHub Actions least-privilege and Native host-device policy gate."""
from pathlib import Path
import os
import re
import stat
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WorkflowSecurity(unittest.TestCase):
    def test_actions_are_immutable_and_untrusted_prs_are_read_only(self):
        for workflow in (ROOT / '.github/workflows').glob('*.yml'):
            text = workflow.read_text()
            self.assertNotIn('pull_request_target:', text, workflow)
            for action in re.findall(r'^\s*uses:\s*([^\s#]+)', text, re.MULTILINE):
                if action.startswith('./'):
                    continue
                self.assertRegex(action, r'^[^@]+@[0-9a-f]{40}$', workflow)
            if 'pull_request:' in text:
                before_jobs = text.split('\njobs:', 1)[0]
                self.assertNotRegex(before_jobs, r'contents:\s*write', workflow)

    def test_native_workflows_never_target_host_block_devices(self):
        for workflow in (ROOT / '.github/workflows').glob('native-*.yml'):
            text = workflow.read_text()
            self.assertNotRegex(text, r'/dev/(?:sd[a-z]|nvme\d+n\d+|loop\d+)', workflow)
            self.assertNotIn('pull_request_target:', text, workflow)
            self.assertNotIn('secrets.', text, workflow)

    def test_installer_build_changes_trigger_dynamic_gates(self):
        for name in [
            'native-installer-e2e.yml',
            'native-installer-adversarial.yml',
            'native-cross-host.yml',
            'native-reproducibility.yml',
        ]:
            text = (ROOT / '.github/workflows' / name).read_text()
            self.assertIn("'scripts/build.sh'", text, name)
            self.assertIn("'scripts/prepare-buildroot-tree.sh'", text, name)
            self.assertIn("'scripts/clean-buildroot-git-cache.sh'", text, name)

    def test_both_base_installs_exist_before_multidisk_acceptance(self):
        text = (ROOT / '.github/workflows/native-installer-e2e.yml').read_text()
        bios_install = text.index('native_installer.py --boot bios')
        uefi_install = text.index('native_installer.py --boot uefi')
        first_multidisk = text.index('native_installer_multidisk.py')
        self.assertLess(bios_install, first_multidisk)
        self.assertLess(uefi_install, first_multidisk)

    def test_policy_checks_committed_diff_and_generated_boundaries(self):
        text = (ROOT / '.github/workflows/native-security-policy.yml').read_text()
        event_header = text.split('\njobs:', 1)[0]
        self.assertIn('fetch-depth: 0', text)
        self.assertIn('git diff --check "origin/$GITHUB_BASE_REF...HEAD"', text)
        self.assertIn('PUSH_BEFORE: ${{ github.event.before }}', text)
        self.assertIn('DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}', text)
        self.assertIn('git hash-object -t tree /dev/null', text)
        self.assertNotRegex(event_header, r'(?m)^\s+paths:')

    def test_tracked_tree_excludes_generated_outputs_and_credentials(self):
        raw = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, check=True,
                             capture_output=True).stdout
        tracked = [os.fsdecode(path) for path in raw.split(b'\0') if path]
        root_outputs = {
            'buildroot', 'output', 'host-tools', 'dl', 'images', 'qemu-run',
            'target', 'staging', 'sysroot', 'target-rootfs', 'build',
        }
        generated_directories = {
            '.build', 'deriveddata', 'xcuserdata', '.venv', 'venv',
            '.cache', '__pycache__', 'target',
        }
        generated_suffixes = {
            '.ext2', '.img', '.qcow2', '.raw', '.bzimage', '.o', '.obj',
            '.a', '.so', '.log', '.pid', '.sock', '.pyc', '.xcuserstate',
            '.efi', '.fd', '.elf', '.dll', '.dylib', '.exe', '.class',
        }
        credential_suffixes = {
            '.key', '.p12', '.pfx', '.mobileprovision', '.provisionprofile',
        }
        violations = []
        private_key_re = re.compile(
            re.escape(b'-----' + b'BEGIN ') +
            rb'(?:(?:RSA|DSA|EC|OPENSSH|ENCRYPTED) )?PRIVATE KEY' +
            re.escape(b'-----')
        )
        for label in (b'', b'RSA ', b'EC ', b'OPENSSH ', b'ENCRYPTED '):
            self.assertIsNotNone(private_key_re.search(
                b'-----' + b'BEGIN ' + label + b'PRIVATE KEY' + b'-----'
            ))
        for name in tracked:
            parts = name.split('/')
            folded = [part.casefold() for part in parts]
            base = folded[-1]
            if (folded[0] in root_outputs or
                    (len(parts) == 1 and base in {'moos-admin-release.tar', 'moos-admin-release.tar.sig'}) or
                    any(part in {item.casefold() for item in generated_directories}
                        for part in folded[:-1]) or
                    any(base.endswith(suffix) for suffix in generated_suffixes) or
                    re.search(r'\.so\.\d', base) or
                    any(base == item or base.startswith(item + '.')
                        for item in ('id_rsa', 'id_ed25519')) or
                    base.endswith(tuple(credential_suffixes)) or
                    'secrets' in folded or 'credentials' in folded or
                    (base.startswith('.env') and base != '.env.example')):
                violations.append(name)
                continue
            path = ROOT.joinpath(*parts)
            info = path.lstat()
            if info.st_size <= 4 * 1024 * 1024 and stat.S_ISREG(info.st_mode):
                if private_key_re.search(path.read_bytes()):
                    violations.append(name)
        self.assertEqual(violations, [], 'tracked generated/credential inputs: ' + repr(violations))

        ignored_tracked = subprocess.run(
            ['git', 'ls-files', '-ci', '--exclude-standard', '-z'], cwd=ROOT,
            check=True, capture_output=True
        ).stdout
        self.assertEqual(ignored_tracked, b'', 'tracked files match generated/secret ignore rules')

        ignore = (ROOT / '.gitignore').read_text()
        for rule in ['/buildroot/', '/output/', '/host-tools/', '/dl/', '/images/',
                     '/qemu-run/', '/target/', '/staging/', '/sysroot/',
                     '/target-rootfs/', '/build/', '*.ext2', '*.img', '*.qcow2',
                     '*.raw', '*.bzImage', '*.o', '*.obj', '*.a', '*.so', '*.so.*',
                     '*.pyc', '*.log', '*.pid', '*.sock', '*.swp', '*.swo',
                     '.cache/', '.pytest_cache/', '__pycache__/', '*.xcuserstate',
                     '/moos-admin-release.tar', '/moos-admin-release.tar.sig',
                     '*.key', '*.p12', '*.pfx', '*.mobileprovision',
                     '*.provisionprofile', 'secrets/', 'credentials/', '.env',
                     '*.pem']:
            self.assertIn(rule, ignore.splitlines(), 'missing ignore rule: ' + rule)


if __name__ == '__main__':
    unittest.main()
