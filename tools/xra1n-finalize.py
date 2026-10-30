#!/usr/bin/env python3
"""Finish XRa1n's Procursus/ElleKit setup on a *normally booted* XR.

Default --check is read-only. --apply makes only verified, idempotent changes.
This does not enable lhook, respring, or rebuild the whole application database.
It does restore the pinned system Sileo registration after Procursus installs
its dependency-satisfying rootless Sileo package.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from install_sshrd_userland import SILEO_BINARY_SHA256, ensure_install_bundle  # noqa: E402

SSH = ROOT / "tools/ssh-normal.sh"
SIGNER = ROOT / "tools/xra1n-tweaksign.sh"
PREP_SHA = "0a5ed93e37c1623a4d3f2067a53d434b04408a8d446209e3b9301d7152de01b7"
DEB_SHA = "e21dc91bdc1be193dc915daecfff45256239b4fe583a36861e662ab1f43906dc"
DEB_PATH = "/var/jb/var/root/ellekit_1.2_iphoneos-arm64.deb"
PREF_DEB = ROOT / "install-bundle/iphone11,8-22H374/finalize/preferenceloader_2.3.3_iphoneos-arm64.deb"
PREF_DEB_SHA = "eaacb5204e13dee166cc44763566bbbd565fbf3cf1ecc25b1a2f432b6bee6f86"
PREF_PAYLOAD_SHA = "b873329f3293916e167ef0783f6052d6cbd27a4a20ccfa201889031eed621987"
PREF_SIGNED_SHA = "42e5680ca80649d1c1624c3c3a75cbdefe0d1bf5b9424db05de55a0b8cdc77a5"
PREF_PLIST_SHA = "0c4adfdb2c5b035f7577103263af7fecdb389887ca92f4b56d76d271352adde9"
NEWTERM_LOGIN = ROOT / "install-bundle/iphone11,8-22H374/apps/newterm/newterm-login"
NEWTERM_HELPER = ROOT / "install-bundle/iphone11,8-22H374/apps/newterm/newterm-helper"
NEWTERM_LOGIN_SHA = "3bf97fca5d6d337fb96f706ff87eaee01e8c2b90f28a41c1d65873578f860767"
NEWTERM_HELPER_SHA = "aac62f50d8aa5ddbbee15cd0274d1907ec1e68af320b133fbf2b135e545f26a6"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="read-only status (default)")
    mode.add_argument("--apply", action="store_true", help="complete missing steps")
    parser.add_argument(
        "--newterm-root",
        action="store_true",
        help="opt-in: install/verify NewTerm root-login appfix (needs NewTerm on device)",
    )
    args = parser.parse_args()
    ensure_install_bundle()
    signer = SIGNER.read_bytes()
    signer_sha = hashlib.sha256(signer).hexdigest()
    pref_deb = PREF_DEB.read_bytes()
    if hashlib.sha256(pref_deb).hexdigest() != PREF_DEB_SHA:
        raise RuntimeError("PreferenceLoader deb hash mismatch")
    cfg = b"post-invoke=/var/jb/usr/libexec/xra1n/tweaksign\n"
    cfg_sha = hashlib.sha256(cfg).hexdigest()
    payload = base64.b64encode(signer).decode()
    config = base64.b64encode(cfg).decode()
    pref_payload = base64.b64encode(pref_deb).decode()
    newterm_requested = 1 if args.newterm_root else 0
    newterm_login_data = ""
    newterm_helper_data = ""
    if args.newterm_root:
        login_bytes = NEWTERM_LOGIN.read_bytes()
        helper_bytes = NEWTERM_HELPER.read_bytes()
        if hashlib.sha256(login_bytes).hexdigest() != NEWTERM_LOGIN_SHA:
            raise RuntimeError("NewTerm login adapter hash mismatch in bundle")
        if hashlib.sha256(helper_bytes).hexdigest() != NEWTERM_HELPER_SHA:
            raise RuntimeError("NewTerm login helper hash mismatch in bundle")
        newterm_login_data = base64.b64encode(login_bytes).decode()
        newterm_helper_data = base64.b64encode(helper_bytes).decode()
    # One SSH session: the USB proxy and pairing are owned by ssh-normal.sh.
    script = f'''#!/bin/sh
set -eu
export PATH=/var/jb/usr/bin:/var/jb/bin:/var/jb/usr/sbin:/var/jb/sbin:/usr/bin:/bin:/usr/sbin:/sbin
mode={"apply" if args.apply else "check"}
incomplete=0
prep_sha={PREP_SHA}
deb_sha={DEB_SHA}
deb={DEB_PATH}
pref_deb=/var/jb/var/root/preferenceloader_2.3.3_iphoneos-arm64.deb
pref_deb_sha={PREF_DEB_SHA}
pref_payload_sha={PREF_PAYLOAD_SHA}
pref_signed_sha={PREF_SIGNED_SHA}
pref_plist_sha={PREF_PLIST_SHA}
sileo_sha={SILEO_BINARY_SHA256}
pref_data='{pref_payload}'
pref_source=/var/jb/Library/MobileSubstrate/DynamicLibraries
pref_target=/var/jb/usr/lib/TweakInject
signer_sha={signer_sha}
cfg_sha={cfg_sha}
signer_data='{payload}'
cfg_data='{config}'
signer=/var/jb/usr/libexec/xra1n/tweaksign
cfg=/var/jb/etc/dpkg/dpkg.cfg.d/xra1n-tweaksign
newterm_requested={newterm_requested}
newterm_login_sha={NEWTERM_LOGIN_SHA}
newterm_helper_sha={NEWTERM_HELPER_SHA}
newterm_login_data='{newterm_login_data}'
newterm_helper_data='{newterm_helper_data}'
newterm_login=/var/jb/usr/bin/login
newterm_helper=/var/jb/Applications/NewTerm.app/NewTermLoginHelper
newterm_login_backup=/var/jb/usr/bin/login.liter8-real
newterm_helper_backup=/var/jb/Applications/NewTerm.app/NewTermLoginHelper.liter8-real
loader=/var/jb/etc/rc.d/ellekit-loader
diverted=/var/jb/etc/rc.d/ellekit-loader.xra1n-disabled
legacy_diverted=/var/jb/etc/rc.d/ellekit-loader.liter8-disabled
fail() {{ echo "[!] $*" >&2; exit 1; }}
sha() {{ sha256sum "$1" | cut -d ' ' -f1; }}
if mount | grep -q 'md0 on /'; then fail 'SSHRD detected; use normal boot'; fi
[ -d /var/jb ] && [ -x /var/jb/bin/sh ] || fail 'Procursus is not mounted'
echo '[+] normal boot and Procursus mount confirmed'

prep_complete() {{
    [ ! -e /var/jb/prep_bootstrap.sh ] &&
    [ -s /var/jb/etc/pwd.db ] && [ -s /var/jb/etc/spwd.db ] &&
    grep -q '^root:.*:/var/jb/usr/bin/zsh$' /var/jb/etc/master.passwd &&
    grep -q '^mobile:.*:/var/jb/usr/bin/zsh$' /var/jb/etc/master.passwd
}}
if prep_complete; then
    echo '[=] Procursus prep already complete'
elif [ "$mode" = check ]; then
    echo '[?] Procursus prep incomplete'
    incomplete=1
else
    prep=/var/jb/prep_bootstrap.sh
    [ -f "$prep" ] && [ ! -L "$prep" ] || fail 'prep missing or symlink; refusing'
    [ "$(sha "$prep")" = "$prep_sha" ] || fail 'prep SHA mismatch; refusing'
    retry=/var/jb/prep_bootstrap.sh.xra1n-retry
    if [ -e "$retry" ]; then
        [ "$(sha "$retry")" = "$prep_sha" ] || fail 'unknown prep retry copy'
    else
        cp -p "$prep" "$retry"
    fi
    # Procursus deletes prep itself. Preserve the verified retry until postchecks pass.
    if ! NO_PASSWORD_PROMPT=1 /var/jb/bin/sh "$prep"; then
        [ -e "$prep" ] || cp -p "$retry" "$prep"
        fail 'Procursus prep failed; verified retry retained'
    fi
    if ! prep_complete; then
        [ -e "$prep" ] || cp -p "$retry" "$prep"
        fail 'Procursus postchecks failed; verified retry retained'
    fi
    rm -f "$retry"
    echo '[+] Procursus prep complete'
fi

state=$(dpkg-query -W -f='${{Version}} ${{Status}}' ellekit 2>/dev/null || true)
case "$state" in
    '1.2 install ok installed') echo '[=] ElleKit 1.2 registered' ;;
    '')
        if [ "$mode" = check ]; then
            echo '[?] ElleKit 1.2 not registered'
            incomplete=1
        else
            [ -f "$deb" ] && [ ! -L "$deb" ] || fail 'staged ElleKit deb absent'
            [ "$(sha "$deb")" = "$deb_sha" ] || fail 'staged ElleKit SHA mismatch'
            dpkg -i "$deb" || true
            state=$(dpkg-query -W -f='${{Version}} ${{Status}}' ellekit 2>/dev/null || true)
            [ "$state" = '1.2 install ok installed' ] || fail "ElleKit install incomplete: $state"
            echo '[+] ElleKit 1.2 registered'
        fi ;;
    *) fail "unexpected ElleKit package state: $state" ;;
esac

if [ "$state" = '1.2 install ok installed' ]; then
    [ -L /var/jb/usr/lib/TweakLoader.dylib ] || fail 'ElleKit TweakLoader link absent'
    [ "$(readlink /var/jb/usr/lib/TweakLoader.dylib)" = /var/jb/usr/lib/ellekit/libinjector.dylib ] || fail 'ElleKit TweakLoader points elsewhere'
    [ -s /var/jb/usr/lib/ellekit/libinjector.dylib ] || fail 'ElleKit injector absent'
    if dpkg-divert --list "$loader" 2>/dev/null | grep -Fq "$diverted"; then
        [ ! -e "$loader" ] || fail 'competing ElleKit loader still present'
        echo '[=] ElleKit loader diversion present'
    elif dpkg-divert --list "$loader" 2>/dev/null | grep -Fq "$legacy_diverted"; then
        [ ! -e "$loader" ] || fail 'competing ElleKit loader still present'
        echo '[=] compatible existing ElleKit loader diversion present'
    elif [ "$mode" = check ]; then
        echo '[?] ElleKit loader diversion absent'
        incomplete=1
    else
        [ ! -e "$diverted" ] || fail 'unknown file at diversion destination'
        dpkg-divert --divert "$diverted" --rename --add "$loader"
        [ ! -e "$loader" ] || fail 'competing ElleKit loader remains'
        echo '[+] ElleKit loader diverted'
    fi
fi

# Sileo uses the same dpkg database. Repair only an actually broken dependency
# state, and inspect APT's plan before authorizing it to change packages.
if apt-get check >/dev/null 2>&1; then
    echo '[=] APT dependencies healthy'
elif [ "$mode" = check ]; then
    echo '[?] APT dependencies broken; a guarded fix is needed'
    incomplete=1
else
    # A freshly staged bootstrap can have package lists that predate the
    # package-manager and libkrw provider packages required by `essential`.
    # Refresh only when repair is actually needed; a healthy finalization
    # remains fully idempotent and does not contact repositories.
    apt-get update || fail 'APT package-list refresh failed'
    echo '[+] APT package lists refreshed'
    plan=$(apt-get -s -f install) || fail 'APT cannot simulate dependency repair'
    if printf '%s\n' "$plan" | grep -q '^Remv '; then
        fail 'APT repair would remove packages; refusing automatic repair'
    fi
    # The staged bootstrap does not include awk yet; count simulated installs
    # with grep, including its exit-1/no-match case under set -e.
    installs=$(printf '%s\n' "$plan" | grep -c '^Inst ' || true)
    [ "$installs" -le 20 ] || fail "APT repair would change $installs packages; refusing"
    export DEBIAN_FRONTEND=noninteractive
    apt-get -y -f install || fail 'APT dependency repair failed'
    apt-get check >/dev/null 2>&1 || fail 'APT dependencies remain broken after repair'
    echo '[+] APT dependencies repaired'
fi

hook_ready=0
if [ -f "$signer" ] && [ ! -L "$signer" ] && [ -f "$cfg" ] && [ ! -L "$cfg" ]; then
    [ "$(sha "$signer")" = "$signer_sha" ] && [ "$(sha "$cfg")" = "$cfg_sha" ] && hook_ready=1
fi
if [ "$hook_ready" = 1 ]; then
    echo '[=] XRa1n tweak re-sign hook installed'
elif [ "$mode" = check ]; then
    echo '[?] XRa1n tweak re-sign hook absent or changed'
    incomplete=1
else
    for target in "$signer" "$cfg"; do
        [ ! -L "$target" ] || fail "refusing symlink target $target"
        [ ! -e "$target" ] || fail "unknown existing hook file $target"
    done
    command -v base64 >/dev/null || fail 'base64 missing'
    mkdir -p /var/jb/usr/libexec/xra1n /var/jb/etc/dpkg/dpkg.cfg.d
    printf '%s' "$signer_data" | base64 -d > "$signer"
    printf '%s' "$cfg_data" | base64 -d > "$cfg"
    chmod 0755 "$signer"
    chmod 0644 "$cfg"
    chown 0:0 "$signer" "$cfg"
    [ "$(sha "$signer")" = "$signer_sha" ] && [ "$(sha "$cfg")" = "$cfg_sha" ] || fail 'hook readback mismatch'
    echo '[+] XRa1n tweak re-sign hook installed'
fi

if ! command -v ldid >/dev/null 2>&1; then
    if [ "$mode" = check ]; then
        echo '[?] ldid is not installed'
        incomplete=1
    else
        export DEBIAN_FRONTEND=noninteractive
        apt-get -y --no-install-recommends install ldid=2.1.5-procursus7 \
            || fail 'apt could not install the pinned Procursus ldid package'
        command -v ldid >/dev/null 2>&1 || fail 'ldid package installed without a usable binary'
        state=$(dpkg-query -W -f='${{Version}} ${{Status}}' ldid 2>/dev/null || true)
        [ "$state" = '2.1.5-procursus7 install ok installed' ] \
            || fail "unexpected ldid package state: $state"
        echo '[+] Procursus ldid 2.1.5-procursus7 installed'
    fi
else
    echo '[=] ldid already available'
fi

if command -v ldid >/dev/null 2>&1; then
    if [ -f "$signer" ]; then
        pending=$(/var/jb/bin/sh "$signer" --list) || fail 'could not inspect tweak signatures'
        if [ -n "$pending" ]; then
            if [ "$mode" = apply ]; then
                /var/jb/bin/sh "$signer"
                [ -z "$(/var/jb/bin/sh "$signer" --list)" ] || fail 'some tweaks remain unsigned'
                echo '[+] pending tweak dylibs signed'
            else
                echo "[?] tweaks awaiting ad-hoc signing: $pending"
                incomplete=1
            fi
        fi
    fi
else
    echo '[=] tweak signatures not checked because ldid is absent'
fi

# PreferenceLoader's package installs to MobileSubstrate's directory. Our
# session-guarded ElleKit loader scans TweakInject instead, so keep a verified
# copy there without enabling injection or restarting Preferences.
pref_state=$(dpkg-query -W -f='${{Version}} ${{Status}}' preferenceloader 2>/dev/null || true)
case "$pref_state" in
    '2.3.3 install ok installed') echo '[=] PreferenceLoader 2.3.3 registered' ;;
    ''|'2.3.3 deinstall ok config-files')
        if [ "$mode" = check ]; then
            echo '[?] PreferenceLoader 2.3.3 not installed'
            incomplete=1
        else
            if [ -e "$pref_deb" ]; then
                [ -f "$pref_deb" ] && [ ! -L "$pref_deb" ] || fail 'PreferenceLoader deb path is unsafe'
                [ "$(sha "$pref_deb")" = "$pref_deb_sha" ] || fail 'unknown staged PreferenceLoader deb'
            else
                command -v base64 >/dev/null || fail 'base64 missing for PreferenceLoader staging'
                printf '%s' "$pref_data" | base64 -d > "$pref_deb"
                chmod 0644 "$pref_deb"
                [ "$(sha "$pref_deb")" = "$pref_deb_sha" ] || fail 'PreferenceLoader staging hash mismatch'
            fi
            dpkg -i "$pref_deb" || fail 'PreferenceLoader package install failed'
            pref_state=$(dpkg-query -W -f='${{Version}} ${{Status}}' preferenceloader 2>/dev/null || true)
            [ "$pref_state" = '2.3.3 install ok installed' ] || fail "unexpected PreferenceLoader package state: $pref_state"
            echo '[+] PreferenceLoader 2.3.3 installed'
        fi ;;
    *) fail "unexpected PreferenceLoader package state: $pref_state" ;;
esac

if [ "$pref_state" = '2.3.3 install ok installed' ]; then
    pref_src_dylib="$pref_source/PreferenceLoader.dylib"
    pref_src_plist="$pref_source/PreferenceLoader.plist"
    pref_dst_dylib="$pref_target/PreferenceLoader.dylib"
    pref_dst_plist="$pref_target/PreferenceLoader.plist"
    [ -f "$pref_src_dylib" ] && [ ! -L "$pref_src_dylib" ] || fail 'PreferenceLoader package dylib absent or unsafe'
    [ -f "$pref_src_plist" ] && [ ! -L "$pref_src_plist" ] || fail 'PreferenceLoader package plist absent or unsafe'
    case "$(sha "$pref_src_dylib")" in
        "$pref_payload_sha"|"$pref_signed_sha") ;;
        *) fail 'PreferenceLoader package dylib hash mismatch' ;;
    esac
    [ "$(sha "$pref_src_plist")" = "$pref_plist_sha" ] || fail 'PreferenceLoader package filter mismatch'
    [ -d "$pref_target" ] && [ ! -L "$pref_target" ] || fail 'ElleKit TweakInject directory absent or unsafe'
    for target in "$pref_dst_dylib" "$pref_dst_plist"; do
        [ ! -L "$target" ] || fail "unsafe PreferenceLoader link: $target"
        [ ! -e "$target" ] || [ -f "$target" ] || fail "non-regular PreferenceLoader target: $target"
    done
    if [ -f "$pref_dst_dylib" ]; then
        case "$(sha "$pref_dst_dylib")" in
            "$pref_payload_sha"|"$pref_signed_sha") ;;
            *) fail 'unknown ElleKit PreferenceLoader dylib' ;;
        esac
    fi
    if [ -f "$pref_dst_plist" ]; then
        [ "$(sha "$pref_dst_plist")" = "$pref_plist_sha" ] || fail 'unknown ElleKit PreferenceLoader filter'
    fi
    if [ ! -f "$pref_dst_dylib" ] || [ ! -f "$pref_dst_plist" ]; then
        if [ "$mode" = check ]; then
            echo '[?] ElleKit PreferenceLoader copy incomplete'
            incomplete=1
        else
            if [ ! -f "$pref_dst_dylib" ]; then
                cp -p "$pref_src_dylib" "$pref_dst_dylib"
                chown 0:0 "$pref_dst_dylib"
                chmod 0755 "$pref_dst_dylib"
            fi
            if [ ! -f "$pref_dst_plist" ]; then
                cp -p "$pref_src_plist" "$pref_dst_plist"
                chown 0:0 "$pref_dst_plist"
                chmod 0644 "$pref_dst_plist"
            fi
        fi
    fi
    if [ -f "$pref_dst_dylib" ] && [ -f "$pref_dst_plist" ]; then
        if [ "$mode" = apply ]; then
            [ -x "$signer" ] && command -v ldid >/dev/null 2>&1 || fail 'tweak signer or ldid unavailable'
            /var/jb/bin/sh "$signer" || fail 'PreferenceLoader signing pass failed'
        fi
        [ "$(sha "$pref_dst_dylib")" = "$pref_signed_sha" ] || {{
            echo '[?] ElleKit PreferenceLoader dylib is not the verified signed build'
            incomplete=1
        }}
        [ "$(sha "$pref_dst_plist")" = "$pref_plist_sha" ] || fail 'PreferenceLoader filter readback mismatch'
        [ "$incomplete" = 0 ] && echo '[=] ElleKit PreferenceLoader copy verified'
    fi
fi

if ! apt-get check >/dev/null 2>&1; then
    if [ "$mode" = check ]; then
        echo '[?] APT dependencies are not healthy after package inspection'
        incomplete=1
    else
        fail 'APT dependencies are not healthy after finalization'
    fi
fi

# Procursus essential requires org.coolstar.sileo in dpkg. APT installs that
# package under /var/jb/Applications, and its trigger registers the stock app
# over our patched /Applications/Sileo.app. Keep the package for dependency
# health, but make the verified system app the launch target again. Targeted
# -p worked on the XR; -u of the rootless path returned an error and is not
# needed. This does not respring or run blanket uicache -a.
sileo=/Applications/Sileo.app/Sileo
[ -f "$sileo" ] && [ ! -L "$sileo" ] || fail 'patched system Sileo absent or unsafe'
[ "$(sha "$sileo")" = "$sileo_sha" ] || fail 'patched system Sileo hash mismatch'
command -v uicache >/dev/null 2>&1 || fail 'uicache is not installed'
sileo_registered() {{
    registration=$(uicache -i org.coolstar.SileoStore 2>/dev/null || true)
    printf '%s\n' "$registration" | grep -Fxq 'Bundle Identifier: org.coolstar.SileoStore' &&
    printf '%s\n' "$registration" | grep -Fxq 'Path: /Applications/Sileo.app'
}}
if sileo_registered; then
    echo '[=] patched system Sileo is registered'
elif [ "$mode" = check ]; then
    echo '[?] Sileo registration points away from patched system app'
    incomplete=1
else
    uicache -p /Applications/Sileo.app || fail 'could not register patched system Sileo'
    sileo_registered || fail 'Sileo registration readback did not select patched system app'
    echo '[+] patched system Sileo registration restored'
fi
# NewTerm root-login appfix: opt-in, idempotent. Replaces the stock login and
# NewTermLoginHelper with the signed persona-99 spawn adapter and tty helper
# verified in work/xr-runtime-fixes. Stock originals are preserved as .liter8-real
# once; existing backups are never overwritten. If NewTerm is not installed,
# this is a soft skip in check mode and a hard refusal in apply mode.
if [ "$newterm_requested" = 1 ]; then
    # --newterm-root is explicit opt-in, so missing NewTerm is a user error
    # in both check and apply modes. Use the app's Mach-O as the marker; the
    # directory is not sufficient because dpkg can leave an empty NewTerm.app
    # behind after an uninstall.
    newterm_app_binary=/var/jb/Applications/NewTerm.app/NewTerm
    [ -f "$newterm_app_binary" ] && [ ! -L "$newterm_app_binary" ] \
        || fail 'NewTerm is not installed; install it from Sileo first, then re-run with --newterm-root'
    [ -f "$newterm_login" ] && [ ! -L "$newterm_login" ] || fail 'login binary missing or symlink'
    [ -f "$newterm_helper" ] && [ ! -L "$newterm_helper" ] || fail 'NewTermLoginHelper missing or symlink'
    login_installed=0
    helper_installed=0
    [ "$(sha "$newterm_login")" = "$newterm_login_sha" ] && login_installed=1
    [ "$(sha "$newterm_helper")" = "$newterm_helper_sha" ] && helper_installed=1
    if [ "$login_installed" = 1 ] && [ "$helper_installed" = 1 ]; then
        # Backups must exist alongside an installed state so rollback is possible.
        [ -f "$newterm_login_backup" ] && [ ! -L "$newterm_login_backup" ] \
            || fail 'login adapter installed but .liter8-real backup missing'
        [ -f "$newterm_helper_backup" ] && [ ! -L "$newterm_helper_backup" ] \
            || fail 'NewTermLoginHelper installed but .liter8-real backup missing'
        echo '[=] NewTerm root-login appfix installed and verified'
    elif [ "$mode" = check ]; then
        echo '[?] NewTerm root-login appfix not installed or drifted'
        incomplete=1
    else
        command -v base64 >/dev/null || fail 'base64 missing for NewTerm staging'
        [ -n "$newterm_login_data" ] && [ -n "$newterm_helper_data" ] \
            || fail 'NewTerm helper payload is empty; this is an orchestration bug'
        # First-run backup: never overwrite an existing .liter8-real so rollback
        # retains the stock binaries even across repeated applies and upgrades.
        for pair in "$newterm_login:$newterm_login_backup" "$newterm_helper:$newterm_helper_backup"; do
            target=${{pair%%:*}}
            backup=${{pair#*:}}
            if [ ! -e "$backup" ]; then
                [ -f "$target" ] && [ ! -L "$target" ] || fail "refusing to back up unsafe path: $target"
                cp -p "$target" "$backup" || fail "could not back up $target"
                chown 0:0 "$backup"
            else
                [ -f "$backup" ] && [ ! -L "$backup" ] || fail "existing backup is unsafe: $backup"
            fi
        done
        # Stage into a sibling path and only move into place after readback.
        if [ "$login_installed" != 1 ]; then
            staged="$newterm_login.xra1n-new"
            printf '%s' "$newterm_login_data" | base64 -d > "$staged"
            chown 0:0 "$staged"
            chmod 0755 "$staged"
            if [ "$(sha "$staged")" != "$newterm_login_sha" ]; then
                rm -f "$staged"
                fail 'login adapter staging hash mismatch'
            fi
            mv "$staged" "$newterm_login"
        fi
        if [ "$helper_installed" != 1 ]; then
            staged="$newterm_helper.xra1n-new"
            printf '%s' "$newterm_helper_data" | base64 -d > "$staged"
            chown 0:0 "$staged"
            chmod 0755 "$staged"
            if [ "$(sha "$staged")" != "$newterm_helper_sha" ]; then
                rm -f "$staged"
                fail 'NewTermLoginHelper staging hash mismatch'
            fi
            mv "$staged" "$newterm_helper"
        fi
        [ "$(sha "$newterm_login")" = "$newterm_login_sha" ] || fail 'login adapter readback mismatch'
        [ "$(sha "$newterm_helper")" = "$newterm_helper_sha" ] || fail 'NewTermLoginHelper readback mismatch'
        echo '[+] NewTerm root-login appfix installed'
    fi
fi

echo '[+] XRa1n finalization inspection finished; injection state unchanged'
[ "$incomplete" = 0 ] || exit 2
'''
    result = subprocess.run([str(SSH), "/bin/sh -s"], input=script.encode(), check=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
