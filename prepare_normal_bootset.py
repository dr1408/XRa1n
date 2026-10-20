#!/usr/bin/env python3
"""Prepare an isolated XR 22H374 normal-boot set (no device I/O)."""

from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from patch_devicetree import NORMAL_PATCHES, apply_patches, parse, resolve_path

ROOT = Path(__file__).resolve().parent
CFW = ROOT / "work/cfw"
SOURCES = ROOT / "work/normal"
BOOTSET = ROOT / "work/normal-bootset"
KERNEL = ROOT / "kernelcache.patched-0x891-full-aks.im4p"
TICKET = ROOT / "tickets/apticket.im4m"
STOCK_IBOOT_SHA256 = "8d748ce2190a5889ecd4933bd212f90d1308d44d8cee1880ed92b4bbeb057753"
COMPONENTS = (
    ("RestoreLogo", "RestoreLogo.img4", "rlgo"),
    ("ANE", "ANE.img4", "anef"),
    ("AOP", "AOP.img4", "aopf"),
    ("AVE", "AVE.img4", "avef"),
    ("GFX", "GFX.img4", "gfxf"),
    ("ISP", "ISP.img4", "ispf"),
    ("RestoreTrustCache", "RestoreTrustCache.img4", "rtsc"),
    ("SIO", "SIO.img4", "siof"),
    ("WCHFirmwareUpdater", "WCH.img4", "wchf"),
    ("SEP", "SEP.img4", "rsep"),
)


def run(*args: object) -> None:
    subprocess.run([str(arg) for arg in args], check=True)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def identity() -> dict:
    manifest = plistlib.loads((CFW / "BuildManifest.plist").read_bytes())
    if manifest.get("ProductBuildVersion") != "22H374":
        raise RuntimeError("BuildManifest is not 22H374")
    matches = [item for item in manifest["BuildIdentities"]
               if item.get("Info", {}).get("DeviceClass") == "n841ap"
               and item.get("Info", {}).get("Variant") == "Customer Erase Install (IPSW)"]
    if len(matches) != 1:
        raise RuntimeError("expected exactly one n841ap erase build identity")
    items = matches[0]["Manifest"]
    for normal, restore in (("KernelCache", "RestoreKernelCache"),
                            ("DeviceTree", "RestoreDeviceTree"), ("SEP", "RestoreSEP")):
        if items[normal]["Info"]["Path"] != items[restore]["Info"]["Path"]:
            raise RuntimeError(f"normal and restore {normal} differ; separate payload required")
    return items


def component(items: dict, name: str) -> Path:
    path = CFW / items[name]["Info"]["Path"]
    if not path.is_file():
        raise RuntimeError(f"missing {name}: {path}")
    return path


def make_sources(items: dict) -> None:
    SOURCES.mkdir(parents=True, exist_ok=True)
    for name in ("iBSS", "iBEC"):
        stock = SOURCES / f"{name}.raw"
        # work/cfw/iBEC was modified by earlier restore experiments and no
        # longer has the guarded pristine bytes. Use the archived stock raw.
        archived = ROOT / "work/restore/iBEC.restore.raw" if name == "iBEC" else None
        if archived is not None:
            shutil.copy2(archived, stock)
        else:
            run("img4", "-i", component(items, name), "-o", stock)
        if digest(stock) != STOCK_IBOOT_SHA256:
            raise RuntimeError(f"{name} pristine iBoot source changed; refusing to patch")
        output = Path(str(stock) + ".patched")
        output.unlink(missing_ok=True)
        with (SOURCES / f"{name}.patch.log").open("w") as log:
            subprocess.run([sys.executable, str(ROOT / "patch_iboot.py"),
                            str(stock), "normal", "--serial"], check=True,
                           stdout=log, stderr=subprocess.STDOUT)
        if not output.is_file() or output.read_bytes() == stock.read_bytes():
            raise RuntimeError(f"{name} normal boot patch failed")

    dt_stock = SOURCES / "DeviceTree.raw"
    run("img4", "-i", component(items, "DeviceTree"), "-o", dt_stock)
    dt_patched = SOURCES / "DeviceTree.normal.raw"
    root = parse(dt_stock.read_bytes())
    results = apply_patches(root, NORMAL_PATCHES)
    dt_patched.write_bytes(root.encode())
    verify = parse(dt_patched.read_bytes())
    if resolve_path(verify, "/defaults").find_prop("content-protect"):
        raise RuntimeError("DeviceTree content-protect removal failed")
    for node, prop in (("/chosen", "ephemeral-storage"),):
        entry = resolve_path(verify, node).find_prop(prop)
        if entry is None or entry.value != struct.pack("<I", 1):
            raise RuntimeError(f"DeviceTree normal property wrong: {node}/{prop}")
    if resolve_path(verify, "/defaults").find_prop("no-effaceable-storage"):
        raise RuntimeError("DeviceTree test must not contain /defaults/no-effaceable-storage")
    (SOURCES / "DeviceTree.patch.log").write_text("\n".join(results) + "\n")
    print(f"[+] normal sources ready: {SOURCES}")


