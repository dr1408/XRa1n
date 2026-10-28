#!/usr/bin/env python3
"""Add the XR Wi-Fi credential shim weak dependency to pinned 22H374 wifid.

Only Mach-O load-command bytes are changed.  The workflow subsequently
repairs the existing CodeDirectory page hashes, retaining Apple's identifier,
entitlements, requirements and launch constraints.
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

SOURCE_SHA256 = "e982be216989321f4f82d2fc0bf1bd69b69745cba5c92106fd7b8f70a29bd8b4"
HOOK = b"/var/jb/usr/lib/wifi-keychain-shim.dylib\0"
EXPECTED_COMMANDS = 94
EXPECTED_END = 0x2A00
EXPECTED_FIRST_SECTION = 0x2B00


def patch(source: bytes) -> bytes:
    digest = hashlib.sha256(source).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError(f"unknown wifid source: {digest}")

    parsed, end = commands(source)
    ceiling = first_text_section(source, parsed)
    if (len(parsed) != EXPECTED_COMMANDS or end != EXPECTED_END
            or ceiling != EXPECTED_FIRST_SECTION):
        raise ValueError("unexpected wifid Mach-O layout")
    if any(source[end:ceiling]):
        raise ValueError("wifid load-command padding is not empty")
    if any(HOOK[:-1] in blob for _, blob in parsed):
        raise ValueError("Wi-Fi shim dependency is already present")
    if sum(cmd == LC_CODE_SIGNATURE for cmd, _ in parsed) != 1:
        raise ValueError("expected one existing code-signature command")

    size = (24 + len(HOOK) + 7) & ~7
    if end + size > ceiling:
        raise ValueError("Wi-Fi weak load does not fit in header slack")
    command = (struct.pack("<6I", LC_LOAD_WEAK_DYLIB, size, 24,
                           0, 0x10000, 0x10000)
               + HOOK.ljust(size - 24, b"\0"))

    # Keep LC_CODE_SIGNATURE last, matching the existing userland patchers.
    position = next(i for i, (cmd, _) in enumerate(parsed)
                    if cmd == LC_CODE_SIGNATURE)
    revised = parsed[:position] + [(LC_LOAD_WEAK_DYLIB, command)] + parsed[position:]
    load_area = b"".join(blob for _, blob in revised)
    output = bytearray(source)
    output[32:ceiling] = load_area.ljust(ceiling - 32, b"\0")
    struct.pack_into("<II", output, 16, len(revised), len(load_area))

    verified, new_end = commands(output)
    if (new_end != end + size
            or first_text_section(output, verified) != ceiling
            or output[ceiling:] != source[ceiling:]
            or sum(cmd == LC_LOAD_WEAK_DYLIB and HOOK in blob
                   for cmd, blob in verified) != 1):
        raise AssertionError("wifid output failed structural verification")
    return bytes(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = patch(args.source.read_bytes())
    print(f"wifid weak-load unsigned SHA-256: {hashlib.sha256(result).hexdigest()}")
    if args.output:
        if args.output.resolve() == args.source.resolve():
            parser.error("source and output must differ")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(result)
        print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
