import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import patch_lockdownd_weakload as patcher
from patch_launchd_weakload import LC_LOAD_WEAK_DYLIB, commands


class LockdowndWeakLoadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "bins/lockdownd").read_bytes()

    def test_release_hook_only(self):
        result = patcher.patch(self.source)
        parsed, _ = commands(result)
        hooks = [blob[24:].split(b"\0", 1)[0] for cmd, blob in parsed
                 if cmd == LC_LOAD_WEAK_DYLIB]
        self.assertEqual(hooks.count(patcher.HOOK[:-1]), 1)
        self.assertEqual(len(hooks), 2)  # Preserve the stock CoreRepairCore weak load.
        self.assertNotIn(patcher.PROBE, result)
        self.assertEqual(result[patcher.EXPECTED_FIRST_SECTION:],
                         self.source[patcher.EXPECTED_FIRST_SECTION:])
        self.assertEqual(hashlib.sha256(result).hexdigest(),
                         "e7b0490764c6e9df2d446baae0c60f40a91e6817acfc3a0d52cbc7b9f990e00a")

    def test_unknown_source_rejected(self):
        changed = bytearray(self.source)
        changed[0x100] ^= 1
        with self.assertRaisesRegex(ValueError, "unknown lockdownd source"):
            patcher.patch(bytes(changed))


if __name__ == "__main__":
    unittest.main()
