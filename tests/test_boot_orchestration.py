"""Offline safety checks; never contacts TSS or the phone."""

from __future__ import annotations

import hashlib
import itertools
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import boot_stage
import prepare_erase_cfw
import run_restore


class BootStageTests(unittest.TestCase):
    def test_restore_usb_must_settle_after_second_enumeration(self) -> None:
        first = "Bus 001 Device 048: ID 05ac:12a8 Apple iPhone"
        second = "Bus 001 Device 049: ID 05ac:12a8 Apple iPhone"
        readings = iter((first, None, second, second, second, second))
        clock = itertools.count()
        with patch.object(run_restore, "restore_usb_address",
                          side_effect=lambda: next(readings, second)), \
             patch.object(run_restore, "time", SimpleNamespace(
                 monotonic=lambda: next(clock), sleep=lambda _: None)):
            self.assertEqual(run_restore.stable_restore_usb(30, stable_for=3), second)

    def test_usbmuxd_restarts_after_restore_os_reenumerates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "logs/restore").mkdir(parents=True)
            address = "Bus 001 Device 049: ID 05ac:12a8 Apple iPhone"
            processes = [Mock(), Mock()]
            for process in processes:
                process.poll.return_value = None
            clock = itertools.count()

            def command(args, **_kwargs):
                if args[0] == "lsusb":
                    return Mock(stdout=address + "\n", returncode=0)
                if args[0] == "idevice_id":
                    return Mock(stdout=("" if len(started) == 1 else
                                        "00008020-000E301E2E9A002E\n"), returncode=0)
                self.assertEqual(args, ["usbmuxd", "-X"])
                return Mock(returncode=0)

            started: list[Mock] = []
            def start(*_args, **_kwargs):
                process = processes[len(started)]
                started.append(process)
                return process

            with patch.object(run_restore, "ROOT", root), \
                 patch.object(run_restore, "stable_restore_usb", return_value=address), \
                 patch.object(run_restore, "restore_usb_address", return_value=address), \
                 patch.object(run_restore, "time", SimpleNamespace(
                     monotonic=lambda: next(clock), sleep=lambda _: None)), \
                 patch.object(run_restore.subprocess, "run", side_effect=command), \
                 patch.object(run_restore.subprocess, "Popen", side_effect=start):
                identity, live = run_restore.refresh_usbmuxd(timeout=90)
            self.assertEqual(identity, "00008020-000E301E2E9A002E")
            self.assertIs(live, processes[1])
            processes[0].terminate.assert_called_once()
            processes[1].terminate.assert_not_called()

    def test_restore_service_probe_uses_saved_ticket(self) -> None:
        ticket = Path("/tmp/saved-apticket.im4m")
        with patch.object(run_restore, "selected_ticket", return_value=ticket), \
             patch.object(run_restore.subprocess, "run", return_value=Mock(
                 returncode=0, stdout="Found device in Restore mode", stderr="")) as command:
            run_restore.wait_for_restore_service("0xe301e2e9a002e", Path("idevicerestore"))
        args = command.call_args.args[0]
        self.assertEqual(args[1:5], ["-n", "-R", "-T", str(ticket)])

    def test_restore_runner_boots_restore_os_before_erase(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in ("work/cfw/BuildManifest.plist",
                             "kernelcache.patched-0x891-full-aks.im4p",
                             "RestoreRamDisk.patched.im4p", ".run/ca.pem",
                             "tools/idevicerestore"):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"fixture")
            ticket = root / "tickets/apticket.im4m"
            ticket.parent.mkdir()
            ticket.write_bytes(b"saved-ticket")
            bootset = root / "work/restore-bootset"
            bootset.mkdir(parents=True)
            events = []

            def command(args, **_kwargs):
                if str(args[1]).endswith("prepare_restore_bootset.py"):
                    artifacts = {"iBSS.raw": b"ibss", "iBEC.img4": b"ibec",
                                 "Kernelcache.img4": b"kernel"}
                    for name, data in artifacts.items():
                        (bootset / name).write_bytes(data)
                    (bootset / "xra1n-boot.json").write_text(json.dumps({
                        "mode": "restore",
                        "serial": False,
                        "ticketSha256": hashlib.sha256(ticket.read_bytes()).hexdigest(),
                        "artifacts": {name: {"bytes": len(data),
                                              "sha256": hashlib.sha256(data).hexdigest()}
                                      for name, data in artifacts.items()}
                    }))
                events.append(" ".join(map(str, args)))

            serial = root / "serial.log"
            serial.write_text("")
            with patch.object(run_restore, "ROOT", root), \
                 patch.object(run_restore, "CFW", root / "work/cfw"), \
                 patch.object(run_restore, "CA", root / ".run/ca.pem"), \
                 patch.object(run_restore, "RESTORE_BOOTSET", bootset), \
                 patch.object(run_restore, "RESTORE_COMPLETE", root / "tickets/restore-complete.json"), \
                 patch.object(run_restore, "run", side_effect=command), \
                 patch.object(run_restore, "require_stage_args"), \
                 patch.object(run_restore, "connected_xr_ecid", return_value="0xe301e2e9a002e"), \
                 patch.object(run_restore, "usb_has", side_effect=lambda product: product == "1227"), \
                 patch.object(run_restore, "start_serial") as start_serial, \
                 patch.object(run_restore, "stop_serial"), \
                 patch.object(run_restore, "selected_ticket", return_value=ticket), \
                 patch.object(run_restore, "send_restore", side_effect=lambda name, *_: events.append(f"send {name}")), \
                 patch.object(run_restore, "wait_for_recovery"), \
                 patch.object(run_restore, "wait_for_restore_os", side_effect=lambda: events.append("restore OS ready")), \
                 patch.object(run_restore, "refresh_usbmuxd", side_effect=lambda: (events.append("usbmuxd ready") or "test-udid", Mock(poll=Mock(return_value=0)))), \
                 patch.object(run_restore, "wait_for_restore_service", side_effect=lambda *_: events.append("restore service ready")), \
                 patch.object(run_restore.subprocess, "run", return_value=Mock(returncode=0)), \
                 patch.object(run_restore, "RESTORE_SEQUENCE", ()):
                run_restore.main(["--reuse-prepared", "--idevicerestore",
                                  str(root / "tools/idevicerestore")])
                start_serial.assert_not_called()
            capture = next(i for i, item in enumerate(events) if "--ticket-only" in item)
            build = next(i for i, item in enumerate(events) if "prepare_restore_bootset.py" in item)
            boot = events.index("send iBEC.img4")
            ready = events.index("restore OS ready")
            service = events.index("restore service ready")
            erase = next(i for i, item in enumerate(events) if "--restore-only" in item)
            self.assertLess(capture, build)
            self.assertLess(build, boot)
            self.assertLess(boot, ready)
            self.assertLess(ready, service)
            self.assertLess(service, erase)
            self.assertTrue((root / "tickets/restore-complete.json").is_file())

    def test_restore_ecid_is_pinned_from_dfu_query(self) -> None:
        dfu = ("CPID: 0x8020\nBDID: 0x0c\nECID: 0x000e301e2e9a002e\n"
               "MODE: DFU\nPWND: usbliter8\nPRODUCT: iPhone11,8\nMODEL: n841ap\n")
        def command(args, **_):
            return Mock(stdout=("Bus 001 Device 001: ID 05ac:1227 Apple\n" if args[0] == "lsusb"
                                else dfu), stderr="")
        with patch.object(run_restore.subprocess, "run", side_effect=command):
            self.assertEqual(run_restore.connected_xr_ecid(), "0xe301e2e9a002e")
        with patch.object(run_restore.subprocess, "run", side_effect=lambda args, **_:
                          Mock(stdout=("Bus 001 Device 001: ID 05ac:1227 Apple\n"
                                       if args[0] == "lsusb" else dfu.replace("PWND: usbliter8", "PWND: no")),
                               stderr="")):
            with self.assertRaisesRegex(RuntimeError, "not USBLiter8-pwned"):
                run_restore.connected_xr_ecid()

    def test_restore_reuse_flag_is_exclusive_and_selects_read_only_check(self) -> None:
        self.assertTrue(run_restore.parse_args(["--reuse-prepared"]).reuse_prepared)
        self.assertEqual(run_restore.parse_args(["--prepare-only", "--ibec-container", "template"])
                         .ibec_container, "template")
        with self.assertRaises(SystemExit):
            run_restore.parse_args(["--prepare-only", "--reuse-prepared"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "work/cfw").mkdir(parents=True)
            for relative in ("work/cfw/BuildManifest.plist",
                             "kernelcache.patched-0x891-full-aks.im4p",
                             "RestoreRamDisk.patched.im4p"):
                path = root / relative
                path.write_bytes(b"fixture")
            commands = []
            def capture(command):
                commands.append(command)
                raise RuntimeError("stop before device I/O")
            with patch.object(run_restore, "ROOT", root), \
                 patch.object(run_restore, "CFW", root / "work/cfw"), \
                 patch.object(run_restore, "run", side_effect=capture):
                with self.assertRaisesRegex(RuntimeError, "stop before device I/O"):
                    run_restore.main(["--reuse-prepared"])
            self.assertEqual(commands[0][-3:], ["--check", "--ibec-container", "plain"])

    def test_erase_cfw_pristine_sources_are_small_selected_files(self) -> None:
        names = ("iBSS", "iBEC", "DeviceTree", "KernelCache", "RestoreRamDisk")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfw = root / "cfw"
            backups = root / "pristine"
            items = {}
            for name in names:
                relative = f"Firmware/{name}.im4p"
                source = cfw / relative
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(f"stock-{name}".encode())
                items[name] = {"Info": {"Path": relative},
                               "Digest": hashlib.sha384(source.read_bytes()).digest()}
            with patch.object(prepare_erase_cfw, "CFW", cfw), \
                 patch.object(prepare_erase_cfw, "PRISTINE", backups):
                prepare_erase_cfw.save_pristine(items)
                self.assertEqual(len(list(backups.rglob("*im4p"))), len(names))
                # A later patched CFW must not overwrite the stock backup.
                (cfw / items["iBEC"]["Info"]["Path"]).write_bytes(b"patched")
                prepare_erase_cfw.save_pristine(items)
                self.assertEqual(prepare_erase_cfw.stock(items, "iBEC").read_bytes(),
                                 b"stock-iBEC")

    def test_erase_cfw_refuses_nonstock_source_without_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfw = root / "cfw"
            source = cfw / "iBSS.im4p"
            cfw.mkdir()
            source.write_bytes(b"changed")
            items = {"iBSS": {"Info": {"Path": "iBSS.im4p"},
                              "Digest": hashlib.sha384(b"stock").digest()}}
            with patch.object(prepare_erase_cfw, "CFW", cfw), \
                 patch.object(prepare_erase_cfw, "PRISTINE", root / "pristine"):
                with self.assertRaises(RuntimeError):
                    prepare_erase_cfw.save_pristine(items)

    def test_ibec_container_comparison_keeps_both_paths_separate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            restore = root / "restore"
            restore.mkdir()
            raw = restore / "iBEC.restore.raw.patched"
            raw.write_bytes(b"patched iBEC")
            template = root / "stock.im4p"
            template.write_bytes(b"stock template")
            ticket = root / "ticket.im4m"
            ticket.write_bytes(b"ticket")
            output = root / "comparison"

            def replace(_template, payload, destination, scratch):
                destination.write_bytes(b"\x16\x04ibec-template-" + payload.read_bytes())

            def wrap(payload, destination, scratch):
                destination.write_bytes(b"\x16\x04ibec-plain-" + payload.read_bytes())

            def run_img4(*args):
                if "-M" in args:
                    Path(args[args.index("-o") + 1]).write_bytes(
                        Path(args[2]).read_bytes() + ticket.read_bytes())
                elif "-m" in args:
                    Path(args[args.index("-m") + 1]).write_bytes(ticket.read_bytes())
                else:
                    self.fail(f"unexpected img4 command: {args}")

            with patch.object(prepare_erase_cfw, "RESTORE", restore), \
                 patch.object(prepare_erase_cfw, "identity", return_value={"iBEC": {}}), \
                 patch.object(prepare_erase_cfw, "stock", return_value=template), \
                 patch.object(prepare_erase_cfw, "selected_ticket", return_value=ticket), \
                 patch.object(prepare_erase_cfw, "require_stage_args"), \
                 patch.object(prepare_erase_cfw, "replace_payload", side_effect=replace), \
                 patch.object(prepare_erase_cfw, "wrap_raw_ibec", side_effect=wrap), \
                 patch.object(prepare_erase_cfw, "run", side_effect=run_img4):
                result = prepare_erase_cfw.compare_ibec_containers(ticket, output)
                self.assertEqual(set(result["cases"]), {"template-replace", "plain-raw-wrap"})
                self.assertEqual(result["patchedPayloadSha256"], hashlib.sha256(raw.read_bytes()).hexdigest())
                self.assertNotEqual(result["cases"]["template-replace"]["im4pSha256"],
                                    result["cases"]["plain-raw-wrap"]["im4pSha256"])
                self.assertEqual(template.read_bytes(), b"stock template")
                with self.assertRaisesRegex(RuntimeError, "already exists"):
                    prepare_erase_cfw.compare_ibec_containers(ticket, output)

    def test_stage_literals_are_distinct_and_console_mode_checked(self) -> None:
        from patch_iboot import MODE_PRESETS, apply_serial

        self.assertNotEqual(MODE_PRESETS["normal"], MODE_PRESETS["restore"])
        self.assertNotEqual(MODE_PRESETS["ramdisk"], MODE_PRESETS["restore"])
        self.assertIn("rd=md0", MODE_PRESETS["ramdisk"])
        self.assertNotIn("rd=md0", MODE_PRESETS["normal"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "iBSS.raw.patched"
            path.write_bytes(apply_serial(MODE_PRESETS["normal"], True).encode() + b"\0")
            boot_stage.require_stage_args(path, "normal", True)
            with self.assertRaises(RuntimeError):
                boot_stage.require_stage_args(path, "normal", False)
            with self.assertRaises(RuntimeError):
                boot_stage.require_stage_args(path, "restore", True)
            path.write_bytes(MODE_PRESETS["normal"].encode() + b"\0")
            boot_stage.require_stage_args(path, "normal", False)
            with self.assertRaises(RuntimeError):
                boot_stage.require_stage_args(path, "normal", True)

    def test_serial_flag_is_opt_in(self) -> None:
        from patch_iboot import serial_requested
        self.assertFalse(run_restore.parse_args([]).serial)
        self.assertTrue(run_restore.parse_args(["--serial"]).serial)
        with patch.dict("os.environ", {"XRA1N_SERIAL": "1"}):
            self.assertFalse(serial_requested(["patch_iboot.py", "--no-serial"]))
            self.assertTrue(serial_requested(["patch_iboot.py", "--serial"]))
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            serial_requested(["--serial", "--no-serial"])

    def test_ticket_must_match_completed_erase_restore(self) -> None:
        # Structurally valid, deliberately non-signable DER test fixture.
        data = b"\x30\x0a\x16\x04IM4M\x04\x02hi"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture_dir = root / "archive"
            fixture_dir.mkdir()
            fixture = fixture_dir / f"{hashlib.sha384(data).hexdigest().upper()}.im4m"
            fixture.write_bytes(data)
            marker = root / "restore-complete.json"
            with patch.object(boot_stage, "RESTORE_COMPLETE", marker):
                with self.assertRaises(RuntimeError):
                    boot_stage.selected_ticket(fixture)
                marker.write_text(json.dumps({
                    "profileID": "iphone11,8-n841ap-22H374",
                    "ticketSha384": "0" * 96,
                }))
                with self.assertRaises(RuntimeError):
                    boot_stage.selected_ticket(fixture)
                marker.write_text(json.dumps({
                    "profileID": "iphone11,8-n841ap-22H374",
                    "ticketSha384": hashlib.sha384(fixture.read_bytes()).hexdigest().upper(),
                }))
                self.assertEqual(boot_stage.selected_ticket(fixture), fixture.resolve())

    def test_clean_runtime_preserves_tickets_and_prior_logs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "logs/restore/old.log"
            ticket = root / "tickets/archive/installed.im4m"
            log.parent.mkdir(parents=True)
            ticket.parent.mkdir(parents=True)
            log.write_text("old")
            ticket.write_text("keep")
            with patch.object(run_restore, "ROOT", root):
                run_restore.clean_runtime()
            self.assertEqual(log.read_text(), "old")
            self.assertEqual(ticket.read_text(), "keep")

    def test_stop_serial_only_removes_own_markers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ready = root / ".run/dcsd-ready.pid"
            marker = root / "logs/serial/dcsd.pid"
            ready.parent.mkdir(parents=True)
            marker.parent.mkdir(parents=True)
            ready.write_text("1234\n")
            marker.write_text("5678\n")
            process = Mock(pid=1234)
            process.poll.return_value = None
            with patch.object(run_restore, "ROOT", root):
                run_restore.stop_serial(process)
            process.terminate.assert_called_once()
            process.wait.assert_called_once_with(timeout=2)
            self.assertFalse(ready.exists())
            self.assertEqual(marker.read_text(), "5678\n")

    def test_serial_logger_exits_when_owner_is_gone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ready = root / "ready.pid"
            result = subprocess.run([
                sys.executable, str(Path(__file__).resolve().parents[1] / "scripts/dcsd_logger.py"),
                "--device", str(root / "nonexistent-serial"),
                "--output", str(root / "capture.log"), "--ready-file", str(ready),
                "--parent-pid", "999999999",
            ], timeout=3, check=True)
            self.assertEqual(result.returncode, 0)
            self.assertFalse(ready.exists())


if __name__ == "__main__":
    unittest.main()
