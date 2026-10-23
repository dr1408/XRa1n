#!/usr/bin/env python3
"""Patch the four post-restore userland daemons for XR 22H374.

This is the small, dependency-free XRa1n counterpart of Liter8's
CoreAuthDResolver, MobileActivationDResolver and CTKDResolver.  It is locked to
the pristine binaries collected for iPhone11,8 / iOS 18.7.10 and checks both
the complete input digest and every instruction pre-image before writing.

The source directory must contain:
  coreauthd, mobileactivationd, ctkd, usermanagerd

By default patched copies and JSON patch records are written to ``patched/``;
the pristine inputs are never modified.  Use --check to validate only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


NOP = 0xD503201F
MOV_X0_ZERO = 0xD2800000
MOV_W0_ONE = 0x52800020
RET = 0xD65F03C0


@dataclass(frozen=True)
class Patch:
    id: str
    offset: int
    original: int
    replacement: int
    summary: str


def encode_adrp(register: int, instruction_address: int, target: int) -> int:
    delta = (target & ~0xFFF) - (instruction_address & ~0xFFF)
    if delta % 0x1000:
        raise ValueError("ADRP target is not page aligned")
    pages = delta // 0x1000
    if not -(1 << 20) <= pages < (1 << 20):
        raise ValueError("ADRP target is out of range")
    immediate = pages & 0x1FFFFF
    return 0x90000000 | ((immediate & 3) << 29) | (((immediate >> 2) & 0x7FFFF) << 5) | register


def encode_add(destination: int, source: int, immediate: int) -> int:
    if not 0 <= immediate < 0x1000:
        raise ValueError("ADD immediate is out of range")
    return 0x91000000 | (immediate << 10) | (source << 5) | destination


# Mach-O __TEXT starts at 0x100000000 and maps 1:1 to file offsets in these
# three executables.  The 32-byte CFString object starts at 0x100279ad0;
# 0x100279ae0 is its backing-string pointer field, NOT the object address.
MOBILE_BASE = 0x100000000
ACTIVATED_CFSTRING = 0x100279AD0
ACTIVATED_CSTRING = 0x1002535C0


PATCHES = {
    "coreauthd": (
        "d479cb7f9de2531e85c5af0edd7d91ed9f4a29fea7e192565f7754f1440b22d7",
        [
            Patch(
                "coreauthd.dto-ratchet.start-controller",
                0x7B68,
                0x94007CE6,
                NOP,
                "Skip the SEP-dependent DTO ratchet controller startup",
            ),
        ],
    ),
    "mobileactivationd": (
        "17076de71a65e1096c74ee87ee6d4460748f2dfc7ffb9760ba4cf8439bfbb26e",
        [
            Patch(
                "mobileactivationd.should-hactivate",
                0x17350,
                0x39405000,
                MOV_W0_ONE,
                "Make DeviceType report that hactivation is enabled",
            ),
            Patch(
                "mobileactivationd.activation-state.migration-gate",
                0x18A0,
                0x36000576,
                NOP,
                "Do not skip activation-state reporting before migration completes",
            ),
            Patch(
                "mobileactivationd.activation-state.adrp",
                0x1900,
                0x90001328,
                encode_adrp(0, MOBILE_BASE + 0x1900, ACTIVATED_CFSTRING),
                "Load the page containing the Activated CFString into X0",
            ),
            Patch(
                "mobileactivationd.activation-state.add",
                0x1904,
                0x913B4108,
                encode_add(0, 0, ACTIVATED_CFSTRING & 0xFFF),
                "Materialize the Activated CFString address in X0",
            ),
            Patch(
                "mobileactivationd.activation-state.dereference",
                0x1908,
                0xF9400100,
                NOP,
                "Keep the CFString object address instead of dereferencing a state global",
            ),
        ],
    ),
    "ctkd": (
        "97c328e8a85248c0b3b0471a92aa0f13bebdc8a7730f1a43014dbeaf214e8657",
        [
            Patch(
                "ctkd.sep-key-server.return-nil",
                0x6CD0,
                0xD503237F,
                MOV_X0_ZERO,
                "Return nil from serverAttributesOfKey:error:",
            ),
            Patch(
                "ctkd.sep-key-server.return",
                0x6CD4,
                0xD10303FF,
                RET,
                "Return before entering the SEP-backed method body",
            ),
        ],
    ),
    "usermanagerd": (
        "4dd3412ac0f6619af541077ab8f14fb78da6d9b5c8713b6f88f231aaa7c05e69",
        [
            Patch(
                "usermanagerd.normal.diagnostics-gate",
                0x51408,
                0x370000C0,
                NOP,
                "Run normal session/persona loading in diagnostics mode",
            ),
            Patch(
                "usermanagerd.init.diagnostics-gate",
                0x514EC,
                0x34000100,
                0x14000008,
                "Enter native early-boot reconciliation in diagnostics mode",
            ),
        ],
    ),
}


SIGNING_IDENTIFIERS = {
    "coreauthd": "com.apple.coreauthd",
    "mobileactivationd": "com.apple.mobileactivationd",
    "ctkd": "com.apple.ctkd",
    "usermanagerd": "com.apple.usermanagerd",
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_and_patch(name: str, source: Path) -> tuple[bytearray, list[dict[str, object]]]:
    expected_digest, patches = PATCHES[name]
    data = bytearray(source.read_bytes())
    actual_digest = digest(data)
    if actual_digest != expected_digest:
        raise SystemExit(
            f"[!] {name}: pristine SHA-256 mismatch\n"
            f"    expected {expected_digest}\n"
            f"    got      {actual_digest}"
        )

    if name == "mobileactivationd":
        object_offset = ACTIVATED_CFSTRING - MOBILE_BASE
        string_offset = ACTIVATED_CSTRING - MOBILE_BASE
        if (
            data[string_offset : string_offset + 10] != b"Activated\0"
            # On-disk dyld chained rebase encoding of 0x1002535c0.
            or struct.unpack_from("<Q", data, object_offset + 16)[0] != 0x00100000002535C0
            or struct.unpack_from("<Q", data, object_offset + 24)[0] != 9
        ):
            raise SystemExit("[!] mobileactivationd: Activated CFString layout mismatch")

    records: list[dict[str, object]] = []
    for patch in patches:
        if patch.offset + 4 > len(data):
            raise SystemExit(f"[!] {name}: {patch.id} is outside the file")
        current = struct.unpack_from("<I", data, patch.offset)[0]
        if current != patch.original:
            raise SystemExit(
                f"[!] {name}: {patch.id} pre-image mismatch at 0x{patch.offset:x}: "
                f"expected 0x{patch.original:08x}, got 0x{current:08x}"
            )
        struct.pack_into("<I", data, patch.offset, patch.replacement)
        records.append(
            {
                "id": patch.id,
                "component": name,
                "offset": patch.offset,
                "offsetHex": f"0x{patch.offset:x}",
                "original": f"0x{patch.original:08x}",
                "replacement": f"0x{patch.replacement:08x}",
                "summary": patch.summary,
            }
        )
    return data, records


def sign_with_ldid(ldid: Path, pristine: Path, patched: Path, name: str) -> None:
    """Preserve the stock entitlement plist while creating an ad-hoc signature."""
    with tempfile.TemporaryDirectory(prefix="xra1n-entitlements-") as temporary:
        entitlements = Path(temporary) / f"{name}.plist"
        result = subprocess.run(
            [str(ldid), "-e", str(pristine)],
            check=True,
            stdout=subprocess.PIPE,
        )
        entitlements.write_bytes(result.stdout)
        subprocess.run(
            [
                str(ldid),
                f"-I{SIGNING_IDENTIFIERS[name]}",
                f"-S{entitlements}",
                "-Cadhoc",
                str(patched),
            ],
            check=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, help="directory containing the four pristine binaries")
    parser.add_argument("output", type=Path, nargs="?", default=Path("patched"))
    parser.add_argument("--check", action="store_true", help="validate all inputs without writing")
    parser.add_argument("--ldid", type=Path, help="preserve entitlements and ad-hoc sign each output")
    parser.add_argument(
        "--unsigned",
        action="store_true",
        help="write intentionally unsigned outputs (for inspection only)",
    )
    arguments = parser.parse_args()

    if not arguments.check and arguments.ldid is None and not arguments.unsigned:
        parser.error("writing deployable outputs requires --ldid (or explicit --unsigned)")
    if arguments.ldid is not None and arguments.unsigned:
        parser.error("--ldid and --unsigned are mutually exclusive")

    resolved: dict[str, tuple[Path, bytearray, list[dict[str, object]]]] = {}
    # Resolve and validate the complete plan first.  Nothing is written unless
    # all four pristine binaries and all ten instruction guards match.
    for name in PATCHES:
        source = arguments.source / name
        if not source.is_file():
            raise SystemExit(f"[!] missing pristine binary: {source}")
        data, records = validate_and_patch(name, source)
        resolved[name] = (source, data, records)
        print(f"[+] {name}: resolved {len(records)} guarded patch(es)")

    if arguments.check:
        print("[+] complete four-daemon patch plan validated; no files written")
        return

    arguments.output.mkdir(parents=True, exist_ok=True)
    for name, (source, data, records) in resolved.items():
        output = arguments.output / name
        output.write_bytes(data)
        shutil.copymode(source, output)
        if arguments.ldid:
            if not arguments.ldid.is_file():
                raise SystemExit(f"[!] ldid not found: {arguments.ldid}")
            sign_with_ldid(arguments.ldid, source, output, name)
        record_path = arguments.output / f"{name}.records.json"
        record_path.write_text(json.dumps(records, indent=2) + "\n")
        (arguments.output / f"{name}.sha256").write_text(f"{digest(output.read_bytes())}\n")
        print(f"[+] {name}: wrote {output}")


if __name__ == "__main__":
    main()
