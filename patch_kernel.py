#!/usr/bin/env python3
"""Transactional, hash-locked XR 22H374 strict Liter8 kernel patcher."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
from dataclasses import dataclass
from pathlib import Path


KERNEL_SHA256 = "8aa487693fbb11d9a1880159f99278dbd140bf212ad96af30067da439a301561"

NOP = 0xD503201F
MOV_W0_ZERO = 0x52800000
MOV_X0_ZERO = 0xD2800000
RET = 0xD65F03C0


def branch(source: int, target: int) -> int:
    """Encode an ARM64 unconditional branch between fixed file offsets."""
    delta = target - source
    if delta % 4 or not -(1 << 27) <= delta < (1 << 27):
        raise ValueError(f"ARM64 branch out of range: {source:#x} -> {target:#x}")
    return 0x14000000 | ((delta // 4) & 0x03FFFFFF)


def conditional_branch(source: int, target: int, condition: int) -> int:
    """Encode an ARM64 B.cond between fixed file offsets."""
    delta = target - source
    if delta % 4 or not -(1 << 20) <= delta < (1 << 20):
        raise ValueError(f"ARM64 conditional branch out of range: {source:#x} -> {target:#x}")
    return 0x54000000 | (((delta // 4) & 0x7FFFF) << 5) | condition


def adr(source: int, target: int, register: int) -> int:
    """Encode ADR for a nearby, potentially unaligned DER byte string."""
    delta = target - source
    if not -(1 << 20) <= delta < (1 << 20):
        raise ValueError(f"ARM64 ADR out of range: {source:#x} -> {target:#x}")
    return 0x10000000 | ((delta & 3) << 29) | (((delta >> 2) & 0x7FFFF) << 5) | register


def aks_full_state_der() -> bytes:
    """Build selector 35's complete 14-member state record."""
    values: dict[str, int | bytes] = {
        "bh": -6, "cxw": 0, "lt": 0, "mua": 0, "sas": 0,
        "sb": 0, "sfa": 0, "sgpe": 0, "sgs": 0, "sls": 3,
        "sms": 0, "srcd": 0, "ss": 6, "uuid": bytes(16),
    }
    members: list[bytes] = []
    for name, value in values.items():
        key = name.encode("ascii")
        encoded_key = bytes((0x0C, len(key))) + key
        encoded_value = (b"\x04\x10" + value if isinstance(value, bytes)
                         else b"\x02\x01" + bytes((value & 0xFF,)))
        member = encoded_key + encoded_value
        members.append(bytes((0x30, len(member))) + member)
    body = b"".join(sorted(members))
    if len(body) != 154:
        raise AssertionError("unexpected AKS state DER length")
    return b"\x31\x81" + bytes((len(body),)) + body


SELECTOR_GATE_CODE = 0x0228E928
EXTERNAL_METHOD_NATIVE = 0x0223C964
SELECTOR35_CODE = 0x02218CBC
SELECTOR35_DATA = SELECTOR35_CODE + 72
SELECTOR35_EPILOGUE = 0x022422BC
SELECTOR35_DER = aks_full_state_der()


def selector_gate_code() -> bytes:
    """Succeed selectors 1 and 10; dispatch all others natively."""
    words = [
        0xD503237F,  # pacibsp; replaced at the externalMethod entry
        0x7100043F,  # cmp w1, #1
        conditional_branch(SELECTOR_GATE_CODE + 0x08, SELECTOR_GATE_CODE + 0x14, 0),  # b.eq success
        0x7100283F,  # cmp w1, #10 (_aks_wrap_key)
        conditional_branch(SELECTOR_GATE_CODE + 0x10, EXTERNAL_METHOD_NATIVE, 1),  # b.ne
        MOV_X0_ZERO,
        0xD50323FF,  # autibsp before the broad early return
        RET,
        NOP,
        NOP,
    ]
    return struct.pack("<10I", *words)


def selector35_code() -> bytes:
    """Copy the full DER into structureOutput, respecting its capacity."""
    words = list(struct.unpack("<18I", bytes.fromhex(
        "892e40f9c90100b48a3240f95f7502f1630100540b000010ac1380d2"
        "6d1540382d1500388c0500f1a1ffff54a81380d2883200f919008052"
        "00000014595880521900bc7200000014"
    )))
    words[5] = adr(SELECTOR35_CODE + 0x14, SELECTOR35_DATA, 11)
    words[14] = branch(SELECTOR35_CODE + 0x38, SELECTOR35_EPILOGUE)
    words[17] = branch(SELECTOR35_CODE + 0x44, SELECTOR35_EPILOGUE)
    return struct.pack("<18I", *words)


