#!/usr/bin/env python3
"""Tethered, non-erase normal boot for n841ap/22H374."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import run_restore
from boot_stage import require_stage_args, selected_ticket
from host_tools import configure as configure_host_tools, tool as host_tool
from restore_with_ticket import wait_for_recovery
from prepare_normal_bootset import BOOTSET, CFW, KERNEL, SOURCES

ROOT = Path(__file__).resolve().parent
SEQUENCE = (
    ("RestoreLogo.img4", "setpicture 0x1", 0),
    ("ANE.img4", "firmware", 0), ("AOP.img4", "firmware", 3),
    ("AVE.img4", "firmware", 1), ("GFX.img4", "firmware", 0),
    ("ISP.img4", "firmware", 1),
    ("RestoreTrustCache.img4", "firmware", 0),
    ("SIO.img4", "firmware", 0), ("WCH.img4", "firmware", 0),
    ("DeviceTree.img4", "devicetree", 0),
    ("SEP.img4", "rsepfirmware", 0),
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(ticket: Path, serial: bool = False) -> dict:
    manifest = json.loads((BOOTSET / "xra1n-boot.json").read_text())
    if manifest.get("mode") != "normal" or manifest.get("profileID") != "iphone11,8-n841ap-22H374":
        raise RuntimeError("wrong bootset mode/profile")
    if manifest.get("serial") != serial:
        raise RuntimeError("bootset serial mode does not match requested mode")
    if manifest.get("ticketSha256") != sha256(ticket):
        raise RuntimeError("bootset ticket does not match selected ticket")
    if manifest.get("kernelArtifactSha256") != sha256(KERNEL):
        raise RuntimeError("bootset kernel does not match downloaded artifact")
    names = ("iBSS.raw", "iBEC.img4", "Kernelcache.img4") + tuple(x[0] for x in SEQUENCE)
    for name in names:
        path = BOOTSET / name
        record = manifest["artifacts"].get(name)
        if record is None or not path.is_file() or path.stat().st_size != record["bytes"] or sha256(path) != record["sha256"]:
            raise RuntimeError(f"missing/changed boot artifact: {name}")
    if (BOOTSET / "RestoreRamdisk.img4").exists():
        raise RuntimeError("normal bootset unexpectedly contains a ramdisk")
    return manifest


def mark(timing, event: str) -> None:
    timing.write(f"{datetime.now(timezone.utc).isoformat(timespec='milliseconds')} "
                 f"monotonic_ns={time.monotonic_ns()} {event}\n")
    timing.flush()


def send(name: str, command: str | None, log, timing) -> None:
    # The RP2350 hub has intermittently dropped recovery USB during large
    # firmware uploads while iBEC remained alive. Resume in the same iBEC
    # session instead of recapturing a ticket or restarting from DFU.
    attempts = 1 if name == "iBEC.img4" else 5
    for attempt in range(1, attempts + 1):
        mark(timing, f"{name} attempt={attempt} recovery-wait-start")
        wait_for_recovery(20)
        try:
            mark(timing, f"{name} upload-start")
            subprocess.run(["irecovery", "-f", str(BOOTSET / name)], check=True,
                           stdout=log, stderr=subprocess.STDOUT)
            mark(timing, f"{name} upload-complete")
            if command:
                subprocess.run(["irecovery", "-c", command], check=True,
                               stdout=log, stderr=subprocess.STDOUT)
            mark(timing, f"{name} command={command or 'none'} complete")
            print(f"[+] {name}: {command or 'uploaded'}", flush=True)
            return
        except subprocess.CalledProcessError:
            mark(timing, f"{name} attempt={attempt} failed")
            if attempt == attempts:
                raise
            print(f"[*] transient USB failure on {name}; retry {attempt}/{attempts}", flush=True)
            time.sleep(2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build or tether-boot the isolated normal set; never erase/restore")
    parser.add_argument("--boot", action="store_true", help="actually send images to USBLiter8-pwned DFU")
    parser.add_argument("--ticket", type=Path,
                        help="erase-restore AP ticket (default: tickets/apticket.im4m); never requests TSS")
    parser.add_argument("--serial", action="store_true",
                        help="route boot console to DCSD and require /dev/ttyACM0 capture")
    args = parser.parse_args()
    configure_host_tools()
    if not KERNEL.is_file():
        raise RuntimeError(f"missing downloaded patched kernel: {KERNEL}")
    subprocess.run([sys.executable, str(ROOT / "prepare_normal_bootset.py"), "--sources-only",
                    *(["--serial"] if args.serial else [])], check=True)
    require_stage_args(SOURCES / "iBSS.raw.patched", "normal", args.serial)
    require_stage_args(SOURCES / "iBEC.raw.patched", "normal", args.serial)
    ticket = selected_ticket(args.ticket)
    print(f"[+] using erase-restore ticket SHA-384: {hashlib.sha384(ticket.read_bytes()).hexdigest().upper()}", flush=True)
    if not args.boot:
        subprocess.run([sys.executable, str(ROOT / "prepare_normal_bootset.py"),
                        "--ticket", str(ticket), *(["--serial"] if args.serial else [])], check=True)
        validate(ticket, args.serial)
        print(f"[+] offline bootset validated: {BOOTSET}", flush=True)
        return
    if not run_restore.usb_has("1227"):
        raise RuntimeError("normal tethered boot requires USBLiter8-pwned DFU (05ac:1227)")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    serial_process, serial_log = run_restore.start_serial(timestamp) if args.serial else (None, None)
    if serial_log is not None:
        print(f"[+] serial capture: {serial_log}", flush=True)
    subprocess.run([str(host_tool("usbliter8ctl")),
                    "boot", str(SOURCES / "iBSS.raw.patched")], check=True)
    wait_for_recovery(30)
    subprocess.run([sys.executable, str(ROOT / "prepare_normal_bootset.py"),
                    "--ticket", str(ticket), *(["--serial"] if args.serial else [])], check=True)
    manifest = validate(ticket, args.serial)
    if manifest["kernelUSBRestrictedModeRestorePredicate"]:
        print("[+] kernel includes Liter8 normal-boot USB Restricted Mode policy patch", flush=True)

    log_path = ROOT / "logs/normal" / f"upload-{timestamp}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timing_path = ROOT / "logs/normal" / f"timing-{timestamp}.log"
    with log_path.open("wb") as log, timing_path.open("w") as timing:
        for name in ("iBSS.raw", "iBEC.img4", "ANE.img4", "AOP.img4", "AVE.img4",
                     "DeviceTree.img4", "SEP.img4", "Kernelcache.img4"):
            mark(timing, f"sha256 {name} {manifest['artifacts'][name]['sha256']}")
        send("iBEC.img4", "go", log, timing)
        # iBEC/go re-enumerates USB. A fixed two-second sleep raced this on
        # the first device test; wait for a responsive recovery command port.
        wait_for_recovery(30)
        # A responsive command port precedes display/firmware readiness. The
        # manual boot showed the blue restore logo, while the scripted boot
        # sent RestoreLogo only ~0.19s after go and did not show it.
        mark(timing, "iBEC.img4 settle-start seconds=2")
        time.sleep(2)
        mark(timing, "iBEC.img4 settle-complete")
        for attempt in range(5):
            result = subprocess.run(["irecovery", "-c", "bgcolor 0 191 255"],
                                    stdout=log, stderr=subprocess.STDOUT)
            if result.returncode == 0:
                break
            if attempt == 4:
                raise RuntimeError("iBEC recovery did not accept bgcolor after re-enumeration")
            time.sleep(1)
        for name, command, delay in SEQUENCE:
            send(name, command, log, timing)
            if delay:
                mark(timing, f"{name} pause-start seconds={delay}")
                time.sleep(delay)
                mark(timing, f"{name} pause-complete")
        send("Kernelcache.img4", None, log, timing)
        # bootx normally drops recovery USB before irecovery sees a reply.
        result = subprocess.run(["irecovery", "-c", "bootx"], stdout=log,
                                stderr=subprocess.STDOUT)
        mark(timing, f"bootx transport-rc={result.returncode}")
        print(f"[*] bootx transport rc={result.returncode}; "
              f"inspect {'serial' if args.serial else 'device/USB state'} for boot result", flush=True)
    if serial_process is not None and serial_process.poll() is not None:
        raise RuntimeError("DCSD logger exited during normal boot")
    time.sleep(5)
    if serial_log is not None:
        serial_text = serial_log.read_bytes().decode("utf-8", "replace").replace("\x00", "")
        if "panic(cpu" in serial_text:
            context = [line for line in serial_text.splitlines() if
                       "panic(cpu" in line or "sep-firmware.img4" in line]
            raise RuntimeError(f"normal boot reached a kernel panic: {context[-3:]}\n"
                               f"serial={serial_log} upload={log_path}")
    print(f"[+] chain sent; serial={serial_log or 'disabled'} upload={log_path} timing={timing_path}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError, KeyError) as error:
        raise SystemExit(f"[!] {error}") from error
