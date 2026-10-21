#!/usr/bin/env python3
"""Apply the three XR 22H374 CommCenter data-activation gate patches.

The sites were derived from the same structural anchors used by Liter8's
CommCenterDataActivationResolver and then verified in IDA against the pristine
iPhone11,8 / iOS 18.7.10 executable.  This tool is intentionally exact-build:
it checks the complete input digest and every four-byte pre-image before it
writes anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from dataclasses import asdict, dataclass
from pathlib import Path


PRISTINE_SHA256 = "77ef543a172aa293c1cbc43829789a8b83acafb468bf558b20b57dc2a7d8c44e"
NOP = 0xD503201F


@dataclass(frozen=True)
class Patch:
    id: str
    offset: int
    original: int
    replacement: int
    summary: str


PATCHES = (
    Patch(
        "commcenter.data-connection.context-index",
        0x804268,
        0x540001E1,  # b.ne 0x1008042a4
        0x1400000F,  # b    0x1008042a4
        "Evaluate activation even when the context index sentinel is -2",
    ),
    Patch(
        "commcenter.data-settings.activation-status",
        0x631370,
        0x36000460,  # tbz w0, #0, 0x1006313fc
        NOP,
        "Do not reject canActivateDataSettings for ActivationStatus",
    ),
    Patch(
        "commcenter.data-settings.activate-baseband-state",
        0x6348B8,
        0x360003A0,  # tbz w0, #0, 0x10063492c
        NOP,
        "Do not repeat the same activation-status refusal while activating",
    ),
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolve(source: Path) -> tuple[bytearray, list[dict[str, object]]]:
    data = bytearray(source.read_bytes())
    actual = sha256(data)
    if actual != PRISTINE_SHA256:
        raise SystemExit(
            "[!] CommCenter pristine SHA-256 mismatch\n"
            f"    expected {PRISTINE_SHA256}\n"
            f"    got      {actual}"
        )

    records: list[dict[str, object]] = []
    for patch in PATCHES:
        current = struct.unpack_from("<I", data, patch.offset)[0]
        if current != patch.original:
            raise SystemExit(
                f"[!] {patch.id}: pre-image mismatch at 0x{patch.offset:x}: "
                f"expected 0x{patch.original:08x}, got 0x{current:08x}"
            )
        struct.pack_into("<I", data, patch.offset, patch.replacement)
        record = asdict(patch)
        record.update(
            offsetHex=f"0x{patch.offset:x}",
            originalHex=f"0x{patch.original:08x}",
            replacementHex=f"0x{patch.replacement:08x}",
        )
        records.append(record)
    return data, records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--records", type=Path)
    args = parser.parse_args()

    patched, records = resolve(args.source)
    print(f"[+] resolved {len(records)} guarded CommCenter cellular patches")
    for record in records:
        print(
            f"[+] {record['id']}: {record['offsetHex']} "
            f"{record['originalHex']} -> {record['replacementHex']}"
        )

    if args.check:
        if args.output is not None:
            parser.error("output is incompatible with --check")
        print("[+] validation complete; no file written")
        return
    if args.output is None:
        parser.error("output is required unless --check is used")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(patched)
    if args.records:
        args.records.parent.mkdir(parents=True, exist_ok=True)
        args.records.write_text(json.dumps(records, indent=2) + "\n")
    print(f"[+] wrote unsigned patched CommCenter: {args.output}")
    print(f"[+] SHA-256: {sha256(patched)}")


if __name__ == "__main__":
    main()
