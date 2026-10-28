"""Offline checks for the SSHRD installer; no USB or SSH connections."""

from __future__ import annotations

import io
import inspect
import os
import plistlib
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import install_sshrd_userland as installer


class SSHRDInstallerTests(unittest.TestCase):
    def test_gsscred_deny_is_pinned_and_accepts_previous_release(self) -> None:
        path, expected = installer.TWEAK_STACK_FILES["lhook.deny"]
        self.assertEqual(installer.digest(path.read_bytes()), expected)
        self.assertIn("GSSCred", path.read_text().splitlines())
        source = inspect.getsource(installer.install_guarded_tweak_stack)
        self.assertIn(
            "a37ebebdd424eb139cc565471b4e3f71b1af44cf9dac7f0898d96418ba8aba1a",
            source,
        )

    def test_missing_bundle_restores_only_verified_local_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "install-bundle/iphone11,8-22H374"
            archive_path = root / "dist/install-bundle-iphone11.8-22H374.tar.gz"
            archive_path.parent.mkdir()
            payload = b"reviewed fixture\n"
            checksum = installer.digest(payload)
            manifest = f"{checksum}  runtime/probe\n".encode()
            prefix = f"install-bundle/{bundle.name}/"
            with tarfile.open(archive_path, "w:gz") as archive:
                for name, data in (("SHA256SUMS", manifest), ("runtime/probe", payload)):
                    member = tarfile.TarInfo(prefix + name)
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
            with patch.object(installer, "BUNDLE", bundle), \
                 patch.object(installer, "BUNDLE_ARCHIVE", archive_path), \
                 patch.object(installer, "BUNDLE_ARCHIVE_SHA256", "0" * 64):
                with self.assertRaisesRegex(RuntimeError, "archive SHA-256 mismatch"):
                    installer.ensure_install_bundle()
                self.assertFalse(bundle.exists())
            with patch.object(installer, "BUNDLE", bundle), \
                 patch.object(installer, "BUNDLE_ARCHIVE", archive_path), \
                 patch.object(installer, "BUNDLE_ARCHIVE_SHA256", installer.digest(archive_path.read_bytes())):
                installer.ensure_install_bundle()
                self.assertEqual((bundle / "runtime/probe").read_bytes(), payload)
                installer.ensure_install_bundle()  # idempotent, no extraction needed
                (bundle / "runtime/probe").write_bytes(b"tampered")
                with self.assertRaisesRegex(RuntimeError, "hash mismatch"):
                    installer.ensure_install_bundle()

    def test_all_install_inputs_use_one_bundle_root(self) -> None:
        paths = [installer.ARCHIVE, installer.SETUP_SIGNED,
                 installer.COMMCENTER_ARTIFACT, installer.COMMCENTER_RECORDS,
                 installer.JBBOOT_ARTIFACT, installer.SILEO_ARTIFACT]
        paths.extend(path for path, _ in installer.NORMAL_SSH_FILES.values())
        paths.extend(path for path, _ in installer.PAIR_FILES.values())
        paths.extend(path for path, _ in installer.LAUNCHD_HOOK_FILES.values())
        paths.extend(path for path, _ in installer.TWEAK_STACK_FILES.values())
        paths.extend(path for path, _, _, _ in installer.ICON_HELPER_FILES.values())
        paths.extend(path for path, _, _, _ in installer.WIFI_FILES.values())
        paths.extend(path for path, _, _, _ in installer.XR_RUNTIME_FILES.values())
        self.assertTrue(all(path.is_relative_to(installer.BUNDLE) for path in paths))

    def test_wifi_artifacts_are_pinned_and_installer_is_guarded(self) -> None:
        expected = {
            "wifid": ("/mnt1/usr/sbin/wifid", 0o755),
            "wifi-keychain-shim.dylib": (
                "/mnt2/jb/usr/lib/wifi-keychain-shim.dylib", 0o755),
        }
        self.assertEqual(set(installer.WIFI_FILES), set(expected))
        for name, (source, sha256, target, mode) in installer.WIFI_FILES.items():
            self.assertEqual((target, mode), expected[name])
            self.assertEqual(installer.digest(source.read_bytes()), sha256)
        self.assertEqual(installer.digest((installer.ROOT / "bins/wifid").read_bytes()),
                         installer.WIFID_PRISTINE_SHA256)
        source = inspect.getsource(installer.install_wifi_shim)
        self.assertIn("wifid.pristine", source)
        self.assertIn("refusing unknown wifid pre-image", source)
        self.assertIn("refusing unknown Wi-Fi shim pre-image", source)
        self.assertIn("wifid rollback", source)
        self.assertNotIn("killall", source)
        self.assertNotIn("kickstart", source)

    def test_scoped_icon_helpers_are_pinned_and_installed_without_restart(self) -> None:
        expected = {
            "systemhook.dylib": "/mnt1/usr/lib/systemhook.dylib",
            "sbextissue": "/mnt2/jb/usr/bin/sbextissue",
        }
        self.assertEqual(set(installer.ICON_HELPER_FILES), set(expected))
        for name, (source, sha256, target, mode) in installer.ICON_HELPER_FILES.items():
            self.assertEqual(target, expected[name])
            self.assertEqual(mode, 0o755)
            self.assertEqual(installer.digest(source.read_bytes()), sha256)
        source = inspect.getsource(installer.install_icon_helpers)
        self.assertIn("refusing unknown icon-helper pre-image", source)
        self.assertIn("installed icon-helper hash mismatch", source)
        self.assertNotIn("kickstart", source)
        self.assertNotIn("killall", source)

    def test_icon_helper_install_rejects_unknown_existing_file(self) -> None:
        class USB:
            def script(self, command):
                if command.startswith("( test -e ") or command.startswith("test -f "):
                    return "YES\n"
                return ""

            def read(self, _):
                return b"unknown existing file"

        patches = {name: source.read_bytes()
                   for name, (source, _, _, _) in installer.ICON_HELPER_FILES.items()}
        with self.assertRaisesRegex(RuntimeError, "refusing unknown icon-helper pre-image"):
            installer.install_icon_helpers(USB(), "fixture", patches)

    def test_icon_helper_install_copies_both_files_and_is_idempotent(self) -> None:
        class USB:
            files = {}
            writes = 0

            def script(self, command):
                if command.startswith("( test -e "):
                    target = command.split()[3]
                    return "YES\n" if target in self.files else ""
                if command.startswith("test -f "):
                    target = command.split()[2]
                    return "YES\n" if target in self.files else ""
                if command.startswith("/bin/cat "):
                    source, target = command.split(" && ")[0].split()[1::2]
                    self.files[target] = self.files[source]
                    self.writes += 1
                return ""

            def call(self, command, input_data=None):
                if command.startswith("/bin/cat > "):
                    self.files[command.removeprefix("/bin/cat > ")] = input_data
                    return SimpleNamespace(stdout=b"")
                if command.startswith("/usr/bin/stat -f %u:%g:%Lp "):
                    return SimpleNamespace(stdout=b"0:0:755\n")
                raise AssertionError(command)

            def read(self, path):
                return self.files[path]

        usb = USB()
        patches = {name: source.read_bytes()
                   for name, (source, _, _, _) in installer.ICON_HELPER_FILES.items()}
        installer.install_icon_helpers(usb, "fixture", patches)
        self.assertEqual(usb.writes, 2)
        for name, (_, _, target, _) in installer.ICON_HELPER_FILES.items():
            self.assertEqual(usb.files[target], patches[name])
        installer.install_icon_helpers(usb, "fixture", patches)
        self.assertEqual(usb.writes, 2)

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
            "c82fa988cd501eb007aab668f20b8f320ab66e052187ebb48896c97915198d5b",
            "42fd6bca0327fc7759768382e325e4a9564b044fac3d0f9c1be9c4f052a28088",
            "7fcaa73960ba36bacfa3e99eed5e4ef834f737629dc7c3f18a2b5f041287dd30",
            "8d6f719206c8245b9ce1364fec0877b6503aa5f1c2dc803c7288badda714e1bb",
        })

    def test_xr_runtime_files_match_working_device_builds(self) -> None:
        expected = {
            "pfwatch": ("/mnt1/usr/local/bin/pfwatch", 0o755),
            "pfruntimeprobe": ("/mnt1/usr/local/bin/pfruntimeprobe", 0o755),
            "l8persona.dylib": ("/mnt2/jb/usr/lib/TweakInject/l8persona.dylib", 0o755),
            "l8persona.plist": ("/mnt2/jb/usr/lib/TweakInject/l8persona.plist", 0o644),
            "l8localauth.dylib": ("/mnt2/jb/usr/lib/TweakInject/l8localauth.dylib", 0o755),
            "l8localauth.plist": ("/mnt2/jb/usr/lib/TweakInject/l8localauth.plist", 0o644),
            "l8files.dylib": ("/mnt2/jb/usr/lib/TweakInject/l8files.dylib", 0o755),
            "l8files.plist": ("/mnt2/jb/usr/lib/TweakInject/l8files.plist", 0o644),
        }
        self.assertEqual(set(installer.XR_RUNTIME_FILES), set(expected))
        for name, (source, sha256, target, mode) in installer.XR_RUNTIME_FILES.items():
            self.assertEqual((target, mode), expected[name])
            self.assertEqual(installer.digest(source.read_bytes()), sha256)
        self.assertEqual(
            plistlib.loads(installer.XR_RUNTIME_FILES["l8persona.plist"][0].read_bytes()),
            {"Filter": {"Executables": ["installd", "installcoordinationd"]}},
        )
        self.assertEqual(
            plistlib.loads(installer.XR_RUNTIME_FILES["l8localauth.plist"][0].read_bytes()),
            {"Filter": {"Executables": ["nehelper"]}},
        )
        self.assertEqual(
            plistlib.loads(installer.XR_RUNTIME_FILES["l8files.plist"][0].read_bytes()),
            {"Filter": {"Executables": ["fileproviderd", "LocalStorageFileProvider"]}},
        )

    def test_files_marker_is_scoped_and_does_not_restart_services(self) -> None:
        class USB:
            script_text = ""

            def script(self, value):
                self.script_text = value
                return "0:0:600\n"

        usb = USB()
        installer.ensure_files_marker(usb)
        self.assertIn("/mnt2/jb/.xra1n-files", usb.script_text)
        self.assertIn("/bin/chmod 0600", usb.script_text)
        self.assertNotIn("lhookctl", usb.script_text)
        self.assertNotIn("killall", usb.script_text)

    def test_persona_marker_is_provisioned_without_enabling_lhook(self) -> None:
        class USB:
            script_text = ""

            def script(self, value):
                self.script_text = value
                return "0:0:600\n"

        usb = USB()
        installer.ensure_persona_marker(usb)
        self.assertIn("/mnt2/jb/.liter8-persona", usb.script_text)
        self.assertIn('[ ! -L "$marker" ]', usb.script_text)
        self.assertIn('(umask 077; : > "$marker")', usb.script_text)
        self.assertIn("/bin/chmod 0600", usb.script_text)
        self.assertNotIn(".liter8-localauth", usb.script_text)
        self.assertNotIn("lhookctl", usb.script_text)
        self.assertNotIn("killall", usb.script_text)
        self.assertIn("ensure_persona_marker(usb)",
                      inspect.getsource(installer.install_xr_runtime_fixes))

    def test_localauth_marker_is_provisioned_without_restarting_nehelper(self) -> None:
        class USB:
            script_text = ""

            def script(self, value):
                self.script_text = value
                return "0:0:600\n"

        usb = USB()
        installer.ensure_localauth_marker(usb)
        self.assertIn("/mnt2/jb/.liter8-localauth", usb.script_text)
        self.assertIn('[ ! -L "$marker" ]', usb.script_text)
        self.assertIn('(umask 077; : > "$marker")', usb.script_text)
        self.assertIn("/bin/chmod 0600", usb.script_text)
        self.assertNotIn("lhookctl", usb.script_text)
        self.assertNotIn("killall", usb.script_text)
        self.assertIn("ensure_localauth_marker(usb)",
                      inspect.getsource(installer.install_xr_runtime_fixes))

    def test_runtime_workflow_excludes_files_diagnostic(self) -> None:
        workflow = (Path(__file__).resolve().parents[1] /
                    ".github/workflows/xr-runtime-fixes.yml").read_text()
        self.assertIn("l8files.dylib", workflow)
        self.assertNotIn("filesdiag", workflow)

    def test_tester_ssh_key_must_be_one_p256_public_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory) / "ssh/xra1n-xr"
            with patch.dict(os.environ, {"XRA1N_SSH_KEY": str(private)}):
                key = installer.normal_ssh_public_key()
                self.assertTrue(private.is_file())
                self.assertEqual(installer.normal_ssh_public_key(), key)
                for invalid in (b"ssh-ed25519 AAAA\n", b"ecdsa-sha2-nistp256 !!!\n",
                                key + key):
                    with self.subTest(invalid=invalid[:40]), self.assertRaises(RuntimeError):
                        installer.validate_ssh_public_key(invalid)
                private.with_suffix(".pub").write_bytes(b"ssh-ed25519 AAAA\n")
                with self.assertRaises(RuntimeError):
                    installer.normal_ssh_public_key()

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