@dataclass(frozen=True)
class Patch:
    id: str
    group: str
    offset: int
    original: int
    replacement: int
    summary: str


@dataclass(frozen=True)
class BlobPatch:
    id: str
    group: str
    offset: int
    original: bytes
    replacement: bytes
    summary: str


def acm(name: str, offset: int, second: int, *, preserve_bti: bool = False) -> tuple[Patch, Patch]:
    """Create a guarded two-record ACM success stub."""
    if preserve_bti:
        return (
            Patch(
                f"kernel.credential-manager.{name}.result",
                "credential-manager",
                offset + 4,
                second,
                MOV_W0_ZERO,
                f"Preserve BTI C and return success from {name}",
            ),
            Patch(
                f"kernel.credential-manager.{name}.return",
                "credential-manager",
                offset + 8,
                MOV_W0_ZERO,
                RET,
                f"Return from the forced {name} success path",
            ),
        )
    return (
        Patch(
            f"kernel.credential-manager.{name}.result",
            "credential-manager",
            offset,
            0xD503237F,
            MOV_W0_ZERO,
            f"Return success from {name}",
        ),
        Patch(
            f"kernel.credential-manager.{name}.return",
            "credential-manager",
            offset + 4,
            second,
            RET,
            f"Return from the forced {name} success path",
        ),
    )


def sandbox_stub(name: str, offset: int, second: int) -> tuple[Patch, Patch]:
    return (
        Patch(
            f"kernel.sandbox.{name}.result",
            "sandbox",
            offset,
            0xD503237F,
            MOV_X0_ZERO,
            f"Return success from Seatbelt {name}",
        ),
        Patch(
            f"kernel.sandbox.{name}.return",
            "sandbox",
            offset + 4,
            second,
            RET,
            f"Return from the forced Seatbelt {name} success path",
        ),
    )


