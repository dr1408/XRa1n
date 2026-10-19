#!/usr/bin/env python3
"""Verify every Mach-O CodeDirectory page hash without evaluating Apple DRs.

The patched files retain stock signing requirements or use ldid ad-hoc
signatures. ``codesign -v`` can report a designated-requirement mismatch even
when every on-disk code slot is valid, so use the same CodeDirectory parser
as Liter8's page-hash repair and fail on any stale slot.
"""

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "userland" / "liter8"))
from resign_pagehashes import find_code_directories, find_code_signature, rehash_directory


def slices(data):
    if data[:4] == b"\xcf\xfa\xed\xfe":
        yield data
        return
    magic = data[:4]
    if magic not in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):
        raise ValueError("not a thin or fat Mach-O")
    count = struct.unpack_from(">I", data, 4)[0]
    if not 1 <= count <= 8:
        raise ValueError("unexpected fat Mach-O slice count")
    arch64 = magic == b"\xca\xfe\xba\xbf"
    stride = 32 if arch64 else 20
    for i in range(count):
        at = 8 + stride * i
        if at + stride > len(data):
            raise ValueError("truncated fat Mach-O header")
        if arch64:
            offset, size = struct.unpack_from(">QQ", data, at + 8)
        else:
            offset, size = struct.unpack_from(">II", data, at + 8)
        if offset + size > len(data) or size == 0:
            raise ValueError("invalid fat Mach-O slice extent")
        yield data[offset:offset + size]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path, nargs="+")
    args = parser.parse_args()
    for path in args.binary:
        data = bytearray(path.read_bytes())
        count = 0
        for part in slices(data):
            offset, _ = find_code_signature(part)
            directories = find_code_directories(part, offset)
            stale = sum(rehash_directory(part, cd, False) for cd in directories)
            if stale:
                raise SystemExit(f"[!] {path}: {stale} stale code-signature page hashes")
            count += len(directories)
        print(f"[+] {path}: {count} CodeDirectory(s), all page hashes valid")


if __name__ == "__main__":
    main()
