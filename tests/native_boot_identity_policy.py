#!/usr/bin/env python3
"""Static gate for the Native duplicate-root fail-closed kernel patch."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NativeBootIdentityPolicy(unittest.TestCase):
    def test_duplicate_partuuid_is_rejected_before_root_mount(self):
        patch = (ROOT / 'patches/linux/0001-refuse-ambiguous-root-partuuid.patch').read_text()
        self.assertIn('class_find_device(&block_class, dev, &cmp', patch)
        self.assertIn('return -ENOTUNIQ;', patch)
        self.assertIn('duplicate PARTUUID=', patch)
        for config in [
            'configs/moos_native_x86_64_defconfig',
            'configs/moos_native_installer_x86_64_defconfig',
        ]:
            self.assertIn('BR2_GLOBAL_PATCH_DIR="../patches board/qemu/patches"',
                          (ROOT / config).read_text())


if __name__ == '__main__':
    unittest.main()
