#!/usr/bin/env python3
"""Reproduce Liter8's iBoot-LZFSE IM4P repack, without touching a device.

The encoder is Apple's libcompression algorithm 0x891, not the generic 0x801
LZFSE encoder. Consequently repacking requires macOS. ``--inspect`` works on
any host and reports the DER structure of an existing IM4P.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import os
import sys
import tempfile
from pathlib import Path
from typing import Callable


IBOOT_LZFSE = 0x891


def der_length(size: int) -> bytes:
    if size < 0 or size >= 1 << 32:
        raise ValueError("DER size out of range")
    if size < 128:
        return bytes([size])
    value = size.to_bytes((size.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(value)]) + value


def tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + der_length(len(value)) + value


def der_integer(value: int) -> bytes:
    if value < 0:
        raise ValueError("negative DER INTEGER")
    raw = value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")
    if raw[0] & 0x80:
        raw = b"\x00" + raw
    return tlv(0x02, raw)


def children(container: bytes) -> list[tuple[int, bytes, bytes]]:
    """Return (tag, value, complete DER child) for an exact top-level SEQUENCE."""
    def read(at: int) -> tuple[int, int, int]:
        if at + 2 > len(container):
            raise ValueError("truncated DER tag/length")
        tag, lead = container[at], container[at + 1]
        cursor = at + 2
        if lead & 0x80:
            count = lead & 0x7f
            if count == 0 or count > 4 or cursor + count > len(container):
                raise ValueError("invalid DER length")
            size = int.from_bytes(container[cursor:cursor + count], "big")
            cursor += count
        else:
            size = lead
        end = cursor + size
        if end > len(container):
            raise ValueError("DER child exceeds container")
        return tag, cursor, end

    tag, start, end = read(0)
    if tag != 0x30 or end != len(container):
        raise ValueError("expected one complete IM4P SEQUENCE")
    result = []
    cursor = start
    while cursor < end:
        tag, value_start, next_cursor = read(cursor)
        result.append((tag, container[value_start:next_cursor], container[cursor:next_cursor]))
        cursor = next_cursor
    return result


def inspect(container: bytes) -> dict[str, object]:
    fields = children(container)
    if len(fields) < 4 or [item[0] for item in fields[:4]] != [0x16, 0x16, 0x16, 0x04]:
        raise ValueError("not an IM4P with type, description and payload")
    if fields[0][1] != b"IM4P":
        raise ValueError("wrong Image4 magic")
    compressed = fields[3][1].startswith(b"bvx2")
    descriptor = None
    if compressed:
        if len(fields) < 5 or fields[4][0] != 0x30:
            raise ValueError("LZFSE payload has no compression descriptor")
        ints = children(fields[4][2])
        if len(ints) != 2 or any(item[0] != 0x02 for item in ints):
            raise ValueError("malformed compression descriptor")
        descriptor = tuple(int.from_bytes(item[1], "big") for item in ints)
        if descriptor[0] != 1:
            raise ValueError("unexpected compression descriptor version")
    return {
        "fourcc": fields[1][1].decode("ascii"),
        "description": fields[2][1].decode("utf-8"),
        "compressed": compressed,
        "descriptor": descriptor,
        "payload_size": len(fields[3][1]),
        "field_tags": [f"0x{item[0]:02x}" for item in fields],
        "sha256": hashlib.sha256(container).hexdigest(),
    }


class AppleCompression:
    def __init__(self) -> None:
        if sys.platform != "darwin":
            raise RuntimeError("Apple libcompression 0x891 requires macOS; no 0x801 fallback")
        library = ctypes.CDLL("/usr/lib/libcompression.dylib")
        self._encode = library.compression_encode_buffer
        self._decode = library.compression_decode_buffer
        for function in (self._encode, self._decode):
            function.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                                 ctypes.c_size_t, ctypes.c_void_p, ctypes.c_uint32]
            function.restype = ctypes.c_size_t

    def encode(self, raw: bytes) -> bytes:
        capacity = max(len(raw) + len(raw) // 8 + 1024, 4096)
        output = ctypes.create_string_buffer(capacity)
        source = ctypes.create_string_buffer(raw)
        size = self._encode(output, capacity, source, len(raw), None, IBOOT_LZFSE)
        if not size:
            raise RuntimeError("0x891 compression failed")
        return output.raw[:size]

    def decode(self, encoded: bytes, expected_size: int) -> bytes:
        output = ctypes.create_string_buffer(expected_size + 1)
        source = ctypes.create_string_buffer(encoded)
        size = self._decode(output, expected_size + 1, source, len(encoded), None,
                            IBOOT_LZFSE)
        if size != expected_size:
            raise RuntimeError(f"0x891 decoded {size} bytes; expected {expected_size}")
        return output.raw[:size]


def repack(source: bytes, replacement: bytes,
           encode: Callable[[bytes], bytes],
           decode: Callable[[bytes, int], bytes]) -> bytes:
    if not replacement:
        raise ValueError("empty replacement payload")
    original = inspect(source)
    if not original["compressed"]:
        raise ValueError("source is not LZFSE-compressed")
    fields = children(source)
    compressed = encode(replacement)
    if not compressed.startswith(b"bvx2") or decode(compressed, len(replacement)) != replacement:
        raise RuntimeError("0x891 compressed payload failed restricted round-trip")

    # Liter8's IM4P builder retains the original FourCC and description, uses
    # SEQUENCE { INTEGER 1, INTEGER uncompressed-size }, and copies PAYP only
    # for the firmware types where it is meaningful. iBEC has no PAYP.
    descriptor = tlv(0x30, der_integer(1) + der_integer(len(replacement)))
    body = b"".join(field[2] for field in fields[:3]) + tlv(0x04, compressed) + descriptor
    if original["fourcc"] in {"krnl", "rkrn", "trxm"}:
        payp = [field[2] for field in fields[4:]
                if field[0] == 0xa0 and b"PAYP" in field[1]]
        if len(payp) > 1:
            raise ValueError("ambiguous PAYP children")
        body += b"".join(payp)
    output = tlv(0x30, body)
    rebuilt = inspect(output)
    if (rebuilt["fourcc"], rebuilt["description"], rebuilt["descriptor"]) != (
            original["fourcc"], original["description"], (1, len(replacement))):
        raise RuntimeError("repacked IM4P metadata mismatch")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="pristine, compressed source IM4P")
    parser.add_argument("replacement", nargs="?", type=Path, help="patched raw payload")
    parser.add_argument("output", nargs="?", type=Path, help="new IM4P output")
    parser.add_argument("--inspect", action="store_true", help="only report IM4P metadata")
    parser.add_argument("--compare", type=Path, help="report metadata of another IM4P")
    args = parser.parse_args()
    for label, path in (("source", args.source), ("compare", args.compare)):
        if path:
            print(f"{label}: {path}: {inspect(path.read_bytes())}")
    if args.inspect:
        if args.replacement or args.output:
            parser.error("--inspect does not accept replacement/output")
        return
    if not args.replacement or not args.output:
        parser.error("replacement and output are required unless --inspect is used")
    if args.output.resolve() in {args.source.resolve(), args.replacement.resolve()}:
        parser.error("output must differ from inputs")
    codec = AppleCompression()
    replacement = args.replacement.read_bytes()
    rebuilt = repack(args.source.read_bytes(), replacement, codec.encode, codec.decode)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".iboot-repack-", dir=args.output.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(rebuilt)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, args.output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"repacked: {args.output}: {inspect(rebuilt)}")


if __name__ == "__main__":
    main()
