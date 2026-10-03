#!/usr/bin/env python3
"""Static GitHub Actions least-privilege and Native host-device policy gate."""
from pathlib import Path
import re
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


if __name__ == '__main__':
    unittest.main()
