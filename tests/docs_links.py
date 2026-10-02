#!/usr/bin/env python3
"""Check local Markdown links in important discovery and Native documents."""
from pathlib import Path
import re
import unittest
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


class DocumentationLinks(unittest.TestCase):
    def test_important_local_targets(self):
        documents = [ROOT / 'README.md', ROOT / 'STEPS.md', *sorted((ROOT / 'docs').rglob('*.md'))]
        for document in documents:
            for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', document.read_text(encoding='utf-8')):
                target = target.split(' "', 1)[0].strip('<>')
                url = urlsplit(target)
                if url.scheme or url.netloc or not url.path:
                    continue
                with self.subTest(document=document.relative_to(ROOT), target=target):
                    self.assertTrue((document.parent / unquote(url.path)).exists(), 'broken local documentation link')


if __name__ == '__main__':
    unittest.main()
