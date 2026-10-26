"""Fail closed when a boot stage or its installed AP ticket is stale."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from patch_iboot import MODE_PRESETS, apply_serial

ROOT = Path(__file__).resolve().parent
DEFAULT_TICKET = ROOT / "tickets/apticket.im4m"
RESTORE_COMPLETE = ROOT / "tickets/restore-complete.json"


def selected_ticket(path: Path | None = None, *, require_complete: bool = True) -> Path:
    """Select only the latest erase-restore ticket unless explicitly overridden."""
    ticket = (path or DEFAULT_TICKET).expanduser().resolve()
    if not ticket.is_file():
        raise RuntimeError(
            f"missing erase-restore AP ticket: {ticket}; run the patched erase restore first"
        )
    liter8_path = str(ROOT / "scripts/liter8")
    if liter8_path not in sys.path:
        sys.path.insert(0, liter8_path)
    from apticket import validate_im4m

    data = ticket.read_bytes()
    validate_im4m(data)
    if ticket.parent.name == "archive":
        digest = hashlib.sha384(data).hexdigest().upper()
        if ticket.stem.upper() != digest:
            raise RuntimeError(f"ticket archive name does not match SHA-384: {ticket}")
    if require_complete:
        if not RESTORE_COMPLETE.is_file():
            raise RuntimeError(
                f"no completed patched erase restore marker: {RESTORE_COMPLETE}; "
                "ticket capture alone is insufficient for Preboot"
            )
        marker = json.loads(RESTORE_COMPLETE.read_text())
        if marker.get("ticketSha384") != hashlib.sha384(data).hexdigest().upper():
            raise RuntimeError("selected ticket does not match completed patched erase restore")
        if marker.get("profileID") != "iphone11,8-n841ap-22H374":
            raise RuntimeError("completed restore marker has wrong device/build profile")
    return ticket


def require_stage_args(path: Path, mode: str, serial: bool = False) -> None:
    """Check the exact boot args for the selected console mode."""
    if mode not in {"normal", "restore", "ramdisk", "ibec"}:
        raise ValueError(f"unsupported boot-argument mode: {mode}")
    if not path.is_file():
        raise RuntimeError(f"missing {mode} boot stage: {path}")
    literal = apply_serial(MODE_PRESETS[mode], serial).encode() + b"\0"
    count = path.read_bytes().count(literal)
    if count != 1:
        raise RuntimeError(
            f"{path} has {count} exact {mode} boot-argument literals "
            f"for serial={serial}; expected 1"
        )
    if not serial and b"serial=3\0" in path.read_bytes():
        raise RuntimeError(f"{path} unexpectedly contains serial boot arguments")
