#!/usr/bin/env python3
"""Tethered SSHRD boot using an explicit saved AP ticket; never capture/restore."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import run_restore
from boot_stage import DEFAULT_TICKET, require_stage_args, selected_ticket
from host_tools import configure as configure_host_tools, tool as host_tool
from restore_with_ticket import wait_for_recovery

ROOT = Path(__file__).resolve().parent
BOOTSET = ROOT / "work/sshrd-bootset"
RAMDISK = ROOT / "RestoreRamDisk.sshrd.im4p"
SEQUENCE = (
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


def run(*args: object, log=None) -> None:
    subprocess.run([str(x) for x in args], check=True,
                   stdout=log, stderr=subprocess.STDOUT if log else None)


def send(name: str, command: str | None, log) -> None:
    for attempt in range(1, 6):
        wait_for_recovery(20)
        try:
            run("irecovery", "-f", BOOTSET / name, log=log)
            if command:
                run("irecovery", "-c", command, log=log)
            print(f"[+] {name}: {command or 'uploaded'}", flush=True)
            return
        except subprocess.CalledProcessError:
            if attempt == 5:
                raise
            print(f"[*] transient USB failure on {name}; retry {attempt}/5", flush=True)
            time.sleep(2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build/boot SSHRD with a saved ticket; no TSS capture or erase")
    parser.add_argument("--ticket", type=Path, default=DEFAULT_TICKET)
    parser.add_argument("--boot", action="store_true")
    parser.add_argument("--serial", action="store_true",
                        help="route boot console to DCSD and require /dev/ttyACM0 capture")
    args = parser.parse_args()
    configure_host_tools()
    ticket = selected_ticket(args.ticket)
    ticket_bytes = ticket.read_bytes()
    ticket_hash = hashlib.sha384(ticket_bytes).hexdigest().upper()
    print(f"[+] erase-restore ticket SHA-384: {ticket_hash}", flush=True)
    run(sys.executable, ROOT / "prepare_boot_stages.py", "--stage", "sshrd",
        *(["--serial"] if args.serial else []))
    require_stage_args(ROOT / "work/sshrd/iBSS.ramdisk.raw.patched", "ramdisk", args.serial)
    require_stage_args(ROOT / "work/sshrd/iBEC.ramdisk.raw.patched", "ramdisk", args.serial)
    run(sys.executable, ROOT / "prepare_sshrd_bootset.py",
        "--ramdisk-im4p", RAMDISK, "--ticket", ticket,
        *(["--serial"] if args.serial else []))
    if not args.boot:
        print(f"[+] SSHRD bootset prepared: {BOOTSET}; no device action", flush=True)
        return
    if not run_restore.usb_has("1227"):
        raise RuntimeError("boot requires USBLiter8-pwned DFU (05ac:1227)")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    serial_process, serial_log = run_restore.start_serial(timestamp) if args.serial else (None, None)
    upload_log = ROOT / "logs/restore" / f"sshrd-upload-{timestamp}.log"
    upload_log.parent.mkdir(parents=True, exist_ok=True)
    with upload_log.open("wb") as log:
        run(host_tool("usbliter8ctl"), "boot",
            BOOTSET / "iBSS.raw", log=log)
        wait_for_recovery(30)
        run("irecovery", "-f", BOOTSET / "iBEC.img4", log=log)
        run("irecovery", "-c", "go", log=log)
        wait_for_recovery(30)
        for name, command, delay in SEQUENCE:
            send(name, command, log)
            if delay:
                time.sleep(delay)
        send("Kernelcache.img4", None, log)
        result = subprocess.run(["irecovery", "-c", "bootx"],
                                stdout=log, stderr=subprocess.STDOUT)
        print(f"[*] bootx transport rc={result.returncode}", flush=True)
    if serial_process is not None and serial_process.poll() is not None:
        raise RuntimeError("DCSD logger exited during SSHRD boot")
    time.sleep(8)
    if serial_log is not None:
        serial = serial_log.read_bytes().decode("utf-8", "replace").replace("\0", "")
        if "panic(cpu" in serial:
            raise RuntimeError(f"kernel panic in {serial_log}")
    print(f"[+] SSHRD chain sent; serial={serial_log or 'disabled'} upload={upload_log}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError, KeyError) as error:
        raise SystemExit(f"[!] {error}") from error
