"""Resolve and verify the pinned Linux/aarch64 XRa1n host-tool bundle."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BUNDLE = ROOT / "tools/host/linux-aarch64"
EXPECTED_SHA256 = {
    "usbliter8ctl": "84c1b6eff46a5553d5f5132b0f2cd66aeb72cdbda3b5fbb66a18d4b4b91387ba",
    "img4": "ec899dd643de63f4e54adf431c517ff9dd949130235731f8dd20a33eaf02d1ea",
    "idevicerestore": "84c5bfd2449361ec9614e25ad353667687b7362dcf13f33b222b38b41aa1bda9",
    "irecovery": "3024f85c99bf29c581bde40c7b9f873dbdd04234bdf40113043f33437d307285",
    "iproxy": "d8a9098d47b9b0e4bc70034bc0a84d5ecb6dd3706ccac8c41cbf3c1d4e8043a8",
    "usbmuxd": "2e29f051c6ef33497fb6cdd4659622b4787cf17cff6899502a0016e3f6061ce3",
    "sshpass": "f67dc2e9079a4df30c505a6049b230d21d2785e5e98698aa08beac74dd23b516",
    "idevice_id": "a3c4c5d1fdd2d2dbd5a60502d60d9c8143c50266dfd9c1703408d76acd3f77a8",
    "idevicepair": "1622cfe7028940a929f07c8bf1d4026db78000656e0e2f20299b59edd56e8d3f",
    "apple-tss-ca-bundle.pem": "0fbf5cc4030cc5157dc1d7bf6e38660664794898bff8fd9ea6b2ae441a8ce22c",
    "lib/libirecovery-1.0.so.5.1.2": "8f661ef8c8515479c0623b1152642353e90b10bb9a31c3b0b330dac89e8f107d",
}


def bundle_dir() -> Path | None:
    override = os.environ.get("XRA1N_TOOLS_DIR")
    if override:
        directory = Path(override).expanduser().resolve()
        if not directory.is_dir():
            raise RuntimeError(f"XRA1N_TOOLS_DIR is not a directory: {directory}")
        return directory
    return BUNDLE if BUNDLE.is_dir() else None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_bundle(directory: Path | None = None) -> Path:
    directory = directory or bundle_dir()
    if directory is None:
        raise RuntimeError("XRa1n host-tool bundle is missing")
    for name, expected in EXPECTED_SHA256.items():
        path = directory / name
        if not path.is_file() or path.is_symlink() or sha256(path) != expected:
            raise RuntimeError(f"missing or hash-mismatched host tool: {path}")
        if not name.endswith(".pem") and not name.startswith("lib/") and not os.access(path, os.X_OK):
            raise RuntimeError(f"host tool is not executable: {path}")
    link = directory / "lib/libirecovery-1.0.so.5"
    if not link.is_symlink() or os.readlink(link) != "libirecovery-1.0.so.5.1.2":
        raise RuntimeError(f"invalid bundled libirecovery SONAME link: {link}")
    return directory


def tool(name: str) -> Path:
    if name not in EXPECTED_SHA256 or "/" in name or name.endswith(".pem"):
        raise ValueError(f"unknown host tool: {name}")
    directory = bundle_dir()
    if directory is not None:
        path = directory / name
        if not path.is_file():
            raise RuntimeError(f"bundled host tool missing: {path}")
        return path
    found = shutil.which(name)
    if found is None:
        raise RuntimeError(f"host tool not found: {name}")
    return Path(found)


def ca_bundle() -> Path:
    directory = bundle_dir()
    if directory is not None:
        path = directory / "apple-tss-ca-bundle.pem"
        if not path.is_file():
            raise RuntimeError(f"Apple TSS CA bundle missing: {path}")
        return path
    return ROOT / ".run/apple-tss-ca-bundle.pem"


def configure() -> None:
    """Verify the selected bundle and make its named tools visible to children."""
    directory = bundle_dir()
    if directory is not None:
        verify_bundle(directory)
        os.environ["PATH"] = f"{directory}{os.pathsep}{os.environ.get('PATH', '')}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify every bundled tool and the TSS CA")
    args = parser.parse_args()
    if args.check:
        directory = verify_bundle()
        print(f"[+] verified {len(EXPECTED_SHA256)} host files: {directory}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError) as error:
        raise SystemExit(f"[!] {error}") from error
