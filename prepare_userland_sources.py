#!/usr/bin/env python3
"""Validate and copy exact XR 22H374 stock files from a directory or ZIP."""

import argparse
import hashlib
import zipfile
from pathlib import Path

EXPECTED = {
    "coreauthd": "d479cb7f9de2531e85c5af0edd7d91ed9f4a29fea7e192565f7754f1440b22d7",
    "mobileactivationd": "17076de71a65e1096c74ee87ee6d4460748f2dfc7ffb9760ba4cf8439bfbb26e",
    "ctkd": "97c328e8a85248c0b3b0471a92aa0f13bebdc8a7730f1a43014dbeaf214e8657",
    "usermanagerd": "4dd3412ac0f6619af541077ab8f14fb78da6d9b5c8713b6f88f231aaa7c05e69",
    "launchd": "e2323c09df447b6d57bc3d9132be82f7186af167a656921212ca9880e29dea06",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.archive.is_dir():
        sources = {name: (args.archive / name).read_bytes() for name in EXPECTED}
    else:
        sources = {}
        with zipfile.ZipFile(args.archive) as archive:
            for name in EXPECTED:
                matches = [item for item in archive.infolist() if Path(item.filename).name == name]
                if len(matches) != 1 or matches[0].is_dir():
                    raise SystemExit(f"[!] expected exactly one ZIP member named {name}: got {len(matches)}")
                sources[name] = archive.read(matches[0])
    for name, expected in EXPECTED.items():
        data = sources[name]
        if len(data) > 6_000_000:
            raise SystemExit(f"[!] {name} exceeds expected size bound")
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            raise SystemExit(f"[!] {name} SHA-256 mismatch: {actual}")
        (args.output / name).write_bytes(data)
        print(f"[+] {name}: {actual}")


if __name__ == "__main__":
    main()
