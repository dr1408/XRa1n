"""Offline checks for the SSHRD installer; no USB or SSH connections."""

from __future__ import annotations

import plistlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import install_sshrd_userland as installer


class SSHRDInstallerTests(unittest.TestCase):
    def test_three_targets_match_observed_device_manifest(self) -> None:
        self.assertEqual(set(installer.TARGETS),
                         {"coreauthd", "ctkd", "mobileactivationd"})
        for device_path, pristine_hash in installer.TARGETS.values():
            self.assertTrue(device_path.startswith("/mnt1/"))
            self.assertEqual(len(pristine_hash), 64)

    def test_mounts_use_roles_not_fixed_devices(self) -> None:
        script = installer.MOUNT_SCRIPT
        self.assertIn("system=$(volume_for_role System)", script)
        self.assertIn("data=$(volume_for_role Data)", script)
        self.assertIn("preboot=$(volume_for_role Preboot)", script)
        self.assertIn('/sbin/mount_apfs "$system" /mnt1', script)
        self.assertIn('/sbin/mount_apfs "$data" /mnt2', script)
        self.assertIn('/sbin/mount_apfs -o rdonly "$preboot" /mnt6', script)
        self.assertIn("/sbin/mount -u -o rw", script)

    def test_procursus_archive_extracts_expected_tree(self) -> None:
        self.assertEqual(installer.digest(installer.ARCHIVE.read_bytes()),
                         installer.BOOTSTRAP_SHA256)
        with tempfile.TemporaryDirectory() as directory:
            root = installer.unpack_bootstrap(Path(directory))
            self.assertTrue((root / "usr/bin/dpkg").is_file())
            self.assertTrue((root / "prep_bootstrap.sh").is_file())

    def test_jbboot_artifact_is_pinned(self) -> None:
        self.assertEqual(installer.digest(installer.JBBOOT_ARTIFACT.read_bytes()),
                         installer.JBBOOT_SHA256)
        self.assertEqual(installer.JBBOOT_UPGRADABLE_SHA256, {
            "42fd6bca0327fc7759768382e325e4a9564b044fac3d0f9c1be9c4f052a28088",
            "7fcaa73960ba36bacfa3e99eed5e4ef834f737629dc7c3f18a2b5f041287dd30",
            "8d6f719206c8245b9ce1364fec0877b6503aa5f1c2dc803c7288badda714e1bb",
        })

    def test_jbboot_cache_builder_is_exact_and_idempotent(self) -> None:
        document = {
            "VersionNumber": 7,
            "LaunchDaemons": {f"job-{index}": {} for index in range(629)},
            "AppExtensions": {},
            "SystemLibraryTreeState": {},
        }
        source = plistlib.dumps(document, fmt=plistlib.FMT_BINARY)
        patched = installer.build_jbboot_cache(source)
        parsed = plistlib.loads(patched)
        # The builder adds both jbboot and Dropbear.
        self.assertEqual(len(parsed["LaunchDaemons"]), 631)
        self.assertEqual(parsed["LaunchDaemons"][installer.JBBOOT_CACHE_KEY],
                         installer.JBBOOT_JOB)
        self.assertEqual(installer.JBBOOT_JOB["KeepAlive"],
                         {"SuccessfulExit": False})
        self.assertEqual(installer.build_jbboot_cache(patched), patched)

    def test_jbboot_cache_builder_rejects_conflicting_job(self) -> None:
        document = {
            "VersionNumber": 7,
            "LaunchDaemons": {installer.JBBOOT_CACHE_KEY: {"Label": "wrong"}},
        }
        source = plistlib.dumps(document, fmt=plistlib.FMT_BINARY)
        with self.assertRaisesRegex(RuntimeError, "conflicting"):
            installer.build_jbboot_cache(source)


if __name__ == "__main__":
    unittest.main()