PATCHES: tuple[Patch, ...] = (
    # Public Liter8 Sandbox compatibility set.  Change only the low target word
    # of mpc_ops[258]; the authenticated-pointer metadata word is preserved.
    Patch(
        "kernel.sandbox.vnode-check-exec.target",
        "sandbox",
        0x00DDE1A8,
        0x02E6DCE8,
        0x02E6363C,
        "Retarget vnode_check_exec to the file_check_mmap allow stub",
    ),
    *sandbox_stub("file-check-mmap", 0x02E6363C, 0xD107C3FF),
    *sandbox_stub("mount-check-mount", 0x02E62A0C, 0xD10783FF),
    *sandbox_stub("mount-check-remount", 0x02E62908, 0xD107C3FF),
    *sandbox_stub("mount-check-unmount", 0x02E62824, 0xD107C3FF),
    *sandbox_stub("vnode-check-rename", 0x02E5DB18, 0x6DB923E9),

    # Public Liter8 AppleCredentialManager set: 25 methods / 50 records.
    *acm("sepmanagermatchedthreadcallhandler", 0x02218294, 0xD10103FF),
    *acm("callplatformfunction", 0x022187A0, 0xD10183FF),
    *acm("cmdcontextv2", 0x02218998, 0xD101C3FF),
    *acm("cmdcontextv3", 0x02218A94, 0xD101C3FF),
    *acm("performcommandgated", 0x02218CB4, 0xD10343FF),
    *acm("performkernelcontrol", 0x02219784, 0xD10243FF),
    *acm("performcommand", 0x02219B58, 0xD102C3FF),
    *acm("processscrdresponsepayload", 0x02219D98, 0xD10183FF),
    *acm("scheduledblclickdeferredack", 0x0221A04C, 0xD10143FF),
    *acm("performscrdinitialization", 0x0221A19C, 0xD10183FF),
    *acm("sendsepcommand", 0x0221A3B8, 0xD10483FF),
    *acm("setpropertiesgated", 0x0221ADB0, 0xD10203FF),
    *acm("performdoubleclickquerygated", 0x0221B268, 0xD10143FF),
    *acm(
        "performlogginglevelquerygated",
        0x0221B358,
        0xB40000C1,
        preserve_bti=True,
    ),
    *acm("lockitem", 0x0221B53C, 0xD101C3FF),
    *acm("unlockitem", 0x0221B790, 0xD10183FF),
    *acm("handlesepmessage", 0x0221BCAC, 0xD10203FF),
    *acm("readfromsepbuffer", 0x0221BFC8, 0xD10183FF),
    *acm("writetosepbuffer", 0x0221C140, 0xD10243FF),
    *acm("sendsepmessage", 0x0221C508, 0xD10203FF),
    *acm("clearsepbuffer", 0x0221C76C, 0xD10143FF),
    *acm("getsependpoint", 0x0221C944, 0xD10243FF),
    *acm("poweroffactiongated", 0x0221D714, 0xD10203FF),
    *acm("sepmanagermatchedgated", 0x0221DA08, 0xD10203FF),
    *acm("setpowerstategated", 0x022295B0, 0xD101C3FF),

    # Strict Liter8 SEP silencer (10 records).
    Patch("kernel.sep.panic-check.result", "sep-aks", 0x0228E920, 0xD503237F, MOV_X0_ZERO, "Force sepPanicCheck success"),
    Patch("kernel.sep.panic-check.return", "sep-aks", 0x0228E924, 0xA9BE4FF4, RET, "Return from sepPanicCheck"),
    Patch("kernel.sep.did-timeout.result", "sep-aks", 0x0228B2BC, 0xD503237F, MOV_X0_ZERO, "Force AppleSEPManager::_didTimeout false"),
    Patch("kernel.sep.did-timeout.return", "sep-aks", 0x0228B2C0, 0xA9BF7BFD, RET, "Return from _didTimeout"),
    Patch("kernel.sep.paging-off.result", "sep-aks", 0x0228BC2C, 0xD503237F, MOV_X0_ZERO, "Force paging-off notification success"),
    Patch("kernel.sep.paging-off.return", "sep-aks", 0x0228BC30, 0xD10503FF, RET, "Return from paging-off notification"),
    Patch("kernel.sep.set-power-state.gate-1", "sep-aks", 0x0228F2F8, 0xB40019C0, NOP, "Bypass first SEP power-state failure gate"),
    Patch("kernel.sep.notify-os-active.gate", "sep-aks", 0x0228E6B0, 0xB4001320, NOP, "Bypass notifyOSActive failure gate"),
    Patch("kernel.sep.set-power-state.gate-2", "sep-aks", 0x0228F330, 0xB4001820, NOP, "Bypass second SEP power-state failure gate"),
    Patch("kernel.sep.prng-reseed.gate", "sep-aks", 0x022398E8, 0x350004A0, NOP, "Bypass SEP PRNG reseed failure gate"),

    # APFS content-protection bypass, verified against XR 22H374 in IDA.
    # Force apfs_is_valid_class to return true for every caller.
    Patch("kernel.apfs.is-valid-class.result", "apfs", 0x02EE5E98, 0xD503237F, 0x52800020, "Force apfs_is_valid_class to return true"),
    Patch("kernel.apfs.is-valid-class.return", "apfs", 0x02EE5E9C, 0xD10183FF, RET, "Early return from apfs_is_valid_class"),

    # AKS start plus selector-aware state shims.  Do not stub the
    # AppleKeyStoreUserClient::externalMethod entry: callers require the
    # selector-specific output buffers to be populated. Selector 7 supplies
    # scalar state; selector 17 returns a short DER record; selector 35 gets
    # the complete state record. This bisection probe broadly succeeds
    # selectors 1 and 10; every other selector stays native.
    Patch("kernel.aks.external-method-gate", "sep-aks", 0x0223C960, 0xD503237F, branch(0x0223C960, SELECTOR_GATE_CODE), "Broadly succeed selectors 1 and 10 and preserve native dispatch for all other selectors"),
    Patch("kernel.aks.selector35.redirect", "sep-aks", 0x0223F514, 0x5280001A, branch(0x0223F514, SELECTOR35_CODE), "Redirect selector 35 to full DER state shim"),
    Patch("kernel.aks.start-sep-call", "sep-aks", 0x0223BC20, 0x97FFD850, NOP, "Skip the SEP-dependent AKS start call"),
    Patch("kernel.aks.selector7.01", "sep-aks", 0x0223E7F8, 0xF90063FF, 0xB40001F4, "Validate selector-7 argument object"),
    Patch("kernel.aks.selector7.02", "sep-aks", 0x0223E7FC, 0xB900B3FF, 0xF9401288, "Load scalarInput"),
    Patch("kernel.aks.selector7.03", "sep-aks", 0x0223E800, 0xB90133FF, 0xB40001A8, "Validate scalarInput"),
    Patch("kernel.aks.selector7.04", "sep-aks", 0x0223E804, 0x6F00E400, 0xB9402A88, "Load scalarInputCount"),
    Patch("kernel.aks.selector7.05", "sep-aks", 0x0223E808, 0xAD0883E0, 0x7100051F, "Require one scalar input"),
    Patch("kernel.aks.selector7.06", "sep-aks", 0x0223E80C, 0xAD0783E0, 0x54000141, "Reject invalid scalar input count"),
    Patch("kernel.aks.selector7.07", "sep-aks", 0x0223E810, 0x3D803BE0, 0xF9402689, "Load scalarOutput"),
    Patch("kernel.aks.selector7.08", "sep-aks", 0x0223E814, 0xB9402A88, 0xB4000109, "Validate scalarOutput"),
    Patch("kernel.aks.selector7.09", "sep-aks", 0x0223E818, 0x7100051F, 0xB9405288, "Load scalarOutputCount"),
    Patch("kernel.aks.selector7.10", "sep-aks", 0x0223E81C, 0x54030181, 0x7100051F, "Require one scalar output"),
    Patch("kernel.aks.selector7.11", "sep-aks", 0x0223E820, 0xB9405288, 0x540000A1, "Reject invalid scalar output count"),
    Patch("kernel.aks.selector7.12", "sep-aks", 0x0223E824, 0x7100051F, 0xD28000C8, "Synthesize noPin | beenUnlocked state"),
    Patch("kernel.aks.selector7.13", "sep-aks", 0x0223E828, 0x54030121, 0xF9000128, "Store synthesized AKS state"),
    Patch("kernel.aks.selector7.14", "sep-aks", 0x0223E82C, 0x12800008, 0x52800013, "Set selector-7 success result"),
    Patch("kernel.aks.selector7.15", "sep-aks", 0x0223E830, 0xB900BFE8, 0x14001807, "Branch to authenticated success epilogue"),
    Patch("kernel.aks.selector7.16", "sep-aks", 0x0223E834, 0xF9401288, 0x52805853, "Build kIOReturnBadArgument low half"),
    Patch("kernel.aks.selector7.17", "sep-aks", 0x0223E838, 0xB9400102, 0x72BC0013, "Build kIOReturnBadArgument high half"),
    Patch("kernel.aks.selector7.18", "sep-aks", 0x0223E83C, 0x9102F3E3, 0x14001804, "Branch bad arguments to authenticated epilogue"),
    # Selector 17 returns an encoded AppleKeyStore state record, not two raw
    # integers. Preserve the native BTI J and redirect its body to an isolated
    # case-local block that emits DER { bh=-6, ss=4, sls=3 }.
    Patch("kernel.aks.selector17.redirect", "sep-aks", 0x0223F51C, 0xF90073FF, 0x14000747, "Redirect selector-17 to its DER state shim"),
    Patch("kernel.aks.external-method-log", "sep-aks", 0x022461AC, 0x97C93C9A, NOP, "Silence replaced AKS externalMethod operation log"),

    # AMFI and policy helpers (12 records).
    Patch("kernel.amfi.trust-cache.bti", "amfi", 0x02077480, 0xD503237F, 0xD503245F, "Install BTI C landing pad"),
    Patch("kernel.amfi.trust-cache.result", "amfi", 0x02077484, 0xD100C3FF, 0xD2800020, "Return trusted"),
    Patch("kernel.amfi.trust-cache.output-check", "amfi", 0x02077488, 0xA9014FF4, 0xB4000042, "Skip optional output store when null"),
    Patch("kernel.amfi.trust-cache.output-store", "amfi", 0x0207748C, 0xA9027BFD, 0xF9000040, "Store trusted output"),
    Patch("kernel.amfi.trust-cache.return", "amfi", 0x02077490, 0x910083FD, RET, "Return from trust-cache stub"),
    Patch("kernel.amfi.launch-constraints.result", "amfi", 0x0207BD88, 0xD503237F, MOV_W0_ZERO, "Force launch constraints success"),
    Patch("kernel.amfi.launch-constraints.return", "amfi", 0x0207BD8C, 0xA9BA6FFC, RET, "Return from launch constraints"),
    Patch("kernel.amfi.developer-mode.result", "amfi", 0x01323A9C, 0xB0FFB6E8, 0x52800020, "Force developer mode enabled"),
    Patch("kernel.amfi.developer-mode.return", "amfi", 0x01323AA0, 0xF942B108, RET, "Return developer-mode state"),
    Patch("kernel.amfi.dyld-policy.1", "amfi", 0x02084454, 0x97CA7D92, 0x52800020, "Accept first dyld policy helper"),
    Patch("kernel.amfi.dyld-policy.2", "amfi", 0x02084460, 0x97FFDD9D, 0x52800020, "Accept second dyld policy helper"),
    Patch("kernel.amfi.post-validation", "amfi", 0x02083950, 0x7100081F, 0x6B00001F, "Force AMFI post-validation comparison equal"),

    Patch("kernel.debugger.result", "debugger", 0x015B50C8, 0x90FFA4E8, 0xD2800020, "Force PE_i_can_has_debugger true"),
    Patch("kernel.debugger.return", "debugger", 0x015B50CC, 0xB40000E0, RET, "Return debugger permission"),
    Patch("kernel.ppl.loaded-trust-cache", "ppl", 0x015F7FF0, 0x1A9F17E0, 0x52800020, "Force PPL loaded trust-cache result true"),

    Patch("kernel.panic.root-snapshot", "panic", 0x02F200E0, 0x37280CC8, NOP, "Bypass root snapshot panic guard"),
    Patch("kernel.panic.broken-root-seal", "panic", 0x02E85C18, 0x37700160, NOP, "Bypass broken root seal panic guard"),
    Patch("kernel.panic.root-vnode-auth", "panic", 0x0129F744, 0x35001340, NOP, "Bypass root-vnode authentication panic guard"),
    Patch("kernel.panic.unencrypted-data", "panic", 0x02F208FC, 0x370000A8, NOP, "Bypass unencrypted Data panic guard"),

    Patch("kernel.persona.uid", "persona", 0x012D50EC, 0x34FFFDA8, NOP, "Bypass requested persona UID restriction"),
    Patch("kernel.persona.gid", "persona", 0x012D50F4, 0x34FFFD68, NOP, "Bypass requested persona GID restriction"),
    Patch("kernel.usb.restore-mode.result", "usb", 0x02870520, 0xD503237F, 0xD2800020, "Force restore/ramdisk boot predicate true"),
    Patch("kernel.usb.restore-mode.return", "usb", 0x02870524, 0xD10143FF, RET, "Return from restore-mode predicate"),
)


