#!/usr/bin/env python3
"""One-command, monitored XRa1n erase-restore for n841ap/22H374."""

from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from boot_stage import RESTORE_COMPLETE, require_stage_args, selected_ticket
from host_tools import ca_bundle, configure as configure_host_tools, tool as host_tool
from restore_with_ticket import ecid_from_query, wait_for_recovery


ROOT = Path(__file__).resolve().parent
CFW = ROOT / "work/cfw"
CA = ca_bundle()
RESTORE_BOOTSET = ROOT / "work/restore-bootset"
RESTORE_SEQUENCE = (
    ("RestoreLogo.img4", "setpicture 0x1", 0),
    ("ANE.img4", "firmware", 0), ("AOP.img4", "firmware", 0),
    ("AVE.img4", "firmware", 1), ("GFX.img4", "firmware", 0),
    ("ISP.img4", "firmware", 1),
    ("RestoreTrustCache.img4", "firmware", 0),
    ("SIO.img4", "firmware", 0), ("WCH.img4", "firmware", 0),
    ("RestoreRamdisk.img4", "ramdisk", 2),
    ("DeviceTree.img4", "devicetree", 0),
    ("SEP.img4", "rsepfirmware", 0),
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments before any device or filesystem side effects."""
    parser = argparse.ArgumentParser(
        description=(
            "Run the complete monitored XRa1n erase-restore for "
            "iPhone11,8 on 22H374. This erases the target device."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare-only", action="store_true",
                      help="prepare the extracted CFW and restore iBSS, without device I/O")
    mode.add_argument("--reuse-prepared", action="store_true",
                      help="verify the existing prepared CFW and restore it without patching again")
    parser.add_argument("--idevicerestore", type=Path,
                        default=host_tool("idevicerestore"),
                        help="select an explicitly built idevicerestore for a controlled handoff test")
    parser.add_argument("--ibec-container", choices=("plain", "template"), default="plain",
                        help="use the plain CFW iBEC or the old template-replacement packaging")
    parser.add_argument("--serial", action="store_true",
                        help="route boot console to DCSD and require /dev/ttyACM0 capture")
    return parser.parse_args(argv)


def run(command: list[object], **kwargs: object) -> subprocess.CompletedProcess:
    printable = " ".join(str(item) for item in command)
    print(f"[cmd] {printable}", flush=True)
    return subprocess.run([str(item) for item in command], check=True, **kwargs)


def usb_has(product: str) -> bool:
    result = subprocess.run(["lsusb"], capture_output=True, text=True, check=True)
    return f"05ac:{product}" in result.stdout.lower()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def last_serial_rejection(path: Path) -> str | None:
    pattern = re.compile(r"Kernelcache image not valid|Kernel IMG4 parse rc=.*|panic\(cpu")
    last = None
    with path.open("r", errors="replace") as stream:
        for line in stream:
            match = pattern.search(line)
            if match:
                last = match.group(0)[:240]
    return last


def send_restore(name: str, command: str | None, output) -> None:
    """Send a single ticketed image, retrying only firmware after iBEC."""
    attempts = 1 if name == "iBEC.img4" else 5
    for attempt in range(1, attempts + 1):
        wait_for_recovery(20)
        try:
            subprocess.run(["irecovery", "-f", str(RESTORE_BOOTSET / name)],
                           check=True, stdout=output, stderr=subprocess.STDOUT)
            if command:
                subprocess.run(["irecovery", "-c", command], check=True,
                               stdout=output, stderr=subprocess.STDOUT)
            print(f"[+] {name}: {command or 'uploaded'}", flush=True)
            return
        except subprocess.CalledProcessError:
            if attempt == attempts:
                raise
            time.sleep(2)


def wait_for_restore_os(timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if usb_has("12a8"):
            print("[+] restore OS enumerated as 05ac:12a8", flush=True)
            return
        time.sleep(1)
    raise RuntimeError("restore OS did not enumerate as 05ac:12a8 after bootx")


def restore_usb_address() -> str | None:
    """Return the sole Restore OS USB address, including its enumeration number."""
    result = subprocess.run(["lsusb"], capture_output=True, text=True, check=True)
    matches = [line.strip() for line in result.stdout.splitlines()
               if "05ac:12a8" in line.lower()]
    if len(matches) > 1:
        raise RuntimeError("more than one Restore OS USB device is connected")
    return matches[0] if matches else None


def stable_restore_usb(deadline: float, stable_for: float = 3) -> str:
    """Avoid starting usbmuxd on the short-lived first 12a8 enumeration."""
    address = None
    since = time.monotonic()
    while time.monotonic() < deadline:
        observed = restore_usb_address()
        now = time.monotonic()
        if observed != address:
            address, since = observed, now
        if address is not None and now - since >= stable_for:
            return address
        time.sleep(0.5)
    raise RuntimeError("Restore OS USB address did not stabilize")


def refresh_usbmuxd(timeout: float = 90) -> tuple[str, subprocess.Popen]:
    """Retry discovery after a 12a8 reset instead of leaving a stale mux running."""
    deadline = time.monotonic() + timeout
    log = ROOT / "logs/restore/usbmuxd-restore.log"
    attempts = 0
    while time.monotonic() < deadline:
        address = stable_restore_usb(deadline)
        attempts += 1
        subprocess.run(["usbmuxd", "-X"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        with log.open("ab") as output:
            process = subprocess.Popen([str(host_tool("usbmuxd")), "-pf"],
                                       stdout=output, stderr=subprocess.STDOUT,
                                       start_new_session=True)
        discovered = False
        try:
            attempt_deadline = min(deadline, time.monotonic() + 8)
            while time.monotonic() < attempt_deadline:
                if process.poll() is not None or restore_usb_address() != address:
                    break
                try:
                    result = subprocess.run(["idevice_id", "-l"], capture_output=True,
                                            text=True, timeout=3)
                except subprocess.TimeoutExpired:
                    result = None
                if result is not None:
                    identities = [line.strip() for line in result.stdout.splitlines()
                                  if line.strip()]
                    if result.returncode == 0 and len(identities) == 1:
                        discovered = True
                        print(f"[+] usbmuxd found Restore OS on attempt {attempts}", flush=True)
                        return identities[0], process
                time.sleep(0.5)
        finally:
            if not discovered and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        print(f"[*] usbmuxd lost Restore OS on attempt {attempts}; rediscovering", flush=True)
    raise RuntimeError(f"usbmuxd could not discover stable Restore OS; see {log}")


def wait_for_restore_service(ecid: str, idevicerestore: Path, timeout: float = 60) -> None:
    """Require idevicerestore itself to see restored before an erase is allowed."""
    deadline = time.monotonic() + timeout
    # The manual recovery that worked after the prior usbmuxd race used this
    # exact saved-ticket, restore-only no-action probe. Restore OS cannot
    # answer the fresh AP/SEP nonce queries of an ordinary recovery probe.
    ticket = selected_ticket(require_complete=False)
    probe = [str(idevicerestore), "-n", "-R", "-T", str(ticket),
             "-d", "-i", ecid, str(CFW)]
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(probe, capture_output=True, text=True, timeout=16)
        except subprocess.TimeoutExpired:
            result = None
        if result is not None and result.returncode == 0 \
                and "Found device in Restore mode" in result.stdout + result.stderr:
            print("[+] idevicerestore no-action probe confirms Restore mode", flush=True)
            return
        time.sleep(2)
    raise RuntimeError("idevicerestore could not discover the restore-mode service")


def connected_xr_ecid() -> str:
    """Pin this restore to the single reviewed XR visible before iBSS handoff."""
    usb = subprocess.run(["lsusb"], capture_output=True, text=True, check=True)
    targets = re.findall(r"\b05ac:(?:1227|1281)\b", usb.stdout.lower())
    if len(targets) != 1:
        raise RuntimeError(f"expected exactly one XR in DFU/Recovery, found {len(targets)}")
    query = subprocess.run(["irecovery", "-q"], capture_output=True,
                           text=True, timeout=10, check=True)
    fields = dict(line.split(":", 1) for line in (query.stdout + query.stderr).splitlines()
                  if ":" in line)
    fields = {name.strip(): value.strip() for name, value in fields.items()}
    if fields.get("CPID", "").lower() != "0x8020" or fields.get("BDID", "").lower() != "0x0c" \
            or fields.get("MODEL") != "n841ap" or fields.get("PRODUCT") != "iPhone11,8":
        raise RuntimeError("connected device is not the reviewed n841ap iPhone XR")
    mode = fields.get("MODE")
    if mode not in {"DFU", "Recovery"}:
        raise RuntimeError(f"connected XR is not in DFU/Recovery: {mode}")
    if mode == "DFU" and fields.get("PWND") != "usbliter8":
        raise RuntimeError("DFU XR is not USBLiter8-pwned")
    number = int(ecid_from_query(query.stdout + query.stderr), 16)
    if not 0 < number < 1 << 64:
        raise RuntimeError("connected XR has an invalid ECID")
    ecid = f"0x{number:x}"
    print(f"[+] pinned {mode} XR ECID for this restore: {ecid}", flush=True)
    return ecid


def clean_runtime() -> None:
    for pid_file in (ROOT / "logs/serial/dcsd.pid",):
        if pid_file.is_file():
            try:
                os.kill(int(pid_file.read_text().strip()), signal.SIGTERM)
            except (OSError, ValueError):
                pass
    # A failed transfer's serial/TSS/restore logs are evidence. A new attempt
    # gets timestamped paths; never erase the old ones during preflight.
    for directory in (ROOT / "logs/serial", ROOT / "logs/restore", ROOT / "logs/tss"):
        directory.mkdir(parents=True, exist_ok=True)


def stop_stale_serial_loggers(logger: Path) -> int:
    """Stop prior XRa1n DCSD loggers before opening the serial device again."""
    victims: set[int] = set()
    logger_arg = str(logger.resolve()).encode()
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            pid = int(proc.name)
            argv = (proc / "cmdline").read_bytes().split(b"\0")
        except (OSError, ValueError):
            continue
        if pid != os.getpid() and logger_arg in argv:
            victims.add(pid)
    for pid in victims:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 2
    remaining = set(victims)
    while remaining and time.monotonic() < deadline:
        remaining = {pid for pid in remaining if Path(f"/proc/{pid}").exists()}
        if remaining:
            time.sleep(0.05)
    for pid in remaining:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    for marker in (ROOT / ".run/dcsd-ready.pid", ROOT / "logs/serial/dcsd.pid"):
        marker.unlink(missing_ok=True)
    return len(victims)


def start_serial(timestamp: str) -> tuple[subprocess.Popen, Path]:
    logger = ROOT / "scripts/dcsd_logger.py"
    log = ROOT / "logs/serial" / f"dcsd-{timestamp}.log"
    ready = ROOT / ".run/dcsd-ready.pid"
    stopped = stop_stale_serial_loggers(logger)
    if stopped:
        print(f"[+] stopped {stopped} stale DCSD logger(s)", flush=True)
    process = subprocess.Popen(
        [sys.executable, "-u", str(logger), "--output", str(log),
         "--ready-file", str(ready), "--parent-pid", str(os.getpid())],
        start_new_session=True,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("DCSD logger exited before opening /dev/ttyACM0")
        if ready.is_file() and ready.read_text().strip() == str(process.pid):
            (ROOT / "logs/serial/dcsd.pid").write_text(f"{process.pid}\n")
            # Normal exit and uncaught exceptions both run this cleanup. The
            # logger also watches its parent for SIGKILL/abrupt termination.
            atexit.register(stop_serial, process)
            print(f"[+] DCSD verified: PID {process.pid}, {log}", flush=True)
            return process, log
        time.sleep(0.1)
    stop_serial(process)
    raise RuntimeError("DCSD logger could not open /dev/ttyACM0")


def stop_serial(process: subprocess.Popen) -> None:
    """Stop this boot's logger without deleting another run's PID markers."""
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    for marker in (ROOT / ".run/dcsd-ready.pid", ROOT / "logs/serial/dcsd.pid"):
        try:
            if marker.read_text().strip() == str(process.pid):
                marker.unlink(missing_ok=True)
        except FileNotFoundError:
            pass


def main(argv: list[str] | None = None) -> None:
    # This must remain the first operation. In particular, --help and invalid
    # arguments must exit before logs are cleaned, DCSD is opened, or anything
    # is transferred to the device.
    args = parse_args(argv)
    configure_host_tools()
    for path in (CFW / "BuildManifest.plist",
                 ROOT / "kernelcache.patched-0x891-full-aks.im4p",
                 ROOT / "RestoreRamDisk.patched.im4p"):
        if not path.is_file():
            raise RuntimeError(f"restore preflight missing: {path}")
    # From-zero preparation changes four CFW files. Repeat restores can
    # instead verify the existing record without touching those files.
    run([sys.executable, ROOT / "prepare_erase_cfw.py",
         "--check" if args.reuse_prepared else "--apply",
         "--ibec-container", args.ibec_container,
         *(["--serial"] if args.serial else [])])
    if args.prepare_only:
        print("[+] CFW prepared; no device action", flush=True)
        return
    for path in (args.idevicerestore, CA):
        if not path.is_file():
            raise RuntimeError(f"restore preflight missing: {path}")
    require_stage_args(ROOT / "work/restore/iBSS.restore.raw.patched", "restore", args.serial)
    run([sys.executable, ROOT / "prepare_boot_stages.py", "--check", "--stage", "restore",
         *(["--serial"] if args.serial else [])])
    ecid = connected_xr_ecid()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    clean_runtime()
    serial_process, serial_log = start_serial(timestamp) if args.serial else (None, None)
    mux_process = None
    try:
        # The reviewed XR iBEC executes but does not deliver the USB remove
        # event that idevicerestore waits for when started in recovery. Use
        # the old hardware-tested handoff: capture only, boot restore OS,
        # then begin idevicerestore from its usbmuxd restore-mode service.
        if not (usb_has("1227") or usb_has("1281")):
            raise RuntimeError(
                "device must begin in USBLiter8-pwned DFU (05ac:1227) "
                "or patched recovery (05ac:1281)"
            )
        run([sys.executable, "-u", ROOT / "restore_with_ticket.py", CFW,
             "--expected-ecid", ecid, "--idevicerestore", args.idevicerestore,
             "--ticket-only", *(["--skip-pico-boot"] if usb_has("1281") else [])])
        ticket = selected_ticket(require_complete=False)
        run([sys.executable, ROOT / "prepare_restore_bootset.py", "--ticket", ticket,
             *(["--serial"] if args.serial else [])])
        manifest = json.loads((RESTORE_BOOTSET / "xra1n-boot.json").read_text())
        if (manifest.get("mode") != "restore" or manifest.get("serial") != args.serial
                or manifest.get("ticketSha256") != sha256_file(ticket)):
            raise RuntimeError("restore bootset does not match captured ticket")
        required = {"iBSS.raw", "iBEC.img4", "Kernelcache.img4"}
        required.update(name for name, _, _ in RESTORE_SEQUENCE)
        if set(manifest["artifacts"]) != required:
            raise RuntimeError("restore bootset manifest has missing or extra images")
        for name, record in manifest["artifacts"].items():
            path = RESTORE_BOOTSET / name
            if not path.is_file() or path.stat().st_size != record["bytes"] or sha256_file(path) != record["sha256"]:
                raise RuntimeError(f"restore bootset changed after preparation: {name}")
        upload = ROOT / "logs/restore" / f"restore-bootset-upload-{timestamp}.log"
        with upload.open("wb") as output:
            send_restore("iBEC.img4", "go", output)
            wait_for_recovery(30)
            for name, command, delay in RESTORE_SEQUENCE:
                send_restore(name, command, output)
                if delay:
                    time.sleep(delay)
            send_restore("Kernelcache.img4", None, output)
            result = subprocess.run(["irecovery", "-c", "bootx"],
                                    stdout=output, stderr=subprocess.STDOUT)
            print(f"[*] restore bootx transport rc={result.returncode}", flush=True)
        wait_for_restore_os()
        udid, mux_process = refresh_usbmuxd()
        print(f"[+] restore-OS usbmuxd identity: {udid}", flush=True)
        wait_for_restore_service(ecid, args.idevicerestore)
        if serial_process is not None and serial_process.poll() is not None:
            raise RuntimeError("DCSD logger exited before erase restore")
        run([sys.executable, "-u", ROOT / "restore_with_ticket.py", CFW,
             "--expected-ecid", ecid, "--idevicerestore", args.idevicerestore,
             "--restore-only"])
        ticket = selected_ticket(require_complete=False)
        if serial_process is not None and serial_process.poll() is not None:
            raise RuntimeError("DCSD logger exited during restore")
        if serial_log is not None:
            rejected = last_serial_rejection(serial_log)
            if rejected:
                raise RuntimeError(f"kernel boot rejected; serial evidence: {rejected}")
        RESTORE_COMPLETE.write_text(json.dumps({
            "profileID": "iphone11,8-n841ap-22H374",
            "ecid": ecid,
            "ticketSha384": hashlib.sha384(ticket.read_bytes()).hexdigest().upper(),
            "completedAtUTC": datetime.now(timezone.utc).isoformat(),
        }, indent=2, sort_keys=True) + "\n")
        print(f"[+] restore completed; serial={serial_log or 'disabled'}; ticket={ticket}")
    finally:
        if mux_process is not None and mux_process.poll() is None:
            mux_process.terminate()
            try:
                mux_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                mux_process.kill()
                mux_process.wait()
        if serial_process is not None:
            stop_serial(serial_process)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"[!] {error}") from error