def build(items: dict, ticket: Path, kernel: Path) -> None:
    if not ticket.is_file() or not kernel.is_file():
        raise RuntimeError("fresh ticket and downloaded patched kernel artifact are required")
    meta = ticket.with_suffix(".json")
    if meta.is_file() and json.loads(meta.read_text()).get("sha256") != digest(ticket):
        raise RuntimeError("ticket metadata hash mismatch")
    with tempfile.TemporaryDirectory(prefix="xra1n-normal-") as temp:
        stage = Path(temp)
        shutil.copy2(SOURCES / "iBSS.raw.patched", stage / "iBSS.raw")
        run("img4", "-i", ROOT / "work/restore-bootset/iBEC.img4", "-o", stage / "iBEC.img4",
            "-R", SOURCES / "iBEC.raw.patched", "-M", ticket, "-T", "ibec")
        for source, target, fourcc in COMPONENTS:
            run("img4", "-i", component(items, source), "-o", stage / target,
                "-M", ticket, "-T", fourcc)
        run("img4", "-i", component(items, "DeviceTree"),
            "-o", stage / "DeviceTree.img4", "-R", SOURCES / "DeviceTree.normal.raw",
            "-M", ticket, "-T", "rdtr")
        run("img4", "-i", kernel, "-o", stage / "Kernelcache.img4",
            "-M", ticket, "-T", "rkrn")
        for image in stage.glob("*.img4"):
            embedded = stage / f".{image.name}.ticket"
            run("img4", "-i", image, "-m", embedded)
            if embedded.read_bytes() != ticket.read_bytes():
                raise RuntimeError(f"ticket mismatch: {image.name}")
        for image, expected in (("iBEC.img4", SOURCES / "iBEC.raw.patched"),
                                ("DeviceTree.img4", SOURCES / "DeviceTree.normal.raw")):
            extracted = stage / f".{image}.raw"
            run("img4", "-i", stage / image, "-o", extracted)
            if extracted.read_bytes() != expected.read_bytes():
                raise RuntimeError(f"payload mismatch: {image}")
        expected_kernel = stage / ".expected.kernel"
        actual_kernel = stage / ".actual.kernel"
        run("img4", "-i", kernel, "-o", expected_kernel)
        run("img4", "-i", stage / "Kernelcache.img4", "-o", actual_kernel)
        if expected_kernel.read_bytes() != actual_kernel.read_bytes():
            raise RuntimeError("patched kernel payload changed during wrapping")
        usb_policy_patch = (struct.unpack_from("<II", actual_kernel.read_bytes(), 0x2870520)
                            == (0xD2800020, 0xD65F03C0))
        BOOTSET.mkdir(parents=True, exist_ok=True)
        for path in stage.iterdir():
            if not path.name.startswith("."):
                shutil.copy2(path, BOOTSET / path.name)
        names = sorted(p for p in BOOTSET.iterdir() if p.suffix == ".img4" or p.name == "iBSS.raw")
        records = {p.name: {"bytes": p.stat().st_size, "sha256": digest(p)} for p in names}
        document = {"schema": 1, "mode": "normal", "profileID": "iphone11,8-n841ap-22H374",
                    "ticketSha256": digest(ticket), "kernelArtifactSha256": digest(kernel),
                    "kernelUSBRestrictedModeRestorePredicate": usb_policy_patch,
                    "artifacts": records}
        (BOOTSET / "xra1n-boot.json").write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
        (BOOTSET / "SHA256SUMS").write_text("".join(
            f"{records[p.name]['sha256']}  {p.name}\n" for p in names))
    print(f"[+] ticketed normal boot set: {BOOTSET}")
    if usb_policy_patch:
        print("[+] kernel includes Liter8 normal-boot USB Restricted Mode restore-predicate patch")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources-only", action="store_true", help="prepare normal iBSS/iBEC/DT without a ticket")
    parser.add_argument("--ticket", type=Path, default=TICKET)
    parser.add_argument("--kernel", type=Path, default=KERNEL)
    args = parser.parse_args()
    items = identity()
    make_sources(items)
    if not args.sources_only:
        build(items, args.ticket.resolve(), args.kernel.resolve())


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError, KeyError) as error:
        raise SystemExit(f"[!] {error}") from error