BLOB_PATCHES: tuple[BlobPatch, ...] = (
    BlobPatch(
        "kernel.aks.external-method-gate-body",
        "sep-aks",
        SELECTOR_GATE_CODE,
        bytes.fromhex(
            "fd7b01a9fd430091f30300aa008440f9600000b42024009480000035fd7b41a9"
            "f44fc2a8ff0f5fd6"
        ),
        selector_gate_code(),
        "Preserve PAC, broadly succeed selectors 1 and 10, and route every other selector natively",
    ),
    BlobPatch(
        "kernel.aks.selector35.full-state-code",
        "sep-aks",
        SELECTOR35_CODE,
        bytes.fromhex(
            "fc6f07a9fa6708a9f85f09a9f6570aa9f44f0ba9fd7b0ca9"
            "fd030391fb0304aaf50303aaf60302aafc0301aaf30300aa"
            "ff6b00b9ff3300f9599300f0280358391f79007188030054"
        ),
        selector35_code(),
        "Validate output capacity and copy 157-byte selector-35 DER state",
    ),
    BlobPatch(
        "kernel.aks.selector35.full-state-data",
        "sep-aks",
        SELECTOR35_DATA,
        bytes.fromhex(
            "683242391f050071e1010054700240f9f10313aa31b4f9f2"
            "301ac1da113d80d21002118b080240f9e90310aae00313aa"
            "010080d2f10309aa9193e1f211093fd7030000144018ffd0"
            "00c00e91f6ef01a94818ffd0082d2091e8f300a9e00300f9"
            "4018ffd000481f91aaf1c9971c3b00b45b3c00b4900340f9"
            "f1031caa31b4f9f2301ac1da088e4df8e90310aae0031caa"
            "01008052f10309aa71b6fef211"
        ),
        SELECTOR35_DER,
        "Provide all 14 AppleSEPKeyStore state fields for selector 35",
    ),
    BlobPatch(
        "kernel.aks.selector17.der-state",
        "sep-aks",
        0x02241238,
        bytes.fromhex("885240b9595880521900bc72c8830035881240f9020140b9e3c30291e00316aae10318aa42ebff97f90300aac0820035e2b340b9e00240f9e4830391e5030391e10315aae3031aaa84c5ff97f90300aa60800035e07340f9e1c340b930abc797c07f00b4e10314aaa9b6ff97f90300aafb030014"),
        bytes.fromhex("892e40f9290200b48a3240f95f7900f1c30100540b0200106c3540a92c3500a96c0940f92c0900f96c1940b92c1900b96c3940792c390079c80380d2883200f91900805210040014595880521900bc720d040014311c30070c0262680201fa30070c02737302010430080c03736c730201030000"),
        "Emit DER AKS state with ss=4 and sls=3 for selector 17",
    ),
    BlobPatch("kernel.identity.release.1", "identity", 0x0003ECAF, b"/RELEASE_ARM64_T8020", b"/PATCHED_ARM64_T8020", "Mark first kernel identity string patched"),
    BlobPatch("kernel.identity.release.2", "identity", 0x0003ED1E, b"/RELEASE_ARM64_T8020", b"/PATCHED_ARM64_T8020", "Mark second kernel identity string patched"),
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolve(source: Path) -> tuple[bytearray, list[dict[str, object]]]:
    data = bytearray(source.read_bytes())
    actual = sha256(data)
    if actual != KERNEL_SHA256:
        raise SystemExit(
            "[!] pristine kernel SHA-256 mismatch\n"
            f"    expected {KERNEL_SHA256}\n"
            f"    got      {actual}"
        )

    selected: tuple[Patch | BlobPatch, ...] = (*PATCHES, *BLOB_PATCHES)
    if len(selected) != 125:
        raise SystemExit(f"[!] internal patch inventory mismatch: expected 125, got {len(selected)}")

    # Validate the complete selected plan before mutating the in-memory image.
    seen: set[int] = set()
    for patch in selected:
        original = patch.original if isinstance(patch, BlobPatch) else struct.pack("<I", patch.original)
        for offset in range(patch.offset, patch.offset + len(original)):
            if offset in seen:
                raise SystemExit(f"[!] overlapping patch byte at 0x{offset:x}")
            seen.add(offset)
        if patch.offset + len(original) > len(data):
            raise SystemExit(f"[!] {patch.id} lies outside the kernel")
        current = bytes(data[patch.offset:patch.offset + len(original)])
        if current != original:
            raise SystemExit(
                f"[!] {patch.id} pre-image mismatch at 0x{patch.offset:x}: "
                f"expected {original.hex()}, got {current.hex()}"
            )

    records: list[dict[str, object]] = []
    for patch in selected:
        original = patch.original if isinstance(patch, BlobPatch) else struct.pack("<I", patch.original)
        replacement = patch.replacement if isinstance(patch, BlobPatch) else struct.pack("<I", patch.replacement)
        if len(original) != len(replacement):
            raise SystemExit(f"[!] {patch.id} changes image length")
        data[patch.offset:patch.offset + len(replacement)] = replacement
        records.append(
            {
                "id": patch.id,
                "component": "kernelcache",
                "group": patch.group,
                "offset": patch.offset,
                "offsetHex": f"0x{patch.offset:x}",
                "original": original.hex(),
                "replacement": replacement.hex(),
                "summary": patch.summary,
            }
        )
    return data, records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("kernel", type=Path, help="pristine decompressed XR 22H374 kernelcache")
    parser.add_argument("output", type=Path, nargs="?", default=Path("kernelcache.patched"))
    parser.add_argument("--check", action="store_true", help="validate without writing")
    parser.add_argument(
        "--records",
        type=Path,
        help="JSON record path (default: OUTPUT.records.json)",
    )
    arguments = parser.parse_args()

    if not arguments.kernel.is_file():
        raise SystemExit(f"[!] kernel not found: {arguments.kernel}")
    data, records = resolve(arguments.kernel)
    print(f"[+] resolved {len(records)} guarded kernel patch records")

    if arguments.check:
        print("[+] kernel patch plan validated; no files written")
        return

    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_bytes(data)
    shutil.copymode(arguments.kernel, arguments.output)
    record_path = arguments.records or Path(f"{arguments.output}.records.json")
    record_path.write_text(json.dumps(records, indent=2) + "\n")
    Path(f"{arguments.output}.sha256").write_text(f"{sha256(data)}\n")
    print(f"[+] wrote {arguments.output}")
    print(f"[+] wrote {record_path}")


if __name__ == "__main__":
    main()
