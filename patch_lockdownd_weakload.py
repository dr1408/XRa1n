#!/usr/bin/env python3
"""Add only the release pairing hook to the pinned XR 22H374 lockdownd.

The source is the backed-up, signed System executable. Only Mach-O header
load-command bytes change here; the GitHub workflow repairs its CodeDirectory
page hashes and verifies the resulting signature and entitlements.
"""

import argparse
import hashlib
import struct
from pathlib import Path

from patch_launchd_weakload import (
    LC_CODE_SIGNATURE,
    LC_LOAD_WEAK_DYLIB,
    commands,
    first_text_section,
)

SOURCE_SHA256 = "b74a41d3a5215e08dcc451048fef9175b85b0335b9cd519701beb3b0b7c00491"
HOOK = b"/usr/lib/xra1n-pair.dylib\0"
PROBE = b"l8pair-probe"
EXPECTED_END = 0x1C80
EXPECTED_FIRST_SECTION = 0x1D40


def patch(source: bytes) -> bytes:
    digest = hashlib.sha256(source).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError(f"unknown lockdownd source: {digest}")
    parsed, end = commands(source)
    ceiling = first_text_section(source, parsed)
    if len(parsed) != 60 or end != EXPECTED_END or ceiling != EXPECTED_FIRST_SECTION:
        raise ValueError("unexpected lockdownd Mach-O layout")
    if any(source[end:ceiling]):
        raise ValueError("load-command padding is not empty")
    if PROBE in source or any(HOOK[:-1] in blob for _, blob in parsed):
        raise ValueError("diagnostic or release pairing hook already present")
    if sum(cmd == LC_CODE_SIGNATURE for cmd, _ in parsed) != 1:
        raise ValueError("expected one existing code-signature command")

    size = (24 + len(HOOK) + 7) & ~7
    if size != 56 or end + size > ceiling:
        raise ValueError("pairing weak load does not fit in header slack")
    command = (struct.pack("<6I", LC_LOAD_WEAK_DYLIB, size, 24, 0, 0x10000, 0x10000)
               + HOOK.ljust(size - 24, b"\0"))
    position = next(i for i, (cmd, _) in enumerate(parsed) if cmd == LC_CODE_SIGNATURE)
    revised = parsed[:position] + [(LC_LOAD_WEAK_DYLIB, command)] + parsed[position:]
    load_area = b"".join(blob for _, blob in revised)
    output = bytearray(source)
    output[32:ceiling] = load_area.ljust(ceiling - 32, b"\0")
    struct.pack_into("<II", output, 16, len(revised), len(load_area))
    verified, new_end = commands(output)
    if (new_end != end + size or first_text_section(output, verified) != ceiling
            or output[ceiling:] != source[ceiling:]
            or sum(cmd == LC_LOAD_WEAK_DYLIB and HOOK in blob
                   for cmd, blob in verified) != 1
            or PROBE in output):
        raise AssertionError("lockdownd output failed structural verification")
    return bytes(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = patch(args.source.read_bytes())
    print(f"release-only lockdownd unsigned SHA-256: {hashlib.sha256(result).hexdigest()}")
    if args.output:
        if args.output.resolve() == args.source.resolve():
            parser.error("source and output must differ")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(result)
        print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
