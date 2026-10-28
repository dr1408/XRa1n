import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import patch_wifid_weakload as patcher
from patch_launchd_weakload import LC_LOAD_WEAK_DYLIB, commands


class WifidWeakLoadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "bins/wifid").read_bytes()

    def test_exact_weak_dependency_only(self):
        result = patcher.patch(self.source)
        parsed, _ = commands(result)
        hooks = [blob[24:].split(b"\0", 1)[0] for cmd, blob in parsed
                 if cmd == LC_LOAD_WEAK_DYLIB]
        self.assertEqual(hooks.count(patcher.HOOK[:-1]), 1)
        self.assertEqual(result[patcher.EXPECTED_FIRST_SECTION:],
                         self.source[patcher.EXPECTED_FIRST_SECTION:])
        self.assertEqual(len(result), len(self.source))

    def test_unknown_source_rejected(self):
        changed = bytearray(self.source)
        changed[0x100] ^= 1
        with self.assertRaisesRegex(ValueError, "unknown wifid source"):
            patcher.patch(bytes(changed))


if __name__ == "__main__":
    unittest.main()
