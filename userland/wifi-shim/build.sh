#!/bin/sh
set -eu

BASE="$(cd "$(dirname "$0")" && pwd)"
SDK="$(xcrun --sdk iphoneos --show-sdk-path)"
LDID="$(command -v ldid || true)"
[ -n "$LDID" ] || { echo '[!] ldid is required' >&2; exit 1; }
OUT="$BASE/wifi-keychain-shim.dylib"

xcrun --sdk iphoneos clang -target arm64e-apple-ios15.0 \
  -isysroot "$SDK" -Os -Wall -Wextra -Werror -fvisibility=hidden \
  -dynamiclib -Wl,-not_for_dyld_shared_cache -Wl,-no_warn_inits \
  -Wl,-install_name,/var/jb/usr/lib/wifi-keychain-shim.dylib \
  "$BASE/shim_common.c" "$BASE/wifi_keychain_shim.c" \
  -framework CoreFoundation -framework Security \
  -o "$OUT"

"$LDID" -S -Cadhoc -Icom.xra1n.wifi-keychain-shim "$OUT"
file "$OUT" | grep -q 'arm64e'
otool -l "$OUT" | grep -q '__interpose'
[ -z "$("$LDID" -e "$OUT")" ]
echo "[+] built $OUT"
