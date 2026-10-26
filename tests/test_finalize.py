"""Offline checks for the normal-boot finalizer; never opens SSH."""

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("xra1n_finalize", ROOT / "tools/xra1n-finalize.py")
assert SPEC is not None and SPEC.loader is not None
finalizer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(finalizer)


class FinalizerTests(unittest.TestCase):
    def test_preferenceloader_deb_is_pinned(self):
        self.assertTrue(finalizer.PREF_DEB.is_relative_to(
            ROOT / "install-bundle/iphone11,8-22H374"))
        self.assertEqual(finalizer.hashlib.sha256(finalizer.PREF_DEB.read_bytes()).hexdigest(),
                         finalizer.PREF_DEB_SHA)

    def test_check_and_apply_render_valid_shell(self):
        real_run = subprocess.run
        for mode in ("--check", "--apply"):
            with self.subTest(mode=mode):
                with patch.object(sys, "argv", ["xra1n-finalize.py", mode]), \
                     patch.object(finalizer.subprocess, "run") as mocked:
                    mocked.return_value.returncode = 0
                    self.assertEqual(finalizer.main(), 0)
                    script = mocked.call_args.kwargs["input"]
                self.assertEqual(real_run(["/bin/sh", "-n"], input=script).returncode, 0)
                self.assertIn(b"preferenceloader_2.3.3_iphoneos-arm64.deb", script)
                self.assertIn(b"pref_target=/var/jb/usr/lib/TweakInject", script)
                self.assertIn(b'pref_dst_dylib="$pref_target/PreferenceLoader.dylib"', script)
                self.assertIn(f"sileo_sha={finalizer.SILEO_BINARY_SHA256}".encode(), script)
                self.assertIn(b"uicache -i org.coolstar.SileoStore", script)
                self.assertIn(b"grep -Fxq 'Path: /Applications/Sileo.app'", script)
                self.assertIn(b"uicache -p /Applications/Sileo.app", script)
                self.assertNotIn(b"uicache -u /var/jb/Applications/Sileo.app", script)
                self.assertLess(script.index(b"apt-get check"),
                                script.index(b"uicache -p /Applications/Sileo.app"))


if __name__ == "__main__":
    unittest.main()
