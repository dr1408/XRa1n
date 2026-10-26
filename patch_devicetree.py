#!/usr/bin/env python3
"""
Port of Liter8's DeviceTreePatcher to Python.

Applies the DeviceTree edits used by the hardware-tested Liter8 RC
normal-boot path:

  remove  /defaults/content-protect
  set     /product/boot-ios-diagnostics     = u32:1

Restore boot uses only the first (content-protect removal). The remaining
The stable normal profile enables boot-ios-diagnostics but not
ephemeral-storage or no-effaceable-storage. A separate one-time native
Persistent-Data boot has created the uid-501 session and Personal persona; this
profile now tests loading that persisted state without rerunning migration.

The DT is parsed into a mutable tree, operations are applied, then the tree
is serialized back. This handles ADD, REMOVE, and SET uniformly, including
when the ADD grows the overall DT size.

Apple flattened DeviceTree format (little-endian):

    Node:
        uint32  nprop
        uint32  nchild
        Prop    props[nprop]
        Node    children[nchild]

    Prop:
        char    name[32]                     (NUL-padded)
        uint32  len                          (top bit = placeholder flag; low 31 = length)
        uint8   value[round_up(len & 0x7FFFFFFF, 4)]

Reference: Liter8-iPad8/Sources/Liter8Core/Resolvers/DeviceTree/DeviceTreePatcher.swift
"""

import struct
import sys
from typing import List, Optional, Tuple


DT_KEY_LEN = 32
PLACEHOLDER_FLAG = 0x80000000


class Prop:
    __slots__ = ("name", "flags", "value")

    def __init__(self, name: str, flags: int, value: bytes):
        self.name = name
        self.flags = flags  # top-bit placeholder flag (0 for most, PLACEHOLDER_FLAG for syscfg placeholders)
        self.value = value

    def encode(self) -> bytes:
        name_bytes = self.name.encode("ascii")
        if len(name_bytes) >= DT_KEY_LEN:
            raise ValueError(f"property name too long: {self.name}")
        name_bytes = name_bytes + b"\x00" * (DT_KEY_LEN - len(name_bytes))
        length = len(self.value) | (self.flags & PLACEHOLDER_FLAG)
        padded = self.value + b"\x00" * ((-len(self.value)) & 3)
        return name_bytes + struct.pack("<I", length) + padded


class Node:
    __slots__ = ("props", "children")

    def __init__(self):
        self.props: List[Prop] = []
        self.children: List["Node"] = []

    @property
    def name(self) -> Optional[str]:
        for p in self.props:
            if p.name == "name":
                return p.value.rstrip(b"\x00").decode("ascii", errors="replace")
        return None

    def find_child(self, name: str) -> Optional["Node"]:
        for c in self.children:
            if c.name == name:
                return c
        return None

    def find_prop(self, name: str) -> Optional[Prop]:
        for p in self.props:
            if p.name == name:
                return p
        return None

    def remove_prop(self, name: str) -> bool:
        for i, p in enumerate(self.props):
            if p.name == name:
                del self.props[i]
                return True
        return False

    def set_prop(self, name: str, value: bytes, flags: int = 0) -> None:
        existing = self.find_prop(name)
        if existing is not None:
            existing.value = value
            existing.flags = flags
            return
        # append new property after the last existing one
        self.props.append(Prop(name, flags, value))

    def encode(self) -> bytes:
        out = struct.pack("<II", len(self.props), len(self.children))
        for p in self.props:
            out += p.encode()
        for c in self.children:
            out += c.encode()
        return out


