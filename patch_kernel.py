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

    # AKS start, broad USBLiter8-style externalMethod success override, and
    # operation-log silencer.  This intentionally affects every AKS selector:
    # AppleKeyStoreUserClient::externalMethod returns kIOReturnSuccess before
    # validating arguments or entering the selector dispatcher.
    Patch("kernel.aks.start-sep-call", "sep-aks", 0x0223BC20, 0x97FFD850, NOP, "Skip the SEP-dependent AKS start call"),
    Patch("kernel.aks.external-method-success", "sep-aks", 0x02245F64, 0xD503237F, MOV_X0_ZERO, "Force every AKS externalMethod selector to return success"),
    Patch("kernel.aks.external-method-return", "sep-aks", 0x02245F68, 0xD10643FF, RET, "Return immediately from the forced AKS externalMethod success path"),
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
    BlobPatch("kernel.identity.release.1", "identity", 0x0003ECAF, b"/RELEASE_ARM64_T8020", b"/PATCHED_ARM64_T8020", "Mark first kernel identity string patched"),
    BlobPatch("kernel.identity.release.2", "identity", 0x0003ED1E, b"/RELEASE_ARM64_T8020", b"/PATCHED_ARM64_T8020", "Mark second kernel identity string patched"),
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolve(source: Path, groups: set[str]) -> tuple[bytearray, list[dict[str, object]]]:
    data = bytearray(source.read_bytes())
    actual = sha256(data)
    if actual != KERNEL_SHA256:
        raise SystemExit(
            "[!] pristine kernel SHA-256 mismatch\n"
            f"    expected {KERNEL_SHA256}\n"
            f"    got      {actual}"
        )

    selected: list[Patch | BlobPatch] = [patch for patch in (*PATCHES, *BLOB_PATCHES) if patch.group in groups]
    expected_counts = {
        "amfi": 12,
        "apfs": 2,
        "credential-manager": 50,
        "debugger": 2,
        "identity": 2,
        "panic": 4,
        "persona": 2,
        "ppl": 1,
        "sandbox": 11,
        "sep-aks": 14,
        "usb": 2,
    }
    for group in groups:
        count = sum(patch.group == group for patch in selected)
        if count != expected_counts[group]:
            raise SystemExit(
                f"[!] internal {group} inventory mismatch: "
                f"expected {expected_counts[group]}, got {count}"
            )

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
    parser.add_argument("kernel", type=Path, nargs="?", help="pristine decompressed XR 22H374 kernelcache")
    parser.add_argument("output", type=Path, nargs="?", default=Path("kernelcache.patched"))
    parser.add_argument(
        "--profile",
        choices=("full", "no-sep-aks", "no-credential-manager", "no-sep-aks-no-acm", "custom"),
        help=("select a named diagnostic profile; full is the existing all-groups behavior. "
              "Profiles excluding groups are experiments, not confirmed bootable kernels"),
    )
    parser.add_argument(
        "--group",
        action="append",
        choices=("amfi", "apfs", "credential-manager", "debugger", "identity", "panic", "persona", "ppl", "sandbox", "sep-aks", "usb"),
        dest="groups",
        help="apply only this group (repeatable; can be combined with --profile custom)",
    )
    parser.add_argument("--list-profiles", action="store_true", help="print profile contents and exit")
    parser.add_argument("--check", action="store_true", help="validate without writing")
    parser.add_argument(
        "--records",
        type=Path,
        help="JSON record path (default: OUTPUT.records.json)",
    )
    arguments = parser.parse_args()

    all_groups = {
        "amfi", "credential-manager", "debugger", "identity", "panic",
        "persona", "ppl", "sandbox", "sep-aks", "usb", "apfs",
    }
    profiles = {
        "full": all_groups,
        "no-sep-aks": all_groups - {"sep-aks"},
        "no-credential-manager": all_groups - {"credential-manager"},
        "no-sep-aks-no-acm": all_groups - {"sep-aks", "credential-manager"},
    }
    if arguments.list_profiles:
        for name, selected in profiles.items():
            print(f"{name}: {', '.join(sorted(selected))}")
        print("custom: specify one or more --group values")
        return
    if arguments.kernel is None:
        parser.error("kernel is required unless --list-profiles is used")

    if arguments.profile and arguments.profile != "custom" and arguments.groups:
        parser.error("use either a named --profile or repeated --group values, not both")
    if arguments.profile == "custom" and not arguments.groups:
        parser.error("--profile custom requires at least one --group")
    if arguments.profile == "custom" or arguments.groups:
        groups = set(arguments.groups or ())
        profile_name = "custom"
    else:
        profile_name = arguments.profile or "full"
        groups = set(profiles[profile_name])

    if not arguments.kernel.is_file():
        raise SystemExit(f"[!] kernel not found: {arguments.kernel}")
    data, records = resolve(arguments.kernel, groups)
    print(f"[+] profile: {profile_name} ({len(records)} patch records)")
    for group in sorted(groups):
        print(f"[+] {group}: resolved {sum(r['group'] == group for r in records)} guarded records")

    if arguments.check:
        print("[+] selected kernel patch groups validated; no files written")
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
