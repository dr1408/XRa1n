#!/usr/bin/env python3
"""Verify the PAC-aware fishhook source used for the patched ElleKit build."""

from __future__ import annotations

import argparse
from pathlib import Path


REQUIRED = (
    '#define SEG_AUTH_CONST  "__AUTH_CONST"',
    'bool section_needs_auth = !strcmp(section->sectname, "__auth_got");',
    "ptrauth_auth_and_resign(indirect_symbol_bindings[i], "
    "ptrauth_key_process_independent_code, &indirect_symbol_bindings[i], "
    "ptrauth_key_function_pointer, 0)",
    "ptrauth_sign_unauthenticated(indirect_symbol_bindings[i], "
    "ptrauth_key_function_pointer, 0)",
    "ptrauth_auth_and_resign(cur->rebindings[j].replacement, "
    "ptrauth_key_function_pointer, 0, ptrauth_key_process_independent_code, "
    "&indirect_symbol_bindings[i])",
    "ptrauth_strip(cur->rebindings[j].replacement, "
    "ptrauth_key_function_pointer)",
    "strcmp(cur_seg_cmd->segname, SEG_AUTH_CONST) != 0",
)

FORBIDDEN = (
    "ptrauth_strip(replacement, ptrauth_key_process_independent_code)",
    "ptrauth_sign_unauthenticated(stripped, "
    "ptrauth_key_process_independent_code, &indirect_symbol_bindings[i])",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()

    source = args.source.read_text()
    missing = [snippet for snippet in REQUIRED if source.count(snippet) != 1]
    present = [snippet for snippet in FORBIDDEN if snippet in source]
    if missing or present:
        if missing:
            print("missing or duplicated required PAC expressions:")
            for snippet in missing:
                print(f"  {snippet}")
        if present:
            print("obsolete ElleKit PAC expressions remain:")
            for snippet in present:
                print(f"  {snippet}")
        raise SystemExit(1)

    print("ElleKit fishhook PAC source verification passed")


if __name__ == "__main__":
    main()
