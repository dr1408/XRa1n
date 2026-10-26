#!/usr/bin/env python3
"""Prepare the extracted XR IPSW in-place for the iBSS + CFW erase restore.

No device I/O occurs here. ``--apply`` is the only mode that writes CFW files;
``--check`` verifies an already prepared CFW without modifying it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from boot_stage import require_stage_args, selected_ticket
from host_tools import configure as configure_host_tools
from patch_devicetree import parse, resolve_path


ROOT = Path(__file__).resolve().parent
CFW = ROOT / "work/cfw"
PRISTINE = ROOT / "work/pristine-cfw"
RESTORE = ROOT / "work/restore"
KERNEL = ROOT / "kernelcache.patched-0x891-full-aks.im4p"
RAMDISK = ROOT / "RestoreRamDisk.patched.im4p"
RECORD = RESTORE / "cfw-artifacts.json"
PROFILE = "iphone11,8-n841ap-22H374"
PINNED_ARTIFACTS = {
    KERNEL.name: "900fb9e106ef5c9d9a7085add1ca0133071f9a0e41a3969c69af6d49cb5f0cbe",
    RAMDISK.name: "6fc6e23cbd5a9955aad9d5e4f4025c2145c9e82719660c8bbf751813935f5f76",
}
REPLACEMENTS = {
    "iBEC": "iBEC.im4p",
    "DeviceTree": "DeviceTree.im4p",
    "KernelCache": KERNEL.name,
    "RestoreRamDisk": RAMDISK.name,
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def sha384(path: Path) -> bytes:
    h = hashlib.sha384()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.digest()


def run(*args: object) -> None:
    subprocess.run([str(arg) for arg in args], check=True)


def identity() -> dict:
    manifest = plistlib.loads((CFW / "BuildManifest.plist").read_bytes())
    if manifest.get("ProductBuildVersion") != "22H374":
        raise RuntimeError("CFW is not the reviewed 22H374 IPSW")
    matches = [item for item in manifest["BuildIdentities"]
               if item.get("Info", {}).get("DeviceClass") == "n841ap"
               and item.get("Info", {}).get("Variant") == "Customer Erase Install (IPSW)"]
    if len(matches) != 1:
        raise RuntimeError("expected one n841ap erase-install identity")
    items = matches[0]["Manifest"]
    for normal, restore in (("KernelCache", "RestoreKernelCache"),
                            ("DeviceTree", "RestoreDeviceTree")):
        if items[normal]["Info"]["Path"] != items[restore]["Info"]["Path"]:
            raise RuntimeError(f"{normal} and {restore} paths differ on this IPSW")
    return items


def component(items: dict, name: str) -> Path:
    relative = Path(items[name]["Info"]["Path"])
    path = (CFW / relative).resolve()
    if not path.is_relative_to(CFW.resolve()) or not path.is_file():
        raise RuntimeError(f"invalid or missing {name} component: {relative}")
    return path


def check_artifacts() -> None:
    for path in (KERNEL, RAMDISK):
        if not path.is_file() or sha256(path) != PINNED_ARTIFACTS[path.name]:
            raise RuntimeError(f"missing or unexpected reviewed artifact: {path}")
    # Both inputs are unsigned IM4P containers with the expected fourcc.
    for path, fourcc in ((KERNEL, b"krnl"), (RAMDISK, b"rdsk")):
        with path.open("rb") as stream:
            header = stream.read(64)
        if b"\x16\x04IM4P" not in header or b"\x16\x04" + fourcc not in header:
            raise RuntimeError(f"wrong Image4 component type: {path}")


def save_pristine(items: dict) -> None:
    """Save only the small changed components, never another full IPSW tree."""
    for name in ("iBSS", "iBEC", "DeviceTree", "KernelCache", "RestoreRamDisk"):
        source = component(items, name)
        relative = source.relative_to(CFW)
        saved = PRISTINE / relative
        expected = items[name]["Digest"]
        if saved.is_file():
            if sha384(saved) != expected:
                raise RuntimeError(f"pristine backup is not stock: {saved}")
            continue
        if sha384(source) != expected:
            raise RuntimeError(f"cannot back up non-stock {name}: {source}")
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, saved)
        if sha384(saved) != expected:
            raise RuntimeError(f"pristine copy failed verification: {saved}")


def stock(items: dict, name: str) -> Path:
    path = PRISTINE / items[name]["Info"]["Path"]
    if not path.is_file() or sha384(path) != items[name]["Digest"]:
        raise RuntimeError(f"missing or invalid pristine {name}: {path}")
    return path


def extract(container: Path, raw: Path) -> None:
    run("img4", "-i", container, "-o", raw)
    if not raw.is_file() or raw.stat().st_size == 0:
        raise RuntimeError(f"Image4 extraction failed: {container}")


def replace_payload(container: Path, raw: Path, output: Path, scratch: Path) -> None:
    run("img4", "-i", container, "-o", output, "-R", raw)
    actual = scratch / (output.name + ".raw")
    extract(output, actual)
    if actual.read_bytes() != raw.read_bytes():
        raise RuntimeError(f"Image4 payload round-trip failed: {output}")


def wrap_raw_ibec(raw: Path, output: Path, scratch: Path) -> None:
    """Build the plain IM4P that patched iBSS actually hands off to iBEC."""
    run("img4", "-i", raw, "-o", output, "-A", "-T", "ibec")
    actual = scratch / (output.name + ".raw")
    extract(output, actual)
    if actual.read_bytes() != raw.read_bytes():
        raise RuntimeError(f"raw-wrapped iBEC payload changed: {output}")


def compare_ibec_containers(ticket: Path, output: Path,
                            old_template: Path | None = None,
                            serial: bool = False) -> dict:
    """Recreate both iBEC packaging paths without modifying CFW or the phone.

    The retired restore-bootset used ``img4 -R`` on an existing iBEC.img4
    template. That exact template was deleted, so the pristine IPSW iBEC is
    used unless the caller supplies a saved bootset image explicitly.
    """
    items = identity()
    raw = RESTORE / "iBEC.restore.raw.patched"
    require_stage_args(raw, "ibec", serial)
    ticket = selected_ticket(ticket, require_complete=False)
    template = old_template or stock(items, "iBEC")
    if not template.is_file():
        raise RuntimeError(f"missing old iBEC template: {template}")
    output = output.expanduser().resolve()
    if output.exists():
        raise RuntimeError(f"comparison output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()
    try:
        cases = {}
        for name in ("template-replace", "plain-raw-wrap"):
            im4p = output / f"{name}.im4p"
            img4 = output / f"{name}.img4"
            if name == "template-replace":
                replace_payload(template, raw, im4p, output)
            else:
                wrap_raw_ibec(raw, im4p, output)
            if b"\x16\x04ibec" not in im4p.read_bytes()[:80]:
                raise RuntimeError(f"{name} lost its ibec component type")
            run("img4", "-i", im4p, "-o", img4, "-M", ticket)
            extracted = output / f"{name}.ticket.im4m"
            run("img4", "-i", img4, "-m", extracted)
            if extracted.read_bytes() != ticket.read_bytes():
                raise RuntimeError(f"{name} embedded the wrong ticket")
            cases[name] = {
                "im4p": str(im4p), "im4pSha256": sha256(im4p),
                "img4": str(img4), "img4Sha256": sha256(img4),
                "img4Bytes": img4.stat().st_size,
            }
        first = (output / "template-replace.im4p").read_bytes()
        second = (output / "plain-raw-wrap.im4p").read_bytes()
        first_difference = next((i for i, (a, b) in enumerate(zip(first, second))
                                 if a != b), min(len(first), len(second)))
        result = {
            "schema": 1, "profileID": PROFILE,
            "template": str(template), "templateSha256": sha256(template),
            "patchedPayloadSha256": sha256(raw),
            "ticket": str(ticket), "ticketSha256": sha256(ticket),
            "firstIM4PDifference": first_difference,
            "cases": cases,
        }
        (output / "comparison.json").write_text(json.dumps(result, indent=2,
                                                          sort_keys=True) + "\n")
        return result
    except BaseException:
        shutil.rmtree(output)
        raise


def publish(source: Path, target: Path) -> None:
    fd, name = tempfile.mkstemp(prefix=".xra1n-cfw-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as output, source.open("rb") as input_file:
            shutil.copyfileobj(input_file, output, 1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        shutil.copymode(target, name)
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def apply(ibec_container: str = "plain", serial: bool = False) -> None:
    if ibec_container not in {"plain", "template"}:
        raise ValueError(f"unsupported iBEC container: {ibec_container}")
    items = identity()
    check_artifacts()
    if sha384(component(items, "iBSS")) != items["iBSS"]["Digest"]:
        raise RuntimeError("CFW iBSS is not stock; patched iBSS must stay separate")
    save_pristine(items)
    RESTORE.mkdir(parents=True, exist_ok=True)
    previous_ibec_sha = None
    if RECORD.is_file():
        previous = json.loads(RECORD.read_text())
        previous_serial = previous.get("serial", True)  # pre-option records used serial
        if previous_serial != serial:
            # Validate the old state before regenerating its raw stage files.
            check(previous.get("ibecContainer", "plain"), previous_serial)
            previous_ibec_sha = previous["components"]["iBEC"]["sha256"]
    with tempfile.TemporaryDirectory(prefix="xra1n-cfw-") as directory:
        stage = Path(directory)
        for name in ("iBSS", "iBEC"):
            output = RESTORE / f"{name}.restore.raw"
            extract(stock(items, name), stage / f"{name}.raw")
            shutil.copy2(stage / f"{name}.raw", output)
        run(sys.executable, ROOT / "prepare_boot_stages.py", *(["--serial"] if serial else []))
        require_stage_args(RESTORE / "iBSS.restore.raw.patched", "restore", serial)
        require_stage_args(RESTORE / "iBEC.restore.raw.patched", "ibec", serial)

        dt_raw = stage / "DeviceTree.stock.raw"
        dt_patched = RESTORE / "DeviceTree.restore.raw"
        extract(stock(items, "DeviceTree"), dt_raw)
        run(sys.executable, ROOT / "patch_devicetree.py", dt_raw, "restore", "-o", dt_patched)
        if resolve_path(parse(dt_patched.read_bytes()), "/defaults").find_prop("content-protect"):
            raise RuntimeError("restore DeviceTree still enables content protection")

        plain_ibec = stage / "iBEC.plain.im4p"
        template_ibec = stage / "iBEC.template.im4p"
        ibec_raw = RESTORE / "iBEC.restore.raw.patched"
        wrap_raw_ibec(ibec_raw, plain_ibec, stage)
        replace_payload(stock(items, "iBEC"), ibec_raw, template_ibec, stage)
        prepared = {
            "iBEC": template_ibec if ibec_container == "template" else plain_ibec,
            "DeviceTree": stage / "DeviceTree.im4p",
            "KernelCache": KERNEL,
            "RestoreRamDisk": RAMDISK,
        }
        replace_payload(stock(items, "DeviceTree"), dt_patched,
                        prepared["DeviceTree"], stage)
        records = {}
        for name, source in prepared.items():
            target = component(items, name)
            current = sha256(target)
            known = {sha256(stock(items, name)), sha256(source)}
            if name == "iBEC":
                known.update((sha256(plain_ibec), sha256(template_ibec)))
                if previous_ibec_sha is not None:
                    known.add(previous_ibec_sha)
            if current not in known:
                raise RuntimeError(f"unknown existing CFW {name}; refusing replacement: {target}")
            records[name] = {"path": str(target.relative_to(CFW)), "sha256": sha256(source)}
        for name, source in prepared.items():
            target = component(items, name)
            if sha256(target) != records[name]["sha256"]:
                publish(source, target)
            if sha256(target) != records[name]["sha256"]:
                raise RuntimeError(f"published CFW {name} differs from prepared source")
        RECORD.write_text(json.dumps({"schema": 1, "profileID": PROFILE,
                                      "ibecContainer": ibec_container,
                                      "serial": serial,
                                      "kernelArtifactSha256": sha256(KERNEL),
                                      "ramdiskArtifactSha256": sha256(RAMDISK),
                                      "components": records}, indent=2, sort_keys=True) + "\n")
    check(ibec_container, serial)


def check(ibec_container: str = "plain", serial: bool = False) -> None:
    if ibec_container not in {"plain", "template"}:
        raise ValueError(f"unsupported iBEC container: {ibec_container}")
    items = identity()
    check_artifacts()
    if not RECORD.is_file():
        raise RuntimeError("CFW preparation record missing; use --apply before restoring")
    record = json.loads(RECORD.read_text())
    if record.get("profileID") != PROFILE or record.get("schema") != 1:
        raise RuntimeError("CFW preparation record is for another profile")
    if record.get("ibecContainer", "plain") != ibec_container:
        raise RuntimeError("CFW iBEC container does not match requested mode")
    if record.get("serial", True) != serial:
        raise RuntimeError("CFW serial mode does not match requested mode")
    if record.get("kernelArtifactSha256") != sha256(KERNEL) or record.get("ramdiskArtifactSha256") != sha256(RAMDISK):
        raise RuntimeError("CFW preparation record references different artifacts")
    expected = record.get("components", {})
    if set(expected) != set(REPLACEMENTS):
        raise RuntimeError("CFW preparation record has the wrong component set")
    for name in REPLACEMENTS:
        path = component(items, name)
        if expected[name] != {"path": str(path.relative_to(CFW)), "sha256": sha256(path)}:
            raise RuntimeError(f"CFW {name} no longer matches the preparation record")
        stock(items, name)
    with tempfile.TemporaryDirectory(prefix="xra1n-ibec-check-") as directory:
        stage = Path(directory)
        expected_ibec = stage / "iBEC.im4p"
        if ibec_container == "template":
            replace_payload(stock(items, "iBEC"), RESTORE / "iBEC.restore.raw.patched",
                            expected_ibec, stage)
        else:
            wrap_raw_ibec(RESTORE / "iBEC.restore.raw.patched", expected_ibec, stage)
        if component(items, "iBEC").read_bytes() != expected_ibec.read_bytes():
            raise RuntimeError(f"CFW iBEC is not the verified {ibec_container} container")
    stock(items, "iBSS")
    if sha384(component(items, "iBSS")) != items["iBSS"]["Digest"]:
        raise RuntimeError("CFW iBSS changed after preparation")
    require_stage_args(RESTORE / "iBSS.restore.raw.patched", "restore", serial)
    require_stage_args(RESTORE / "iBEC.restore.raw.patched", "ibec", serial)
    print("[+] reviewed patched CFW and separate restore iBSS verified")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--apply", action="store_true", help="prepare CFW in place; no device I/O")
    group.add_argument("--check", action="store_true", help="verify prepared CFW without writing")
    group.add_argument("--compare-ibec", action="store_true",
                       help="build ticketed old-template and plain iBEC variants without device I/O")
    parser.add_argument("--ticket", type=Path, default=ROOT / "tickets/apticket.im4m")
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "work/restore/ibec-container-comparison")
    parser.add_argument("--old-template", type=Path,
                        help="saved old restore-bootset iBEC.img4; otherwise use pristine IPSW iBEC")
    parser.add_argument("--ibec-container", choices=("plain", "template"), default="plain",
                        help="CFW iBEC packaging for --apply/--check; plain remains the default")
    parser.add_argument("--serial", action="store_true", help="prepare boot args for DCSD serial capture")
    args = parser.parse_args()
    configure_host_tools()
    if args.apply:
        apply(args.ibec_container, args.serial)
    elif args.check:
        check(args.ibec_container, args.serial)
    else:
        result = compare_ibec_containers(args.ticket, args.output_dir, args.old_template,
                                         args.serial)
        print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError, KeyError, ValueError) as error:
        raise SystemExit(f"[!] {error}") from error