def parse_node(data: bytes, offset: int) -> Tuple[Node, int]:
    nprop, nchild = struct.unpack_from("<II", data, offset)
    offset += 8
    node = Node()
    for _ in range(nprop):
        name_bytes = data[offset : offset + DT_KEY_LEN]
        name = name_bytes.rstrip(b"\x00").decode("ascii", errors="replace")
        offset += DT_KEY_LEN
        length_raw = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        flags = length_raw & PLACEHOLDER_FLAG
        length = length_raw & 0x7FFFFFFF
        value = bytes(data[offset : offset + length])
        padded = (length + 3) & ~3
        offset += padded
        node.props.append(Prop(name, flags, value))
    for _ in range(nchild):
        child, offset = parse_node(data, offset)
        node.children.append(child)
    return node, offset


def parse(data: bytes) -> Node:
    root, consumed = parse_node(data, 0)
    if consumed != len(data):
        print(f"[warn] DT parse consumed {consumed} of {len(data)} bytes", file=sys.stderr)
    return root


# ---- path resolution ------------------------------------------------------

def resolve_path(root: Node, path: str) -> Node:
    """Resolve a slash-separated path to a child node of root."""
    parts = [p for p in path.strip("/").split("/") if p]
    cur = root
    for part in parts:
        nxt = cur.find_child(part)
        if nxt is None:
            raise LookupError(f"DT node not found at /{'/'.join(parts)} (missing: {part})")
        cur = nxt
    return cur


# ---- Liter8 patch plans ---------------------------------------------------

def u32_le(value: int) -> bytes:
    return struct.pack("<I", value)


RESTORE_PATCHES = [
    # remove /defaults/content-protect
    ("remove", "/defaults", "content-protect", None, "Prevent encrypted data volume (sepless boot)"),
]

NORMAL_PATCHES = RESTORE_PATCHES + [
    ("set", "/product", "boot-ios-diagnostics", u32_le(1),
     "Select the stable diagnostics normal-boot path"),
    # Do not enable /chosen/ephemeral-storage or no-effaceable-storage.
]


PATCH_PLANS = {
    "restore": RESTORE_PATCHES,
    "normal": NORMAL_PATCHES,
}


def apply_patches(root: Node, patches) -> List[str]:
    results = []
    for op, path, prop_name, value, desc in patches:
        node = resolve_path(root, path)
        if op == "remove":
            removed = node.remove_prop(prop_name)
            results.append(f"  {'removed' if removed else 'absent'}  {path}/{prop_name}   -- {desc}")
        elif op == "set":
            existing = node.find_prop(prop_name)
            if existing is None:
                node.set_prop(prop_name, value, flags=0)
                results.append(f"  added    {path}/{prop_name} = {value.hex()}   -- {desc}")
            else:
                old = existing.value
                node.set_prop(prop_name, value, flags=existing.flags)
                verb = "updated " if old != value else "unchanged"
                results.append(f"  {verb} {path}/{prop_name} = {value.hex()} (was {old.hex() or '<nil>'})   -- {desc}")
        else:
            raise ValueError(f"unknown op: {op}")
    return results


def main():
    if len(sys.argv) < 3 or sys.argv[2] not in PATCH_PLANS:
        print(
            f"usage: {sys.argv[0]} <DeviceTree.raw> <restore|normal> [-o <output>]",
            file=sys.stderr,
        )
        sys.exit(1)
    inpath = sys.argv[1]
    mode = sys.argv[2]
    outpath = None
    if "-o" in sys.argv:
        i = sys.argv.index("-o")
        outpath = sys.argv[i + 1]
    if outpath is None:
        outpath = inpath + ".patched"

    with open(inpath, "rb") as f:
        data = f.read()
    print(f"[*] input: {inpath} ({len(data)} bytes)")

    root = parse(data)

    print(f"[*] applying Liter8 DeviceTree {mode} plan:")
    for line in apply_patches(root, PATCH_PLANS[mode]):
        print(line)

    new_data = root.encode()
    print(f"\n[*] new DT size: {len(new_data)} bytes  (delta {len(new_data) - len(data):+d})")

    with open(outpath, "wb") as f:
        f.write(new_data)
    print(f"[+] wrote {outpath}")


if __name__ == "__main__":
    main()
