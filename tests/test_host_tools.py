"""Read-only checks for the relocatable, pinned aarch64 host bundle."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import host_tools


class HostToolsTests(unittest.TestCase):
    def test_reviewed_bundle_and_ca_resolve_without_picotool(self) -> None:
        self.assertEqual(host_tools.verify_bundle(), host_tools.BUNDLE)
        self.assertEqual(host_tools.tool("usbliter8ctl"), host_tools.BUNDLE / "usbliter8ctl")
        self.assertEqual(host_tools.tool("idevicerestore"), host_tools.BUNDLE / "idevicerestore")
        self.assertEqual(host_tools.ca_bundle(), host_tools.BUNDLE / "apple-tss-ca-bundle.pem")

    def test_relocated_bundle_is_selected_and_prepended(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            moved = Path(temporary) / "host-tools"
            shutil.copytree(host_tools.BUNDLE, moved, symlinks=True)
            with patch.dict(os.environ, {"XRA1N_TOOLS_DIR": str(moved), "PATH": "/usr/bin"}):
                host_tools.configure()
                self.assertEqual(host_tools.tool("iproxy"), moved / "iproxy")
                self.assertEqual(host_tools.ca_bundle(), moved / "apple-tss-ca-bundle.pem")
                self.assertEqual(os.environ["PATH"].split(os.pathsep)[0], str(moved))

    def test_changed_binary_and_bad_soname_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            moved = Path(temporary) / "host-tools"
            shutil.copytree(host_tools.BUNDLE, moved, symlinks=True)
            (moved / "img4").write_bytes(b"changed")
            with self.assertRaisesRegex(RuntimeError, "hash-mismatched"):
                host_tools.verify_bundle(moved)
            shutil.copy2(host_tools.BUNDLE / "img4", moved / "img4")
            (moved / "lib/libirecovery-1.0.so.5").unlink()
            with self.assertRaisesRegex(RuntimeError, "SONAME"):
                host_tools.verify_bundle(moved)


if __name__ == "__main__":
    unittest.main()
