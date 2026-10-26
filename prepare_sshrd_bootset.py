#!/usr/bin/env python3
"""Build and verify the dedicated n841/22H374 SSHRD boot set."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from host_tools import configure as configure_host_tools
from boot_stage import require_stage_args


ROOT = Path(__file__).resolve().parent
DEFAULT_ARCHIVE = ROOT / "SSHRestoreRamDisk-iPhone11,8-22H374.zip"
DEFAULT_TICKET = ROOT / "tickets/apticket.im4m"
DEFAULT_BOOTSET = ROOT / "work/sshrd-bootset"
IBSS = ROOT / "work/sshrd/iBSS.ramdisk.raw.patched"
IBEC = ROOT / "work/sshrd/iBEC.ramdisk.raw.patched"
KERNEL = ROOT / "kernelcache.patched-0x891-full-aks.im4p"
RESTORE_DT = ROOT / "work/restore/DeviceTree.restore.raw"


def run(*command: object) -> None:
    subprocess.run([str(item) for item in command], check=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def attach_ticket(source: Path, output: Path, ticket: Path, fourcc: str | None = None) -> None:
    command: list[object] = ["img4", "-i", source, "-o", output, "-M", ticket]
    if fourcc:
        command += ["-T", fourcc]
    run(*command)


def main(argv: list[str] | None = None) -> None:
    from prepare_normal_bootset import COMPONENTS, component, identity, pristine_component

    parser = argparse.ArgumentParser(description="Build the ticketed SSHRD boot set; no device I/O")
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--ramdisk-im4p", type=Path,
                        help="already extracted RestoreRamDisk.sshrd.im4p")
    parser.add_argument("--ticket", type=Path, default=DEFAULT_TICKET)
    parser.add_argument("--bootset", type=Path, default=DEFAULT_BOOTSET)
    parser.add_argument("--serial", action="store_true", help="require serial-patched iBoot stages")
    args = parser.parse_args(argv)
    configure_host_tools()

    archive, ticket, bootset = (path.resolve() for path in (args.archive, args.ticket, args.bootset))
    ramdisk_im4p = args.ramdisk_im4p.resolve() if args.ramdisk_im4p else None
    required = ((ramdisk_im4p,) if ramdisk_im4p else (archive,)) + (ticket, IBSS, IBEC, KERNEL, RESTORE_DT)
    for path in required:
        if not path.is_file():
            raise SystemExit(f"missing required input: {path}")
    require_stage_args(IBSS, "ramdisk", args.serial)
    require_stage_args(IBEC, "ramdisk", args.serial)

    metadata_path = ticket.with_suffix(".json")
    if metadata_path.is_file():
        metadata = json.loads(metadata_path.read_text())
        if metadata.get("sha256") != sha256(ticket):
            raise SystemExit("ticket metadata hash does not match apticket.im4m")

    with tempfile.TemporaryDirectory(prefix="xra1n-sshrd-") as directory:
        staging = Path(directory)
        member = "RestoreRamDisk.sshrd.im4p"
        if ramdisk_im4p:
            shutil.copy2(ramdisk_im4p, staging / member)
        else:
            with zipfile.ZipFile(archive) as bundle:
                if member not in bundle.namelist():
                    raise SystemExit(f"artifact lacks {member}")
                bundle.extract(member, staging)
                checksum_member = f"{member}.sha256"
                if checksum_member in bundle.namelist():
                    expected = bundle.read(checksum_member).decode().split()[0]
                    if sha256(staging / member) != expected:
                        raise SystemExit("SSHRD workflow artifact checksum mismatch")

        shutil.copy2(IBSS, staging / "iBSS.raw")

        # Use the manifest-verified pristine template, never an old erase
        # restore bootset or the restore-patched iBEC inside CFW.
        items = identity()
        ibec_template = pristine_component(items, "iBEC")
        run("img4", "-i", ibec_template, "-o", staging / "iBEC.img4",
            "-R", IBEC, "-M", ticket, "-T", "ibec")

        # Firmware that is not patched is read from the extracted CFW. The
        # DeviceTree is recreated from pristine bytes and the restore plan.
        for source_name, output_name, fourcc in COMPONENTS:
            attach_ticket(component(items, source_name), staging / output_name, ticket, fourcc)
        run("img4", "-i", pristine_component(items, "DeviceTree"),
            "-o", staging / "DeviceTree.img4", "-R", RESTORE_DT,
            "-M", ticket, "-T", "rdtr")

        attach_ticket(staging / member, staging / "RestoreRamdisk.img4", ticket, "rdsk")
        attach_ticket(KERNEL, staging / "Kernelcache.img4", ticket, "rkrn")

        # Verify every IMG4 embeds exactly the selected ticket.
        for image in sorted(staging.glob("*.img4")):
            embedded = staging / f".{image.name}.im4m"
            run("img4", "-i", image, "-m", embedded)
            if embedded.read_bytes() != ticket.read_bytes():
                raise SystemExit(f"embedded ticket mismatch: {image.name}")

        # Verify the patched raw payloads survived container construction.
        extracted = staging / ".iBEC.actual.raw"
        run("img4", "-i", staging / "iBEC.img4", "-o", extracted)
        if extracted.read_bytes() != IBEC.read_bytes():
            raise SystemExit("ticketed iBEC payload differs from SSHRD patch output")
        dt_extracted = staging / ".DeviceTree.actual.raw"
        run("img4", "-i", staging / "DeviceTree.img4", "-o", dt_extracted)
        if dt_extracted.read_bytes() != RESTORE_DT.read_bytes():
            raise SystemExit("ticketed DeviceTree differs from restore patch output")

        if bootset.exists():
            shutil.rmtree(bootset)
        bootset.mkdir(parents=True)
        for path in sorted(staging.iterdir()):
            if not path.name.startswith("."):
                shutil.copy2(path, bootset / path.name)
        sums = "".join(
            f"{sha256(path)}  {path.name}\n"
            for path in sorted(bootset.iterdir()) if path.is_file()
        )
        (bootset / "SHA256SUMS").write_text(sums)

    print(f"[+] SSHRD bootset: {bootset}")
    print(f"[+] ticket SHA256: {sha256(ticket)}")
    print(f"[+] serial={args.serial} iBSS/iBEC SHA256: {sha256(IBSS)}")


if __name__ == "__main__":
    main()
