#!/usr/bin/env python3
"""Transactionally patch and re-sign the XR 22H374 RestoreRamDisk binaries.

The complete plan is resolved against pristine SHA-256 digests and instruction
pre-images before either file is changed. Signed bytes are copied through the
existing inode so APFS ownership, mode and inode identity remain intact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Patch:
    id: str
    offset: int
    original: int
    replacement: int
    summary: str


TARGETS = {
    "restored_external": {
        "path": "usr/local/bin/restored_external",
        "identifier": "com.apple.restored_external",
        "sha256": "4d40d853b6254a219f2c565b936acf70207eb45d57c795e2e55c72ec43aa75b6",
        "patches": [
            Patch("restored.fdr-return", 0x7A4B4, 0xAA1A03E0, 0xD2800000,
                  "Return success from RestoredFDRRecover"),
        ],
    },
    "asr": {
        "path": "usr/sbin/asr",
        "identifier": "com.apple.asr",
        "sha256": "d97dd831ed5f4bb2cfff7fdcc7269c4c0ac3b41cc6f09e22caf82644e4704eed",
        "patches": [
            Patch("asr.signature-mismatch", 0x24D20, 0x350031E0, 0xD503201F,
                  "Bypass the image-signature mismatch branch"),
        ],
    },
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolve(
    name: str, path: Path, *, verify_patched: bool = False
) -> tuple[bytearray, list[dict[str, object]]]:
    target = TARGETS[name]
    data = bytearray(path.read_bytes())
    actual = sha256(data)
    if not verify_patched and actual != target["sha256"]:
        raise SystemExit(
            f"[!] {name}: pristine SHA-256 mismatch\n"
            f"    expected {target['sha256']}\n"
            f"    got      {actual}"
        )

    records: list[dict[str, object]] = []
    for patch in target["patches"]:
        if patch.offset + 4 > len(data):
            raise SystemExit(f"[!] {name}: {patch.id} lies outside the binary")
        current = struct.unpack_from("<I", data, patch.offset)[0]
        expected = patch.replacement if verify_patched else patch.original
        if current != expected:
            raise SystemExit(
                f"[!] {name}: {patch.id} pre-image mismatch at 0x{patch.offset:x}: "
                f"expected 0x{expected:08x}, got 0x{current:08x}"
            )
        if not verify_patched:
            struct.pack_into("<I", data, patch.offset, patch.replacement)
        records.append({
            "id": patch.id,
            "component": name,
            "offset": patch.offset,
            "offsetHex": f"0x{patch.offset:x}",
            "original": f"0x{patch.original:08x}",
            "replacement": f"0x{patch.replacement:08x}",
            "summary": patch.summary,
        })
    return data, records


def run(arguments: list[object], *, capture: bool = False) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        [str(argument) for argument in arguments],
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if result.returncode:
        detail = result.stderr.decode(errors="replace").strip() if capture else ""
        raise SystemExit(f"[!] command failed ({result.returncode}): {arguments[0]}: {detail}")
    return result


def sign(ldid: Path, pristine: Path, staged: Path, identifier: str, work: Path) -> None:
    entitlements = work / f"{staged.name}.entitlements.plist"
    original_entitlements = run([ldid, "-e", pristine], capture=True).stdout
    entitlements.write_bytes(original_entitlements)
    run([ldid, f"-I{identifier}", f"-S{entitlements}", "-M", "-Cadhoc", staged])
    if run([ldid, "-e", staged], capture=True).stdout != original_entitlements:
        raise SystemExit(f"[!] entitlements changed while signing {staged.name}")


def metadata(path: Path) -> tuple[int, int, int, int]:
    info = path.stat()
    return info.st_ino, info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)


def install_through_inode(path: Path, data: bytes) -> None:
    before = metadata(path)
    with path.open("r+b") as stream:
        stream.write(data)
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())
    if metadata(path) != before:
        raise SystemExit(f"[!] APFS metadata changed while replacing {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path, help="mounted RestoreRamDisk root")
    parser.add_argument("--ldid", type=Path, help="ldid executable used for deployable output")
    parser.add_argument("--check", action="store_true", help="validate only; write nothing")
    parser.add_argument(
        "--verify-patched",
        action="store_true",
        help="verify replacement instructions in an already-patched image",
    )
    parser.add_argument("--records", type=Path, help="write patch records JSON here")
    parser.add_argument("--unsigned", action="store_true", help="explicitly skip signing")
    arguments = parser.parse_args()

    if arguments.check and arguments.verify_patched:
        parser.error("--check and --verify-patched are mutually exclusive")
    if not (arguments.check or arguments.verify_patched) and arguments.ldid is None and not arguments.unsigned:
        parser.error("patching requires --ldid (or explicit --unsigned)")
    if arguments.ldid is not None and arguments.unsigned:
        parser.error("--ldid and --unsigned are mutually exclusive")
    if arguments.ldid is not None and not arguments.ldid.is_file():
        parser.error(f"ldid is not a file: {arguments.ldid}")

    plan: dict[str, tuple[Path, bytes, bytearray, list[dict[str, object]]]] = {}
    all_records: list[dict[str, object]] = []
    for name, target in TARGETS.items():
        path = arguments.root / str(target["path"])
        if not path.is_file():
            raise SystemExit(f"[!] missing RestoreRamDisk binary: {path}")
        original = path.read_bytes()
        data, records = resolve(name, path, verify_patched=arguments.verify_patched)
        plan[name] = path, original, data, records
        all_records.extend(records)
        print(f"[+] {name}: resolved {len(records)} guarded patch(es)")

    if arguments.check or arguments.verify_patched:
        state = "patched image" if arguments.verify_patched else "pristine patch plan"
        print(f"[+] complete RestoreRamDisk {state} validated; no files written")
        return

    with tempfile.TemporaryDirectory(prefix="xra1n-ramdisk-") as directory:
        work = Path(directory)
        staged_outputs: dict[str, bytes] = {}
        # Finish every fallible patch/sign operation before touching the image.
        for name, (path, _, data, _) in plan.items():
            staged = work / name
            staged.write_bytes(data)
            shutil.copymode(path, staged)
            if arguments.ldid is not None:
                sign(arguments.ldid, path, staged, str(TARGETS[name]["identifier"]), work)
            staged_outputs[name] = staged.read_bytes()

        installed: list[str] = []
        try:
            for name, (path, _, _, _) in plan.items():
                install_through_inode(path, staged_outputs[name])
                installed.append(name)
                print(f"[+] {name}: installed through original inode")
        except BaseException:
            for name in reversed(installed):
                path, original, _, _ = plan[name]
                install_through_inode(path, original)
                print(f"[!] {name}: rolled back after installation failure")
            raise

    if arguments.records:
        arguments.records.parent.mkdir(parents=True, exist_ok=True)
        arguments.records.write_text(json.dumps(all_records, indent=2) + "\n")
        print(f"[+] wrote records: {arguments.records}")


if __name__ == "__main__":
    main()
