#!/usr/bin/env python3
"""Build the ticketed XR restore-OS bootset without device I/O."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from boot_stage import selected_ticket, require_stage_args
from host_tools import configure as configure_host_tools
from prepare_erase_cfw import check_artifacts
from prepare_normal_bootset import COMPONENTS, component, identity, pristine_component

ROOT = Path(__file__).resolve().parent
BOOTSET = ROOT / "work/restore-bootset"
IBSS = ROOT / "work/restore/iBSS.restore.raw.patched"
IBEC = ROOT / "work/restore/iBEC.restore.raw.patched"
DEVICE_TREE = ROOT / "work/restore/DeviceTree.restore.raw"
KERNEL = ROOT / "kernelcache.patched-0x891-full-aks.im4p"
RAMDISK = ROOT / "RestoreRamDisk.patched.im4p"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def run(*args: object) -> None:
    subprocess.run([str(arg) for arg in args], check=True)


def build(ticket: Path, output: Path = BOOTSET, serial: bool = False) -> dict:
    ticket = selected_ticket(ticket, require_complete=False)
    check_artifacts()
    require_stage_args(IBSS, "restore", serial)
    require_stage_args(IBEC, "restore", serial)
    if not DEVICE_TREE.is_file():
        raise RuntimeError(f"missing restore DeviceTree: {DEVICE_TREE}")
    items = identity()
    with tempfile.TemporaryDirectory(prefix="xra1n-restore-bootset-") as temporary:
        stage = Path(temporary)
        shutil.copy2(IBSS, stage / "iBSS.raw")
        run("img4", "-i", pristine_component(items, "iBEC"),
            "-o", stage / "iBEC.img4", "-R", IBEC, "-M", ticket, "-T", "ibec")
        for source, name, fourcc in COMPONENTS:
            run("img4", "-i", component(items, source), "-o", stage / name,
                "-M", ticket, "-T", fourcc)
        run("img4", "-i", pristine_component(items, "DeviceTree"),
            "-o", stage / "DeviceTree.img4", "-R", DEVICE_TREE,
            "-M", ticket, "-T", "rdtr")
        run("img4", "-i", RAMDISK, "-o", stage / "RestoreRamdisk.img4",
            "-M", ticket, "-T", "rdsk")
        run("img4", "-i", KERNEL, "-o", stage / "Kernelcache.img4",
            "-M", ticket, "-T", "rkrn")

        for image in stage.glob("*.img4"):
            extracted_ticket = stage / f".{image.name}.im4m"
            run("img4", "-i", image, "-m", extracted_ticket)
            if extracted_ticket.read_bytes() != ticket.read_bytes():
                raise RuntimeError(f"ticket mismatch: {image.name}")
        for name, expected in (("iBEC.img4", IBEC), ("DeviceTree.img4", DEVICE_TREE)):
            extracted = stage / f".{name}.raw"
            run("img4", "-i", stage / name, "-o", extracted)
            if digest(extracted) != digest(expected):
                raise RuntimeError(f"payload mismatch: {name}")

        output.mkdir(parents=True, exist_ok=True)
        records = {}
        for source in sorted(stage.iterdir()):
            if source.name.startswith("."):
                continue
            target = output / source.name
            shutil.copy2(source, target)
            records[source.name] = {"bytes": target.stat().st_size, "sha256": digest(target)}
        manifest = {"schema": 1, "mode": "restore", "profileID": "iphone11,8-n841ap-22H374",
                    "serial": serial,
                    "ticketSha256": digest(ticket), "artifacts": records}
        (output / "xra1n-boot.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        (output / "SHA256SUMS").write_text("".join(
            f"{record['sha256']}  {name}\n" for name, record in sorted(records.items())))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticket", type=Path, default=ROOT / "tickets/apticket.im4m")
    parser.add_argument("--bootset", type=Path, default=BOOTSET)
    parser.add_argument("--serial", action="store_true", help="require serial-patched iBoot stages")
    args = parser.parse_args()
    configure_host_tools()
    result = build(args.ticket, args.bootset, args.serial)
    print(f"[+] restore bootset: {args.bootset}; ticket={result['ticketSha256']}")


if __name__ == "__main__":
    main()
