#!/usr/bin/env python3
"""Create an unsigned XR 22H374 launchd with Liter8's /usr/lib/lhook weak dependency.

XR has 16 bytes of load-command slack. Drop only LC_FUNCTION_STARTS and an
empty LC_DATA_IN_CODE command (32 bytes total), then add the 40-byte dependency.
No segment, section, code, or LINKEDIT bytes are moved. This is an *offline*
candidate; it must be re-signed and boot-tested before deployment.
"""

import argparse
import hashlib
import struct
from pathlib import Path

EXPECTED_SHA256 = "e2323c09df447b6d57bc3d9132be82f7186af167a656921212ca9880e29dea06"
EXPECTED_COMMAND_END = 0xC78
EXPECTED_FIRST_SECTION = 0xC88
LC_SEGMENT_64 = 0x19
LC_FUNCTION_STARTS = 0x26
LC_DATA_IN_CODE = 0x29
LC_CODE_SIGNATURE = 0x1D
LC_LOAD_WEAK_DYLIB = 0x80000018
HOOK = b"/usr/lib/lhook\0"


def u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def commands(data):
    if u32(data, 0) != 0xFEEDFACF or u32(data, 12) != 2:
        raise ValueError("not a 64-bit Mach-O executable")
    pos = 32
    result = []
    for _ in range(u32(data, 16)):
        cmd, size = struct.unpack_from("<II", data, pos)
        if size < 8 or size % 8 or pos + size > len(data):
            raise ValueError(f"invalid load command at {pos:#x}")
        result.append((cmd, bytes(data[pos:pos + size])))
        pos += size
    if pos != 32 + u32(data, 20):
        raise ValueError("sizeofcmds mismatch")
    return result, pos


def first_text_section(data, parsed):
    offsets = []
    for cmd, blob in parsed:
        if cmd != LC_SEGMENT_64 or blob[8:24].split(b"\0", 1)[0] != b"__TEXT":
            continue
        nsects = u32(blob, 64)
        if len(blob) != 72 + nsects * 80:
            raise ValueError("malformed __TEXT section table")
        offsets += [u32(blob, 72 + i * 80 + 48) for i in range(nsects)]
    if not offsets:
        raise ValueError("no __TEXT sections")
    return min(offsets)


def weak_command():
    size = (24 + len(HOOK) + 7) & ~7
    if size != 40:
        raise AssertionError("Liter8 weak-load command size changed")
    return struct.pack("<6I", LC_LOAD_WEAK_DYLIB, size, 24, 0, 0x10000, 0x10000) + HOOK.ljust(size - 24, b"\0")


def patch(pristine):
    digest = hashlib.sha256(pristine).hexdigest()
    if digest != EXPECTED_SHA256:
        raise ValueError(f"XR 22H374 launchd SHA mismatch: {digest}")
    parsed, end = commands(pristine)
    ceiling = first_text_section(pristine, parsed)
    if len(parsed) != 20 or end != EXPECTED_COMMAND_END or ceiling != EXPECTED_FIRST_SECTION:
        raise ValueError("unexpected XR launchd load-command layout")
    if any(pristine[end:ceiling]):
        raise ValueError("load-command slack is not zero padding")
    if sum(cmd == LC_FUNCTION_STARTS for cmd, _ in parsed) != 1:
        raise ValueError("expected one LC_FUNCTION_STARTS")
    data_cmds = [blob for cmd, blob in parsed if cmd == LC_DATA_IN_CODE]
    if len(data_cmds) != 1 or len(data_cmds[0]) != 16 or u32(data_cmds[0], 12) != 0:
        raise ValueError("LC_DATA_IN_CODE is not an empty 16-byte command")
    if sum(cmd == LC_CODE_SIGNATURE for cmd, _ in parsed) != 1:
        raise ValueError("expected one code-signature command to preserve for re-signing")
    if any(cmd == LC_LOAD_WEAK_DYLIB and HOOK[:-1] in blob for cmd, blob in parsed):
        raise ValueError("lhook is already loaded")

    kept = [(cmd, blob) for cmd, blob in parsed if cmd not in (LC_FUNCTION_STARTS, LC_DATA_IN_CODE)]
    # Put the dependency before LC_CODE_SIGNATURE; leave all data references
    # and signature offsets unchanged. Re-signing is a separate mandatory step.
    sig_index = next(i for i, (cmd, _) in enumerate(kept) if cmd == LC_CODE_SIGNATURE)
    kept.insert(sig_index, (LC_LOAD_WEAK_DYLIB, weak_command()))
    load_area = b"".join(blob for _, blob in kept)
    new_end = 32 + len(load_area)
    if new_end != 0xC80 or new_end > ceiling:
        raise ValueError("replacement commands do not fit")
    out = bytearray(pristine)
    out[32:ceiling] = load_area.ljust(ceiling - 32, b"\0")
    struct.pack_into("<II", out, 16, len(kept), len(load_area))
    if out[ceiling:] != pristine[ceiling:]:
        raise AssertionError("section, code or LINKEDIT bytes changed")
    revised, revised_end = commands(out)
    if revised_end != new_end or first_text_section(out, revised) != ceiling:
        raise AssertionError("patched Mach-O failed structural validation")
    return bytes(out), len(parsed), len(revised), end, new_end, ceiling


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--apply", action="store_true", help="write the unsigned candidate")
    args = parser.parse_args()
    candidate, old_n, new_n, old_end, new_end, ceiling = patch(args.input.read_bytes())
    print(f"load commands: {old_n} -> {new_n}; end: {old_end:#x} -> {new_end:#x}; first section: {ceiling:#x}")
    print(f"remaining slack: {ceiling - new_end} bytes; weak dependency: {HOOK[:-1].decode()}")
    print(f"candidate SHA-256: {hashlib.sha256(candidate).hexdigest()} (unsigned)")
    if args.apply:
        if args.output is None or args.output.resolve() == args.input.resolve():
            parser.error("--apply requires a distinct -o/--output path")
        args.output.write_bytes(candidate)
        print(f"wrote {args.output}")
    else:
        print("dry-run only; no file written")


if __name__ == "__main__":
    main()
