#!/usr/bin/env python3
"""Rebuild or verify the 22H374 restore/SSHRD iBoot stage patches offline."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from boot_stage import require_stage_args

ROOT = Path(__file__).resolve().parent
STOCK = ROOT / "work/restore"
STOCK_SHA256 = "8d748ce2190a5889ecd4933bd212f90d1308d44d8cee1880ed92b4bbeb057753"
STAGES = (
    ("iBSS", "restore", "restore", "restore"),
    ("iBEC", "restore", "ibec", "restore"),
    ("iBSS", "sshrd", "ramdisk", "ramdisk"),
    ("iBEC", "sshrd", "ramdisk", "ramdisk"),
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="compare without writing")
    parser.add_argument("--serial", action="store_true", help="route kernel console to DCSD UART")
    parser.add_argument("--stage", choices=("all", "restore", "sshrd"), default="all",
                        help="limit regeneration so SSHRD mode changes leave restore stages intact")
    args = parser.parse_args()
    for name in ("iBSS", "iBEC"):
        stock = STOCK / f"{name}.restore.raw"
        if not stock.is_file() or sha256(stock) != STOCK_SHA256:
            raise RuntimeError(f"pristine {name} hash mismatch: {stock}")

    with tempfile.TemporaryDirectory(prefix="xra1n-iboot-") as temporary:
        staging = Path(temporary)
        prepared: list[tuple[Path, Path, Path, str]] = []
        for name, directory, mode, suffix in STAGES:
            if args.stage != "all" and directory != args.stage:
                continue
            source = STOCK / f"{name}.restore.raw"
            raw = staging / f"{directory}-{name}.raw"
            patched = Path(str(raw) + ".patched")
            log = staging / f"{directory}-{name}.patch.log"
            target = ROOT / "work" / directory / f"{name}.{suffix}.raw.patched"
            target_log = ROOT / "work" / directory / f"{name}.patch.log"
            shutil.copy2(source, raw)
            with log.open("wb") as output:
                subprocess.run([sys.executable, str(ROOT / "patch_iboot.py"),
                                str(raw), mode, "--serial" if args.serial else "--no-serial"], check=True,
                               stdout=output, stderr=subprocess.STDOUT)
            require_stage_args(patched, mode, args.serial)
            if patched.read_bytes() == source.read_bytes():
                raise RuntimeError(f"{directory} {name} patch made no changes")
            prepared.append((patched, target, log, str(target_log)))

        for patched, target, log, target_log in prepared:
            if args.check:
                if not target.is_file() or sha256(target) != sha256(patched):
                    raise RuntimeError(
                        f"stale {target}; rebuild with python3 prepare_boot_stages.py"
                    )
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(patched, target)
                shutil.copy2(log, target_log)
            print(f"[+] {'verified' if args.check else 'built'} {target}: {sha256(patched)}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"[!] {error}") from error
