#!/var/jb/bin/sh
# Re-sign only non-ad-hoc, regular TweakInject dylibs after package changes.
# The dpkg post-invoke hook must not fail an otherwise successful install.
PATH=/var/jb/usr/bin:/var/jb/bin:/usr/bin:/bin
dir=/var/jb/usr/lib/TweakInject
list=0
[ "${1:-}" = --list ] && list=1
if ! command -v ldid >/dev/null 2>&1; then
    echo 'xra1n-tweaksign: ldid unavailable' >&2
    [ "$list" = 1 ] && exit 2
    exit 0
fi
needs_signing() {
    lines=$(ldid -h "$1" 2>/dev/null | grep CodeDirectory)
    [ -z "$lines" ] || printf '%s\n' "$lines" | grep -qv adhoc
}
for dylib in "$dir"/*.dylib; do
    [ -f "$dylib" ] && [ ! -L "$dylib" ] || continue
    needs_signing "$dylib" || continue
    [ "$list" = 1 ] && { printf '%s\n' "${dylib##*/}"; continue; }
    tmp="$dylib.xra1n-sign"
    xml="$tmp.xml"
    rm -f "$tmp" "$xml"
    cp -p "$dylib" "$tmp" || continue
    ident=$(ldid -h "$dylib" 2>/dev/null | sed -n 's/^Identifier=//p' | sed -n '1p')
    [ -n "$ident" ] || ident=${dylib##*/}
    ents=$(ldid -e "$dylib" 2>/dev/null)
    if [ -n "$ents" ]; then
        printf '%s\n' "$ents" > "$xml"
        ldid -S"$xml" -Cadhoc -I"$ident" "$tmp" >/dev/null 2>&1
        rc=$?
        rm -f "$xml"
    else
        ldid -S -Cadhoc -I"$ident" "$tmp" >/dev/null 2>&1
        rc=$?
    fi
    if [ "$rc" -eq 0 ] && ! needs_signing "$tmp"; then
        mv -f "$tmp" "$dylib" && echo "xra1n-tweaksign: signed ${dylib##*/}"
    else
        rm -f "$tmp"
        echo "xra1n-tweaksign: could not sign ${dylib##*/}" >&2
    fi
done
exit 0
