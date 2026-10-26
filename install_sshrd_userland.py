#!/usr/bin/env python3
"""Install Procursus, XR userland patches, and Setup overrides over SSHRD SSH.

Default is an offline local-artifact check. --install performs device writes.
Original executables are backed up ONLY on the device's Data volume.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import plistlib
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath

from boot_stage import selected_ticket
from host_tools import configure as configure_host_tools, tool as host_tool

ROOT = Path(__file__).resolve().parent
BUNDLE = ROOT / "install-bundle/iphone11,8-22H374"
BUNDLE_ARCHIVE = ROOT / "dist/install-bundle-iphone11.8-22H374.tar.gz"
BUNDLE_ARCHIVE_SHA256 = "1912c082ac59c7ef10e5883545adc62f30bd3de6686c63dc4f217f685687cb80"
ARCHIVE = BUNDLE / "bootstrap/bootstrap_1900.tar.zst"
ARTIFACTS = BUNDLE / "userland"
USBMUXD = host_tool("usbmuxd")
IPROXY = host_tool("iproxy")
SSHPASS = host_tool("sshpass")
BOOTSTRAP_SHA256 = "8354c3aa1ecdad8ebc47d9a76dfca6f830a2b757278068bd33b98bf1d638a9cb"
SETUP_PATH = "/mnt1/Applications/Setup.app/Setup"
SETUP_PRISTINE_SHA256 = "595a9e839dcdba3dbaa93f74e63627f984ab56537ce6687a1a1c9a633338344d"
SETUP_SIGNED_SHA256 = "68c7b8c1db70763465996ac3e152d8a92cd175e60b46523997caddbe33b8880f"
SETUP_SIGNED = BUNDLE / "setup/Setup.patched.signed"
COMMCENTER_PATH = "/mnt1/System/Library/Frameworks/CoreTelephony.framework/Support/CommCenter"
COMMCENTER_PRISTINE_SHA256 = "77ef543a172aa293c1cbc43829789a8b83acafb468bf558b20b57dc2a7d8c44e"
COMMCENTER_PATCHED_SHA256 = "81f995ac88597c3d9a0e3a18f4536af611b6a9f9f5b7c6dc7b5af3946013870c"
COMMCENTER_ARTIFACT = BUNDLE / "commcenter/CommCenter"
COMMCENTER_RECORDS = BUNDLE / "commcenter/patch-records.json"
GUARDED_HOOK_ROOT = BUNDLE / "tweaks"
JBBOOT_ARTIFACT = BUNDLE / "jbboot/xra1n-jbboot"
JBBOOT_SHA256 = "d914b4757e618b45740030176599716bde9c8e8fbea14b357f93c7c146de0561"
JBBOOT_UPGRADABLE_SHA256 = {
    "c82fa988cd501eb007aab668f20b8f320ab66e052187ebb48896c97915198d5b",
    "8d6f719206c8245b9ce1364fec0877b6503aa5f1c2dc803c7288badda714e1bb",
    "42fd6bca0327fc7759768382e325e4a9564b044fac3d0f9c1be9c4f052a28088",
    "7fcaa73960ba36bacfa3e99eed5e4ef834f737629dc7c3f18a2b5f041287dd30",
}
JBBOOT_PATH = "/mnt1/usr/local/bin/xra1n-jbboot"
SILEO_ARTIFACT = BUNDLE / "apps/Sileo.app.tar.gz"
SILEO_ARCHIVE_SHA256 = "648acf1e4230ca7c962394f2c652519e737b8041b27a4a8d92bb03d60db33b99"
SILEO_BINARY_SHA256 = "b26abe6bd2cb4557dbcc381d2f8300cc21fc84cca1714e7671b5988e0d2e2cc3"
SILEO_HELPER_SHA256 = "b108b9381bba5f0e503bcf8e8c17d8f8947c97529345b98b171fef2bde7e5f2a"
SILEO_PATH = "/mnt1/Applications/Sileo.app"
LAUNCHD_CACHE_PATH = "/mnt1/System/Library/xpc/launchd.plist"
JBBOOT_CACHE_KEY = "/System/Library/LaunchDaemons/com.xra1n.jbboot.plist"
JBBOOT_JOB = {
    "Label": "com.xra1n.jbboot",
    "ProgramArguments": ["/usr/local/bin/xra1n-jbboot"],
    "RunAtLoad": True,
    "KeepAlive": {"SuccessfulExit": False},
    "POSIXSpawnType": "Interactive",
    "EnablePressuredExit": False,
    "EnableTransactions": False,
    "StandardOutPath": "/var/mobile/xra1n-jbboot.log",
    "StandardErrorPath": "/var/mobile/xra1n-jbboot.log",
}
DROPBEAR_CACHE_KEY = "/System/Library/LaunchDaemons/com.dropbear.plist"
DROPBEAR_JOB = {
    "EnablePressuredExit": False, "EnableTransactions": False,
    "KeepAlive": True, "Label": "com.dropbear",
    "POSIXSpawnType": "Interactive",
    "ProgramArguments": ["/usr/local/bin/dropbear", "-F", "-r",
        "/private/var/dropbear/dropbear_rsa_host_key", "-r",
        "/private/var/dropbear/dropbear_ecdsa_host_key", "-r",
        "/private/var/dropbear/dropbear_dss_host_key", "-E", "-p", "22"],
    "RunAtLoad": True, "StandardErrorPath": "/var/mobile/dropbear.log",
    "StandardOutPath": "/var/mobile/dropbear.log", "ThrottleInterval": 5,
}
NORMAL_SSH = BUNDLE / "ssh"
NORMAL_SSH_FILES = {
    "dropbear": (NORMAL_SSH / "bin/dropbear", "89e789cd3ca755daa0cbc9b52a10f2373b021460a7c5a0cbd7a3a705126c5553"),
    "dropbearkey": (NORMAL_SSH / "bin/dropbearkey", "3c5747c3fce3e226e6fff132a305c67b948b83e41d8143de00e17087df9e6b67"),
    "sh": (NORMAL_SSH / "bin/sh", "d6baf3862004da95510b4d831e5447e699daa99338e6fcb2f2c44267a290a403"),
    "ls": (NORMAL_SSH / "bin/ls", "84575bd31e2918494d60787dcdcf4a2b6c95837f433da95148717f288f1fa831"),
    "cat": (NORMAL_SSH / "bin/cat", "139b84461327f3c2ec6caea9baf7e4bcb773133741427a551b605924efb8abbe"),
}
PAIR_ROOT = BUNDLE / "pairing"
PAIR_FILES = {
    "pair-lockdownd": (ARTIFACTS / "pairing/lockdownd", "747124d325ddac9d81b5722603b5cb361e57582dfc08cb1f9631efb3f1ee4c2d"),
    "pair-coreauthd": (PAIR_ROOT / "coreauthd.xra1n", "02de51020a8a9d0509983088226cfc59bc4db04c73479a7eb14fbc8ed1f5ca1e"),
    "xra1n-pair.dylib": (PAIR_ROOT / "xra1n-pair.dylib", "8f05b10601a2416c78fbf0f95d2a3b3e34c691cc3ee6647ed1a909041791bf1b"),
    "xra1n-coreauth.dylib": (PAIR_ROOT / "xra1n-coreauth.dylib", "9699c1e76949e29e3c0db5928685201b46b0c1064a416951031ca892f6f74e63"),
}
LAUNCHD_HOOK_FILES = {
    "launchd": (BUNDLE / "launchd/launchd", "641583bb8eb5f274bc31a502552bcb3b46990ffaeb0892c053f25ec583450807"),
    "lhook": (GUARDED_HOOK_ROOT / "lhook", "6d3c2ca94a15045ffe6c878f384cb504a58238c6fecde474436bf24fe50e03c7"),
}
TWEAK_STACK_FILES = {
    "lhookctl": (GUARDED_HOOK_ROOT / "lhookctl", "0d02efad8d27b451e0a1b653c0697a5587146e4470b135252daae3131a8499e0"),
    "lhook.deny": (GUARDED_HOOK_ROOT / "lhook.deny", "a37ebebdd424eb139cc565471b4e3f71b1af44cf9dac7f0898d96418ba8aba1a"),
    "libinjector.dylib": (GUARDED_HOOK_ROOT / "ellekit/libinjector.dylib", "0ffc5cf9953cbe74e91bae84967ac26834d67fba3c3426e407235a477335d6a9"),
    "MobileSafety.dylib": (GUARDED_HOOK_ROOT / "ellekit/MobileSafety.dylib", "9af9aa65dd32fb30dd47930e56080411fe39579cc5b04dc1c67959ee9467b401"),
    "ELLEKIT-LICENSE": (GUARDED_HOOK_ROOT / "ellekit/LICENSE", "ac3842df55783a43c093806730c480cecdd3c011dfb7fd001ec1ed5258fb0c08"),
    "ellekit.deb": (GUARDED_HOOK_ROOT / "ellekit/ellekit_1.2_iphoneos-arm64.deb", "e21dc91bdc1be193dc915daecfff45256239b4fe583a36861e662ab1f43906dc"),
}
XR_RUNTIME_FILES = {
    "pfwatch": (BUNDLE / "runtime/pfwatch",
                "b4e460dd2aa6af4b181f1606bb00cb6a3162fef3da9b763727d34ba3cd81b822",
                "/mnt1/usr/local/bin/pfwatch", 0o755),
    "pfruntimeprobe": (BUNDLE / "runtime/pfruntimeprobe",
                       "f26002769dd753e37604de6b63caa388a812de9a4dd856382beb66eb0e86912b",
                       "/mnt1/usr/local/bin/pfruntimeprobe", 0o755),
    "l8persona.dylib": (BUNDLE / "runtime/l8persona.dylib",
                        "27e8609f31d3e96ce45c1abbdd5f1921a451bb31799d71f8e22ca8e52f98755f",
                        "/mnt2/jb/usr/lib/TweakInject/l8persona.dylib", 0o755),
    "l8persona.plist": (BUNDLE / "runtime/l8persona.plist",
                        "4250dc6ed4935a8a7105e7d1f35adf75008a288f5898e1528ec1225101684173",
                        "/mnt2/jb/usr/lib/TweakInject/l8persona.plist", 0o644),
    "l8localauth.dylib": (BUNDLE / "runtime/l8localauth.dylib",
                          "a208623578cce5c12041200a736949c7bc1dfcf782209835515993157d658113",
                          "/mnt2/jb/usr/lib/TweakInject/l8localauth.dylib", 0o755),
    "l8localauth.plist": (BUNDLE / "runtime/l8localauth.plist",
                          "d77ac9bd85af38d8e01174496dec7ee9fc97f61fe57eb7b387f5ca13d7fb0d0e",
                          "/mnt2/jb/usr/lib/TweakInject/l8localauth.plist", 0o644),
    "l8files.dylib": (BUNDLE / "runtime/l8files.dylib",
                       "f97a68c9c376bb4c214c79aa64dc376591e9a2d7c17e455f326bdfa525c6136e",
                       "/mnt2/jb/usr/lib/TweakInject/l8files.dylib", 0o755),
    "l8files.plist": (BUNDLE / "runtime/l8files.plist",
                       "9d651028ca8675d72938ea255cda9c6c4c0db5ff1466a272e6f64d07b7eecf79",
                       "/mnt2/jb/usr/lib/TweakInject/l8files.plist", 0o644),
}
XR_RUNTIME_UPGRADABLE = {
    "pfwatch": {"d020cbe017d51ebb4150e72ea303cf980b506d283b80886ed36e8f3f1a19d770"},
    "pfruntimeprobe": {"0b3288b7a612a57b83235560acfea0e9ef9312357b349c11169397828e454948"},
}
TARGETS = {
    "coreauthd": ("/mnt1/System/Library/Frameworks/LocalAuthentication.framework/Support/coreauthd",
                  "d479cb7f9de2531e85c5af0edd7d91ed9f4a29fea7e192565f7754f1440b22d7"),
    "ctkd": ("/mnt1/System/Library/Frameworks/CryptoTokenKit.framework/ctkd",
             "97c328e8a85248c0b3b0471a92aa0f13bebdc8a7730f1a43014dbeaf214e8657"),
    "mobileactivationd": ("/mnt1/usr/libexec/mobileactivationd",
                          "17076de71a65e1096c74ee87ee6d4460748f2dfc7ffb9760ba4cf8439bfbb26e"),
}
SCREENTIME_DISABLED_PATH = "/mnt2/db/com.apple.xpc.launchd/disabled.plist"
SCREENTIME_LABELS = (
    "com.apple.ScreenTimeAgent",
    "com.apple.ScreenTimeSettingsAgent",
    "com.apple.FamilyControlsAgent",
    "com.apple.familycircled",
    "com.apple.familynotificationd",
)
MOUNT_SCRIPT = r'''
set -eu
export PATH=/usr/bin:/bin:/usr/sbin:/sbin:/usr/libexec:/System/Library/Filesystems/apfs.fs
apfs_util=/System/Library/Filesystems/apfs.fs/apfs.util
volume_for_role() {
    wanted="$1"
    for dev in /dev/disk1s[1-9] /dev/disk0s1s[1-9]; do
        [ -e "$dev" ] || continue
        role=$("$apfs_util" -p "$dev" 2>/dev/null) || true
        [ "$role" = "$wanted" ] && { printf '%s\n' "$dev"; return 0; }
    done
    return 1
}
system=$(volume_for_role System) || { echo 'missing APFS System role' >&2; exit 1; }
data=$(volume_for_role Data) || { echo 'missing APFS Data role' >&2; exit 1; }
preboot=$(volume_for_role Preboot) || { echo 'missing APFS Preboot role' >&2; exit 1; }
for target in /mnt1 /mnt2 /mnt6; do
    [ -d "$target" ] || { echo "missing mountpoint: $target" >&2; exit 1; }
done
if ! /sbin/mount | /usr/bin/grep -q " on /mnt1 "; then
    /sbin/mount_apfs "$system" /mnt1
fi
/sbin/mount -u -o rw "$system"
if ! /sbin/mount | /usr/bin/grep -q " on /mnt2 "; then
    /sbin/mount_apfs "$data" /mnt2
fi
if ! /sbin/mount | /usr/bin/grep -q " on /mnt6 "; then
    /sbin/mount_apfs -o rdonly "$preboot" /mnt6
fi
for target in /mnt1 /mnt2 /mnt6; do
    /sbin/mount | /usr/bin/grep " on $target "
done
testfile=/mnt1/Applications/.xra1n-rw-$$
: > "$testfile" && /bin/rm "$testfile"
printf 'MOUNTS_AND_SYSTEM_RW_OK\n'
'''


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _bundle_manifest(data: bytes) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in data.decode("utf-8").splitlines():
        parts = line.split("  ", 1)
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            raise RuntimeError("invalid install bundle manifest")
        name = parts[1]
        path = PurePosixPath(name)
        if (not name or path.is_absolute() or any(part in (".", "..") for part in name.split("/"))
                or str(path) != name or name in entries or name == "SHA256SUMS"):
            raise RuntimeError(f"invalid install bundle path: {name}")
        entries[name] = parts[0]
    if not entries:
        raise RuntimeError("empty install bundle manifest")
    return entries


def _verify_bundle(directory: Path) -> None:
    manifest = directory / "SHA256SUMS"
    if directory.is_symlink() or not manifest.is_file() or manifest.is_symlink():
        raise RuntimeError(f"invalid install bundle: {directory}")
    if (directory / "ssh/device-key").exists():
        raise RuntimeError("install bundle must not contain SSH client keys")
    for name, expected in _bundle_manifest(manifest.read_bytes()).items():
        path = directory / name
        if not path.is_file() or path.is_symlink() or digest(path.read_bytes()) != expected:
            raise RuntimeError(f"install bundle hash mismatch: {name}")


def ensure_install_bundle() -> None:
    """Restore the local release archive if needed; never download or touch a device."""
    if BUNDLE.exists() or BUNDLE.is_symlink():
        _verify_bundle(BUNDLE)
        return
    if not BUNDLE_ARCHIVE.is_file():
        raise RuntimeError(f"missing install bundle and local archive: {BUNDLE_ARCHIVE}")
    if digest(BUNDLE_ARCHIVE.read_bytes()) != BUNDLE_ARCHIVE_SHA256:
        raise RuntimeError("install bundle archive SHA-256 mismatch")
    BUNDLE.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".xra1n-bundle-", dir=BUNDLE.parent) as temp:
        staged = Path(temp) / BUNDLE.name
        staged.mkdir()
        prefix = f"install-bundle/{BUNDLE.name}/"
        with tarfile.open(BUNDLE_ARCHIVE, "r:gz") as archive:
            members = archive.getmembers()
            by_name = {member.name: member for member in members}
            if len(by_name) != len(members) or any(not member.isfile() for member in members):
                raise RuntimeError("install bundle archive contains duplicate or non-file entries")
            manifest_name = prefix + "SHA256SUMS"
            manifest_member = by_name.get(manifest_name)
            if manifest_member is None:
                raise RuntimeError("install bundle archive has no manifest")
            manifest_file = archive.extractfile(manifest_member)
            if manifest_file is None:
                raise RuntimeError("install bundle manifest is unreadable")
            manifest_data = manifest_file.read()
            entries = _bundle_manifest(manifest_data)
            if any(name.startswith("ssh/device-key/") for name in entries):
                raise RuntimeError("install bundle archive must not contain SSH client keys")
            if set(by_name) != {manifest_name, *(prefix + name for name in entries)}:
                raise RuntimeError("install bundle archive contents differ from manifest")
            (staged / "SHA256SUMS").write_bytes(manifest_data)
            for name, expected in entries.items():
                source = archive.extractfile(by_name[prefix + name])
                if source is None:
                    raise RuntimeError(f"unreadable install bundle entry: {name}")
                destination = staged / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                hash_state = hashlib.sha256()
                with destination.open("wb") as target:
                    while chunk := source.read(1024 * 1024):
                        hash_state.update(chunk)
                        target.write(chunk)
                if hash_state.hexdigest() != expected:
                    raise RuntimeError(f"install bundle hash mismatch: {name}")
        if BUNDLE.exists() or BUNDLE.is_symlink():
            raise RuntimeError(f"install bundle appeared during extraction: {BUNDLE}")
        os.replace(staged, BUNDLE)
    print(f"[+] restored verified install bundle from {BUNDLE_ARCHIVE}", flush=True)


def q(value: str | Path) -> str:
    return shlex.quote(str(value))


def validate_ssh_public_key(data: bytes) -> bytes:
    """Accept one ECDSA P-256 public key for normal-boot SSH access."""
    if len(data.strip().splitlines()) != 1:
        raise RuntimeError("normal SSH public key must occupy one line")
    fields = data.strip().split()
    if len(fields) not in (2, 3) or fields[0] != b"ecdsa-sha2-nistp256":
        raise RuntimeError("normal SSH public key must be one ECDSA P-256 key")
    try:
        decoded = base64.b64decode(fields[1], validate=True)
    except (ValueError, binascii.Error) as error:
        raise RuntimeError("normal SSH public key is not valid base64") from error
    if b"ecdsa-sha2-nistp256" not in decoded[:64]:
        raise RuntimeError("normal SSH public key type does not match its blob")
    return b" ".join(fields) + b"\n"


def normal_ssh_public_key() -> bytes:
    """Use a host-owned key; create it once, never ship it in the bundle."""
    private = Path(os.environ.get("XRA1N_SSH_KEY", "~/.ssh/xra1n-xr")).expanduser()
    public = Path(str(private) + ".pub")
    if not private.exists() and not public.exists():
        private.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        subprocess.run(["ssh-keygen", "-t", "ecdsa", "-b", "256", "-N", "",
                        "-f", str(private), "-C", "xra1n-xr"], check=True)
        print(f"[+] generated host-owned SSH key: {private}", flush=True)
    if not private.is_file() or not public.is_file():
        raise RuntimeError(f"incomplete SSH key pair: {private} and {public}")
    derived = validate_ssh_public_key(subprocess.run(
        ["ssh-keygen", "-y", "-f", str(private)], check=True,
        capture_output=True).stdout)
    chosen = validate_ssh_public_key(public.read_bytes())
    if derived.split()[:2] != chosen.split()[:2]:
        raise RuntimeError(f"SSH public key does not match private key: {public}")
    return chosen


def offline_preflight() -> dict[str, bytes]:
    if digest(ARCHIVE.read_bytes()) != BOOTSTRAP_SHA256:
        raise RuntimeError("Procursus archive hash mismatch")
    sums: dict[str, str] = {}
    for line in (ARTIFACTS / "SHA256SUMS").read_text().splitlines():
        parts = line.split()
        if len(parts) == 2:
            sums[parts[1].removeprefix("./")] = parts[0]
    patches: dict[str, bytes] = {}
    for name, (_, pristine_hash) in TARGETS.items():
        pristine = BUNDLE / "stock" / name
        patched = ARTIFACTS / "daemons" / name
        if digest(pristine.read_bytes()) != pristine_hash:
            raise RuntimeError(f"local pristine {name} hash mismatch")
        data = patched.read_bytes()
        if digest(data) != sums.get(f"daemons/{name}") or digest(data) == pristine_hash:
            raise RuntimeError(f"patched {name} hash mismatch")
        patches[name] = data
    if digest((BUNDLE / "stock/Setup").read_bytes()) != SETUP_PRISTINE_SHA256:
        raise RuntimeError("local pristine Setup hash mismatch")
    setup = SETUP_SIGNED.read_bytes()
    if digest(setup) != SETUP_SIGNED_SHA256:
        raise RuntimeError("signed Setup artifact hash mismatch")
    patches["Setup"] = setup
    commcenter_pristine = (BUNDLE / "stock/CommCenter").read_bytes()
    if digest(commcenter_pristine) != COMMCENTER_PRISTINE_SHA256:
        raise RuntimeError("local pristine CommCenter hash mismatch")
    commcenter = COMMCENTER_ARTIFACT.read_bytes()
    if digest(commcenter) != COMMCENTER_PATCHED_SHA256:
        raise RuntimeError("signed patched CommCenter artifact hash mismatch")
    records = json.loads(COMMCENTER_RECORDS.read_text())
    expected_records = {
        ("commcenter.data-connection.context-index", 0x804268),
        ("commcenter.data-settings.activation-status", 0x631370),
        ("commcenter.data-settings.activate-baseband-state", 0x6348B8),
    }
    if {(record.get("id"), record.get("offset")) for record in records} != expected_records:
        raise RuntimeError("CommCenter patch records mismatch")
    patches["CommCenter"] = commcenter
    jbboot = JBBOOT_ARTIFACT.read_bytes()
    if digest(jbboot) != JBBOOT_SHA256:
        raise RuntimeError("signed xra1n-jbboot artifact hash mismatch")
    for marker in (b"kpersona_alloc", b"kpersona_info",
                   b"xra1n-jbboot: persona 99 created",
                   b"/var/jb/usr/bin/uicache",
                   b"/var/jb/.xra1n-system-apps-registered",
                   b"xra1n-jbboot: system app registration complete"):
        if jbboot.count(marker) != 2:
            raise RuntimeError(f"xra1n-jbboot universal marker mismatch: {marker!r}")
    patches["xra1n-jbboot"] = jbboot
    sileo_archive = SILEO_ARTIFACT.read_bytes()
    if digest(sileo_archive) != SILEO_ARCHIVE_SHA256:
        raise RuntimeError("Liter8-compatible Sileo archive hash mismatch")
    with tarfile.open(SILEO_ARTIFACT, "r:gz") as archive:
        members = {member.name.removeprefix("./"): member for member in archive.getmembers()}
        main_member = members.get("Sileo.app/Sileo")
        helper_member = members.get("Sileo.app/giveMeRoot")
        if main_member is None or helper_member is None:
            raise RuntimeError("Sileo archive is missing its executables")
        main_file = archive.extractfile(main_member)
        helper_file = archive.extractfile(helper_member)
        if main_file is None or helper_file is None:
            raise RuntimeError("Sileo archive executables are not regular files")
        sileo_main = main_file.read()
        sileo_helper = helper_file.read()
        if digest(sileo_main) != SILEO_BINARY_SHA256:
            raise RuntimeError("Sileo executable hash mismatch")
        if digest(sileo_helper) != SILEO_HELPER_SHA256:
            raise RuntimeError("Sileo giveMeRoot hash mismatch")
        if helper_member.mode & 0o7777 != 0o4755:
            raise RuntimeError("Sileo giveMeRoot is not setuid 4755 in the archive")
        for marker in (b"/var/jb/.installed_usbl8r", b"000501:000501"):
            if sileo_main.count(marker) != 1:
                raise RuntimeError(f"Sileo patch marker count mismatch: {marker!r}")
    patches["Sileo.app.tar.gz"] = sileo_archive
    extra_paths = []
    for group in (NORMAL_SSH_FILES, PAIR_FILES, LAUNCHD_HOOK_FILES):
        for name, (path, expected) in group.items():
            data = path.read_bytes()
            if digest(data) != expected:
                raise RuntimeError(f"artifact hash mismatch: {name}")
            patches[name] = data
            extra_paths.append(str(path))
    for name, (path, expected) in TWEAK_STACK_FILES.items():
        data = path.read_bytes()
        if digest(data) != expected:
            raise RuntimeError(f"tweak-stack artifact hash mismatch: {name}")
        patches[name] = data
        if name in {"lhookctl", "libinjector.dylib", "MobileSafety.dylib"}:
            extra_paths.append(str(path))
    for name, (path, expected, _, _) in XR_RUNTIME_FILES.items():
        data = path.read_bytes()
        if digest(data) != expected:
            raise RuntimeError(f"XR runtime artifact hash mismatch: {name}")
        patches[name] = data
        if not name.endswith(".plist"):
            extra_paths.append(str(path))
    expected_filters = {
        "l8persona.plist": ["installd", "installcoordinationd"],
        "l8localauth.plist": ["nehelper"],
        "l8files.plist": ["fileproviderd", "LocalStorageFileProvider"],
    }
    for name, executables in expected_filters.items():
        if plistlib.loads(patches[name]) != {"Filter": {"Executables": executables}}:
            raise RuntimeError(f"XR tweak filter mismatch: {name}")
    subprocess.run([sys.executable, str(ROOT / "verify_userland_signature.py"),
                    *(str(ARTIFACTS / "daemons" / name) for name in TARGETS),
                    str(SETUP_SIGNED), str(COMMCENTER_ARTIFACT),
                    str(JBBOOT_ARTIFACT), *extra_paths], check=True)
    subprocess.run([sys.executable, str(ROOT / "patch_setup_xr.py"),
                    str(SETUP_SIGNED), "--verify", "--expect-count", "58"],
                   check=True, stdout=subprocess.DEVNULL)
    for tool in (USBMUXD, IPROXY, SSHPASS):
        if not tool.is_file():
            raise RuntimeError(f"missing USB/SSH tool: {tool}")
    print("[+] offline Procursus, pairing, guarded lhook/ElleKit, PhotoForce, XR tweaks, SSH, Setup, CommCenter, jbboot and Sileo artifacts verified",
          flush=True)
    return patches


def build_jbboot_cache(source: bytes) -> bytes:
    """Add the proven jbboot and normal-boot Dropbear jobs."""
    try:
        document = plistlib.loads(source)
    except Exception as error:
        raise RuntimeError(f"launchd cache is invalid: {error}") from error
    if not isinstance(document, dict) or document.get("VersionNumber") != 7:
        raise RuntimeError("launchd cache is not the expected Version 7 dictionary")
    jobs = document.get("LaunchDaemons")
    if not isinstance(jobs, dict):
        raise RuntimeError("launchd cache has no LaunchDaemons dictionary")
    current = jobs.get(JBBOOT_CACHE_KEY)
    if current is not None and current != JBBOOT_JOB:
        raise RuntimeError("launchd cache contains a conflicting xra1n-jbboot job")
    jobs[JBBOOT_CACHE_KEY] = dict(JBBOOT_JOB)
    dropbear = jobs.get(DROPBEAR_CACHE_KEY)
    if dropbear is not None and dropbear != DROPBEAR_JOB:
        raise RuntimeError("launchd cache contains a conflicting Dropbear job")
    jobs[DROPBEAR_CACHE_KEY] = dict(DROPBEAR_JOB)
    patched = plistlib.dumps(document, fmt=plistlib.FMT_BINARY, sort_keys=False)
    verified = plistlib.loads(patched)
    if verified["LaunchDaemons"].get(JBBOOT_CACHE_KEY) != JBBOOT_JOB:
        raise RuntimeError("constructed launchd cache failed jbboot verification")
    if verified["LaunchDaemons"].get(DROPBEAR_CACHE_KEY) != DROPBEAR_JOB:
        raise RuntimeError("constructed launchd cache failed Dropbear verification")
    return patched


class USBSSH:
    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self.device_id: str | None = None
        self.usbmuxd: subprocess.Popen | None = None
        self.iproxy: subprocess.Popen | None = None
        self.usb_log = None
        self.proxy_log = None
        self.env = os.environ.copy()
        self.env["SSHPASS"] = "alpine"

    def start(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        for name in ("idevicesyslog", "iproxy"):
            subprocess.run(["pkill", "-x", name], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        self.usb_log = (self.log_dir / "usbmuxd.log").open("wb")
        for attempt in range(1, 4):
            subprocess.run([str(USBMUXD), "--force-exit"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            time.sleep(0.5)
            self.usbmuxd = subprocess.Popen([str(USBMUXD), "-pf"],
                                             stdout=self.usb_log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and self.usbmuxd.poll() is None:
                try:
                    result = subprocess.run(["idevice_id", "-l"], capture_output=True,
                                            text=True, timeout=3)
                except subprocess.TimeoutExpired:
                    continue
                if result.returncode == 0:
                    ids = result.stdout.splitlines()
                    self.device_id = next((value for value in ids if value.startswith("SSHRD_Script")), None)
                    if self.device_id is not None:
                        break
                time.sleep(0.5)
            else:
                if self.usbmuxd.poll() is None:
                    self.usbmuxd.terminate()
                    self.usbmuxd.wait(timeout=3)
                print(f"[*] usbmuxd/UDID retry {attempt}/3", flush=True)
                continue
            break
        else:
            raise RuntimeError("SSHRD_Script USB device not visible to usbmuxd; check lsusb")

        self.proxy_log = (self.log_dir / "iproxy.log").open("wb")
        self.iproxy = subprocess.Popen([str(IPROXY), "-u", self.device_id, "2222:22"],
                                       stdout=self.proxy_log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and self.iproxy.poll() is None:
            try:
                if self.call("id -u", timeout=8).stdout.strip() == b"0":
                    print("[+] SSHRD root SSH ready on localhost:2222", flush=True)
                    return
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                time.sleep(1)
        raise RuntimeError(f"ramdisk root SSH unavailable; inspect {self.log_dir / 'iproxy.log'}")

    def call(self, command: str, *, input_data: bytes | None = None,
             timeout: float = 90) -> subprocess.CompletedProcess:
        argv = [str(SSHPASS), "-e", "ssh", "-F", "/dev/null", "-T",
                "-o", "Tunnel=no", "-o", "ConnectTimeout=8",
                "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=6",
                "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                "-o", "LogLevel=ERROR", "-p", "2222", "root@localhost", command]
        return subprocess.run(argv, input=input_data, capture_output=True,
                              timeout=timeout, env=self.env, check=True)

    def script(self, script: str) -> str:
        try:
            result = self.call("/bin/sh -s", input_data=script.encode())
        except subprocess.CalledProcessError as error:
            stdout = error.stdout.decode("utf-8", "replace").strip()
            stderr = error.stderr.decode("utf-8", "replace").strip()
            raise RuntimeError(
                f"remote shell failed ({error.returncode}); "
                f"stdout={stdout!r}; stderr={stderr!r}"
            ) from error
        return result.stdout.decode("utf-8", "replace")

    def read(self, path: str) -> bytes:
        return self.call(f"/bin/cat {q(path)}").stdout

    def close(self) -> None:
        if self.iproxy is not None and self.iproxy.poll() is None:
            self.iproxy.terminate()
        if self.usbmuxd is not None and self.usbmuxd.poll() is None:
            self.usbmuxd.terminate()
        for process in (self.iproxy, self.usbmuxd):
            if process is not None:
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
        for stream in (self.proxy_log, self.usb_log):
            if stream is not None:
                stream.close()


def unpack_bootstrap(directory: Path) -> Path:
    decoder = subprocess.Popen(["zstd", "-dc", str(ARCHIVE)], stdout=subprocess.PIPE)
    try:
        subprocess.run(["tar", "-xf", "-", "-C", str(directory)],
                       stdin=decoder.stdout, check=True)
    finally:
        assert decoder.stdout is not None
        decoder.stdout.close()
    if decoder.wait() != 0:
        raise RuntimeError("Procursus zstd decompression failed")
    root = directory / "var/jb"
    for path in ("bin", "usr", "prep_bootstrap.sh"):
        if not (root / path).exists():
            raise RuntimeError(f"Procursus archive missing var/jb/{path}")
    return root


def stream_tree(usb: USBSSH, source: Path, destination: str) -> None:
    tar = subprocess.Popen(["tar", "-C", str(source), "-cpf", "-", "."],
                           stdout=subprocess.PIPE)
    assert tar.stdout is not None
    argv = [str(SSHPASS), "-e", "ssh", "-F", "/dev/null", "-T",
            "-o", "Tunnel=no", "-o", "ConnectTimeout=8",
            "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR", "-p", "2222", "root@localhost",
            f"/usr/bin/tar -xpf - -C {q(destination)}"]
    result = subprocess.run(argv, stdin=tar.stdout, capture_output=True,
                            env=usb.env, timeout=300)
    tar.stdout.close()
    if tar.wait() != 0 or result.returncode != 0:
        raise RuntimeError(f"Procursus tar transfer failed: {result.stderr.decode(errors='replace')}")


def install_bootstrap(usb: USBSSH, ticket_id: str) -> None:
    target = "/mnt2/jb"
    stage = f"/mnt2/xra1n-stage/{ticket_id}/bootstrap"
    marker = f"{target}/.xra1n-bootstrap-sha256"
    state = usb.script(f"""
set -eu
if [ -L {q(target)} ]; then echo SYMLINK; exit 0; fi
if [ -d {q(target)} ]; then
  if [ -f {q(marker)} ]; then /bin/cat {q(marker)}; exit 0; fi
  if [ -z \"$(/bin/ls -A {q(target)})\" ]; then echo EMPTY; exit 0; fi
  echo UNMANAGED; exit 0
fi
echo ABSENT
""").strip()
    if state == BOOTSTRAP_SHA256:
        print("[+] matching Procursus tree already present; skipping", flush=True)
        return
    if state not in {"EMPTY", "ABSENT"}:
        raise RuntimeError(f"refusing to overwrite existing /mnt2/jb ({state})")
    usb.script(f"/bin/mkdir -p {q(stage)}")
    with tempfile.TemporaryDirectory(prefix="xra1n-procursus-") as temporary:
        source = unpack_bootstrap(Path(temporary))
        stream_tree(usb, source, stage)
        if usb.read(f"{stage}/prep_bootstrap.sh") != (source / "prep_bootstrap.sh").read_bytes():
            raise RuntimeError("Procursus staging readback mismatch")
    usb.script(f"""
set -eu
test -d {q(stage + '/bin')}
test -d {q(stage + '/usr')}
test -f {q(stage + '/prep_bootstrap.sh')}
count=$(/usr/bin/find {q(stage)} | /usr/bin/wc -l)
test "$count" -gt 6000
if [ -d {q(target)} ]; then /bin/rmdir {q(target)}; fi
/bin/mv {q(stage)} {q(target)}
printf '%s\\n' {q(BOOTSTRAP_SHA256)} > {q(marker)}
""")
    if usb.read(marker).decode().strip() != BOOTSTRAP_SHA256:
        raise RuntimeError("Procursus completion marker readback mismatch")
    print("[+] Procursus files installed on Data at /mnt2/jb", flush=True)


def install_daemons(usb: USBSSH, ticket_id: str, patches: dict[str, bytes]) -> None:
    backup = f"/mnt2/xra1n-backup/{ticket_id}"
    stage = f"/mnt2/xra1n-stage/{ticket_id}/daemons"
    current = {name: digest(usb.read(target)) for name, (target, _) in TARGETS.items()}
    accepted_installed = {name: {digest(patches[name])} for name in TARGETS}
    # The final pairing stage intentionally upgrades coreauthd from the base
    # daemon patch to the pinned weak-load image.
    accepted_installed["coreauthd"].add(PAIR_FILES["pair-coreauthd"][1])
    if all(current[name] in accepted_installed[name] for name in TARGETS):
        for name, (_, pristine_hash) in TARGETS.items():
            if digest(usb.read(f"{backup}/{name}")) != pristine_hash:
                raise RuntimeError(f"already-patched {name} lacks a pristine on-device backup")
        print("[+] three daemons already match a recognized final state; pristine backups verified", flush=True)
        return
    if any(current[name] != pristine_hash for name, (_, pristine_hash) in TARGETS.items()):
        raise RuntimeError("daemon set is mixed or unknown; refusing to overwrite it")
    usb.script(f"/bin/mkdir -p {q(backup)} {q(stage)}")
    # Check all originals and make all on-device backups before changing any
    # System file. No original binary is copied to the Linux host.
    for name, (target, pristine_hash) in TARGETS.items():
        if digest(usb.read(target)) != pristine_hash:
            raise RuntimeError(f"device {name} does not match pristine 22H374; no daemon changes made")
        backup_path = f"{backup}/{name}"
        usb.script(f"""
set -eu
test -f {q(target)} && test ! -L {q(target)}
if [ ! -e {q(backup_path)} ]; then /bin/cp -p {q(target)} {q(backup_path)}; fi
""")
        if digest(usb.read(backup_path)) != pristine_hash:
            raise RuntimeError(f"on-device backup mismatch: {name}")
        staged = f"{stage}/{name}"
        usb.call(f"/bin/cat > {q(staged)}", input_data=patches[name])
        if digest(usb.read(staged)) != digest(patches[name]):
            raise RuntimeError(f"on-device staged patch mismatch: {name}")
        print(f"[+] {name}: pristine, on-device backup and staged patch verified", flush=True)

    changed: list[str] = []
    try:
        for name, (target, _) in TARGETS.items():
            staged = f"{stage}/{name}"
            # Rewrite through the existing inode rather than replacing an
            # APFS System vnode with mv/rename.
            changed.append(name)
            usb.script(f"""
set -eu
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
test -f {q(target)} && test ! -L {q(target)}
/bin/cat {q(staged)} > {q(target)}
chown 0:0 {q(target)}
chmod 755 {q(target)}
""")
            if digest(usb.read(target)) != digest(patches[name]):
                raise RuntimeError(f"installed {name} readback mismatch")
            print(f"[+] installed and read back {name}", flush=True)
    except Exception:
        for name in reversed(changed):
            target, pristine_hash = TARGETS[name]
            try:
                usb.script(f"/bin/cat {q(backup + '/' + name)} > {q(target)}")
                if digest(usb.read(target)) != pristine_hash:
                    print(f"[!] rollback readback FAILED: {name}", file=sys.stderr)
            except Exception as error:
                print(f"[!] rollback FAILED for {name}: {error}", file=sys.stderr)
        raise
    record = "".join(f"{digest(patches[name])}  {name}\n" for name in TARGETS)
    record_path = f"{backup}/installed.sha256"
    usb.call(f"/bin/cat > {q(record_path)}", input_data=record.encode())
    if usb.read(record_path) != record.encode():
        raise RuntimeError("on-device daemon install record mismatch")


def install_commcenter(usb: USBSSH, ticket_id: str, patched: bytes) -> None:
    """Install the signed three-gate CommCenter patch, or verify and skip it."""
    backup = f"/mnt2/xra1n-backup/{ticket_id}/CommCenter"
    stage = f"/mnt2/xra1n-stage/{ticket_id}/CommCenter.cellular"
    patched_hash = digest(patched)
    if patched_hash != COMMCENTER_PATCHED_SHA256:
        raise RuntimeError("patched CommCenter input hash mismatch")

    current_hash = digest(usb.read(COMMCENTER_PATH))
    if current_hash == patched_hash:
        if digest(usb.read(backup)) != COMMCENTER_PRISTINE_SHA256:
            raise RuntimeError("already-patched CommCenter lacks its pristine on-device backup")
        print("[+] CommCenter already matches cellular patch; pristine backup verified",
              flush=True)
        return
    if current_hash != COMMCENTER_PRISTINE_SHA256:
        raise RuntimeError("device CommCenter is neither pristine nor the recognized cellular patch")

    original_stat = usb.call(f"/bin/ls -ln {q(COMMCENTER_PATH)}").stdout.decode().split()
    usb.script(f"""
set -eu
/bin/mkdir -p {q(str(Path(backup).parent))} {q(str(Path(stage).parent))}
test -f {q(COMMCENTER_PATH)} && test ! -L {q(COMMCENTER_PATH)}
if [ ! -e {q(backup)} ]; then /bin/cp -p {q(COMMCENTER_PATH)} {q(backup)}; fi
""")
    if digest(usb.read(backup)) != COMMCENTER_PRISTINE_SHA256:
        raise RuntimeError("on-device pristine CommCenter backup mismatch")
    usb.call(f"/bin/cat > {q(stage)}", input_data=patched)
    if digest(usb.read(stage)) != patched_hash:
        raise RuntimeError("on-device staged CommCenter mismatch")

    try:
        # Keep the System vnode and its metadata, as for the other System
        # executables; only rewrite its contents.
        usb.script(f"set -eu; test -f {q(COMMCENTER_PATH)} && test ! -L {q(COMMCENTER_PATH)}; "
                   f"/bin/cat {q(stage)} > {q(COMMCENTER_PATH)}")
        if digest(usb.read(COMMCENTER_PATH)) != patched_hash:
            raise RuntimeError("installed CommCenter readback mismatch")
        installed_stat = usb.call(f"/bin/ls -ln {q(COMMCENTER_PATH)}").stdout.decode().split()
        if (installed_stat[0], *installed_stat[2:4]) != (original_stat[0], *original_stat[2:4]):
            raise RuntimeError("installed CommCenter owner or mode changed")
    except Exception:
        usb.script(f"/bin/cat {q(backup)} > {q(COMMCENTER_PATH)}")
        if digest(usb.read(COMMCENTER_PATH)) != COMMCENTER_PRISTINE_SHA256:
            print("[!] CommCenter rollback readback FAILED", file=sys.stderr)
        else:
            print("[!] restored pristine CommCenter backup", file=sys.stderr)
        raise
    print(f"[+] signed cellular-patched CommCenter installed and read back: {patched_hash}",
          flush=True)


def install_setup(usb: USBSSH, ticket_id: str, signed: bytes) -> None:
    backup = f"/mnt2/xra1n-backup/{ticket_id}/Setup"
    stage = f"/mnt2/xra1n-stage/{ticket_id}/Setup.patched.signed"
    signed_hash = digest(signed)
    if signed_hash != SETUP_SIGNED_SHA256:
        raise RuntimeError("signed Setup hash mismatch")
    current_hash = digest(usb.read(SETUP_PATH))
    if current_hash == signed_hash:
        if digest(usb.read(backup)) != SETUP_PRISTINE_SHA256:
            raise RuntimeError("already-patched Setup lacks a pristine on-device backup")
        print("[+] Setup already matches signed patch; pristine backup verified", flush=True)
        return
    if current_hash != SETUP_PRISTINE_SHA256:
        raise RuntimeError("device Setup is not pristine XR 22H374; refusing replacement")

    original_stat = usb.call(f"/bin/ls -ln {q(SETUP_PATH)}").stdout.decode().split()
    usb.script(f"""
set -eu
/bin/mkdir -p {q(str(Path(backup).parent))} {q(str(Path(stage).parent))}
test -f {q(SETUP_PATH)} && test ! -L {q(SETUP_PATH)}
if [ ! -e {q(backup)} ]; then /bin/cp -p {q(SETUP_PATH)} {q(backup)}; fi
""")
    if digest(usb.read(backup)) != SETUP_PRISTINE_SHA256:
        raise RuntimeError("on-device pristine Setup backup mismatch")
    usb.call(f"/bin/cat > {q(stage)}", input_data=signed)
    if digest(usb.read(stage)) != signed_hash:
        raise RuntimeError("on-device staged Setup readback mismatch")
    print("[+] Setup pristine backup and signed stage verified", flush=True)

    try:
        # Preserve the existing System inode, uid/gid and mode. Do not replace
        # the app bundle or its separate _CodeSignature resource seal.
        usb.script(f"set -eu; test -f {q(SETUP_PATH)} && test ! -L {q(SETUP_PATH)}; "
                   f"/bin/cat {q(stage)} > {q(SETUP_PATH)}")
        if digest(usb.read(SETUP_PATH)) != signed_hash:
            raise RuntimeError("installed Setup readback mismatch")
        installed_stat = usb.call(f"/bin/ls -ln {q(SETUP_PATH)}").stdout.decode().split()
        if (installed_stat[0], *installed_stat[2:4]) != (original_stat[0], *original_stat[2:4]):
            raise RuntimeError("installed Setup owner or mode changed")
    except Exception:
        usb.script(f"/bin/cat {q(backup)} > {q(SETUP_PATH)}")
        if digest(usb.read(SETUP_PATH)) != SETUP_PRISTINE_SHA256:
            print("[!] Setup rollback readback FAILED", file=sys.stderr)
        else:
            print("[!] restored pristine Setup backup", file=sys.stderr)
        raise
    print(f"[+] signed Setup installed and read back: {signed_hash}", flush=True)


def install_jbboot(usb: USBSSH, ticket_id: str, helper: bytes) -> None:
    """Install the signed PATH-link helper and its launchd-cache job."""
    if digest(helper) != JBBOOT_SHA256:
        raise RuntimeError("xra1n-jbboot artifact hash mismatch")

    helper_exists = usb.script(
        f"if test -f {q(JBBOOT_PATH)} && test ! -L {q(JBBOOT_PATH)}; "
        "then echo YES; else echo NO; fi"
    ).strip() == "YES"
    original_helper = usb.read(JBBOOT_PATH) if helper_exists else None
    if (original_helper is not None and digest(original_helper) != JBBOOT_SHA256 and
            digest(original_helper) not in JBBOOT_UPGRADABLE_SHA256):
        raise RuntimeError("refusing to overwrite unknown xra1n-jbboot executable")

    original_cache = usb.read(LAUNCHD_CACHE_PATH)
    try:
        current_document = plistlib.loads(original_cache)
    except Exception as error:
        raise RuntimeError(f"installed launchd cache is invalid: {error}") from error
    jobs = current_document.get("LaunchDaemons") if isinstance(current_document, dict) else None
    if (not isinstance(current_document, dict) or
            current_document.get("VersionNumber") != 7 or not isinstance(jobs, dict)):
        raise RuntimeError("installed launchd cache has an unexpected shape")
    current_job = jobs.get(JBBOOT_CACHE_KEY)
    if current_job is not None and current_job != JBBOOT_JOB:
        raise RuntimeError("installed launchd cache has a conflicting jbboot job")
    # A freshly erased 22H374 cache has 628 stock jobs.  The previous
    # installation had one unrelated experimental job, which made the old
    # guard appear to be 629 before jbboot was added.
    current_dropbear = jobs.get(DROPBEAR_CACHE_KEY)
    if current_dropbear is not None and current_dropbear != DROPBEAR_JOB:
        raise RuntimeError("installed launchd cache has a conflicting Dropbear job")
    expected_count = 628 + int(current_job is not None) + int(current_dropbear is not None)
    if len(jobs) != expected_count:
        raise RuntimeError(
            f"launchd cache has {len(jobs)} jobs; expected {expected_count} for XR 22H374"
        )

    patched_cache = build_jbboot_cache(original_cache)
    if original_helper == helper and patched_cache == original_cache:
        print("[+] xra1n-jbboot and Dropbear launchd-cache jobs already match", flush=True)
        return

    backup_dir = f"/mnt2/xra1n-backup/{ticket_id}/jbboot"
    stage_dir = f"/mnt2/xra1n-stage/{ticket_id}/jbboot"
    cache_backup = f"{backup_dir}/launchd.plist.before-jbboot"
    helper_stage = f"{stage_dir}/xra1n-jbboot"
    cache_stage = f"{stage_dir}/launchd.plist"
    usb.script(f"/bin/mkdir -p {q(backup_dir)} {q(stage_dir)}")

    backup_exists = usb.script(
        f"if test -f {q(cache_backup)}; then echo YES; else echo NO; fi"
    ).strip() == "YES"
    if not backup_exists:
        usb.call(f"/bin/cat > {q(cache_backup)}", input_data=original_cache)
    try:
        saved_document = plistlib.loads(usb.read(cache_backup))
    except Exception as error:
        raise RuntimeError(f"jbboot launchd-cache backup is invalid: {error}") from error
    if not isinstance(saved_document, dict) or saved_document.get("VersionNumber") != 7:
        raise RuntimeError("jbboot launchd-cache backup has an unexpected shape")

    usb.call(f"/bin/cat > {q(helper_stage)}", input_data=helper)
    usb.call(f"/bin/cat > {q(cache_stage)}", input_data=patched_cache)
    if usb.read(helper_stage) != helper or usb.read(cache_stage) != patched_cache:
        raise RuntimeError("jbboot staged artifact readback mismatch")

    try:
        if original_helper is None:
            usb.script(f"""
set -eu
/bin/mkdir -p {q(str(Path(JBBOOT_PATH).parent))}
/bin/cp -p {q(helper_stage)} {q(JBBOOT_PATH + '.new')}
/usr/sbin/chown 0:0 {q(JBBOOT_PATH + '.new')}
/bin/chmod 0755 {q(JBBOOT_PATH + '.new')}
/bin/mv -f {q(JBBOOT_PATH + '.new')} {q(JBBOOT_PATH)}
""")
        elif original_helper != helper:
            # Keep the existing System vnode when upgrading a known helper.
            usb.script(f"""
set -eu
/bin/cat {q(helper_stage)} > {q(JBBOOT_PATH)}
/usr/sbin/chown 0:0 {q(JBBOOT_PATH)}
/bin/chmod 0755 {q(JBBOOT_PATH)}
""")
        if patched_cache != original_cache:
            # Preserve the existing APFS vnode, as with the other System files.
            usb.call(f"/bin/cat > {q(LAUNCHD_CACHE_PATH)}", input_data=patched_cache)

        installed_helper = usb.read(JBBOOT_PATH)
        installed_cache = usb.read(LAUNCHD_CACHE_PATH)
        installed_document = plistlib.loads(installed_cache)
        if installed_helper != helper:
            raise RuntimeError("installed xra1n-jbboot readback mismatch")
        if installed_cache != patched_cache:
            raise RuntimeError("installed jbboot launchd cache readback mismatch")
        if installed_document["LaunchDaemons"].get(JBBOOT_CACHE_KEY) != JBBOOT_JOB:
            raise RuntimeError("installed launchd cache is missing exact jbboot job")
        if installed_document["LaunchDaemons"].get(DROPBEAR_CACHE_KEY) != DROPBEAR_JOB:
            raise RuntimeError("installed launchd cache is missing exact Dropbear job")
    except Exception:
        usb.call(f"/bin/cat > {q(LAUNCHD_CACHE_PATH)}", input_data=original_cache)
        if original_helper is None:
            usb.call(f"/bin/rm -f {q(JBBOOT_PATH)}")
        else:
            usb.call(f"/bin/cat > {q(JBBOOT_PATH)}", input_data=original_helper)
        raise

    print(f"[+] installed xra1n-jbboot and launchd job; cache SHA-256 "
          f"{digest(patched_cache)}", flush=True)


def install_sileo(usb: USBSSH, ticket_id: str, archive: bytes) -> None:
    """Install the reproducibly patched and signed Liter8 Sileo system app."""
    if digest(archive) != SILEO_ARCHIVE_SHA256:
        raise RuntimeError("Sileo archive hash mismatch")

    current_exists = usb.script(
        f"if test -f {q(SILEO_PATH + '/Sileo')} && "
        f"test -f {q(SILEO_PATH + '/giveMeRoot')}; then echo YES; else echo NO; fi"
    ).strip() == "YES"
    if current_exists:
        current_main = digest(usb.read(SILEO_PATH + "/Sileo"))
        current_helper = digest(usb.read(SILEO_PATH + "/giveMeRoot"))
        if current_main == SILEO_BINARY_SHA256 and current_helper == SILEO_HELPER_SHA256:
            mode = usb.call(f"/usr/bin/stat -f %Lp {q(SILEO_PATH + '/giveMeRoot')}").stdout.strip()
            if mode != b"755":
                usb.script(f"/bin/chmod 0755 {q(SILEO_PATH + '/giveMeRoot')}")
            print("[+] Liter8-compatible Sileo already installed", flush=True)
            return

    backup_dir = f"/mnt2/xra1n-backup/{ticket_id}/sileo"
    backup = f"{backup_dir}/Sileo.app.before-liter8"
    stage_dir = f"/mnt2/xra1n-stage/{ticket_id}/sileo"
    remote_archive = f"{stage_dir}/Sileo.app.tar.gz"
    unpack = f"{stage_dir}/unpack"
    usb.script(f"/bin/mkdir -p {q(backup_dir)} {q(stage_dir)}")

    if current_exists:
        backup_exists = usb.script(
            f"if test -d {q(backup)}; then echo YES; else echo NO; fi"
        ).strip() == "YES"
        if not backup_exists:
            usb.script(f"/bin/cp -Rp {q(SILEO_PATH)} {q(backup)}")
        if not usb.script(f"if test -f {q(backup + '/Sileo')}; then echo YES; fi").strip():
            raise RuntimeError("on-device Sileo backup verification failed")

    usb.call(f"/bin/cat > {q(remote_archive)}", input_data=archive, timeout=180)
    if digest(usb.read(remote_archive)) != SILEO_ARCHIVE_SHA256:
        raise RuntimeError("staged Sileo archive readback mismatch")
    usb.script(f"""
set -eu
/bin/rm -rf {q(unpack)}
/bin/mkdir -p {q(unpack)}
/usr/bin/gzip -dc {q(remote_archive)} | /usr/bin/tar -xpf - -C {q(unpack)}
test -f {q(unpack + '/Sileo.app/Sileo')}
test -f {q(unpack + '/Sileo.app/giveMeRoot')}
""")
    staged_main = usb.read(unpack + "/Sileo.app/Sileo")
    staged_helper = usb.read(unpack + "/Sileo.app/giveMeRoot")
    if digest(staged_main) != SILEO_BINARY_SHA256:
        raise RuntimeError("staged Sileo executable hash mismatch")
    if digest(staged_helper) != SILEO_HELPER_SHA256:
        raise RuntimeError("staged giveMeRoot hash mismatch")
    for marker in (b"/var/jb/.installed_usbl8r", b"000501:000501"):
        if staged_main.count(marker) != 1:
            raise RuntimeError(f"staged Sileo marker mismatch: {marker!r}")

    try:
        usb.script(f"""
set -eu
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
chown -R 0:0 {q(unpack + '/Sileo.app')}
chmod 0755 {q(unpack + '/Sileo.app/Sileo')}
chmod 0755 {q(unpack + '/Sileo.app/giveMeRoot')}
rm -rf {q(SILEO_PATH)}
mv {q(unpack + '/Sileo.app')} {q(SILEO_PATH)}
chown -R 0:0 {q(SILEO_PATH)}
chmod 0755 {q(SILEO_PATH + '/giveMeRoot')}
/bin/sync
""")
        if digest(usb.read(SILEO_PATH + "/Sileo")) != SILEO_BINARY_SHA256:
            raise RuntimeError("installed Sileo executable readback mismatch")
        if digest(usb.read(SILEO_PATH + "/giveMeRoot")) != SILEO_HELPER_SHA256:
            raise RuntimeError("installed Sileo helper readback mismatch")
        mode = usb.call(f"/usr/bin/stat -f %Lp {q(SILEO_PATH + '/giveMeRoot')}").stdout.strip()
        if mode != b"755":
            raise RuntimeError(f"installed giveMeRoot mode is {mode!r}, expected 755")
    except Exception:
        usb.script(f"/bin/rm -rf {q(SILEO_PATH)}")
        if current_exists:
            usb.script(f"/bin/cp -Rp {q(backup)} {q(SILEO_PATH)}")
        raise

    print("[+] installed and verified Liter8-compatible Sileo.app", flush=True)


def install_normal_ssh(usb: USBSSH, ticket_id: str, patches: dict[str, bytes]) -> None:
    """Install the pinned key-only normal-boot Dropbear payload idempotently."""
    destinations = {
        "dropbear": "/mnt1/usr/local/bin/dropbear",
        "dropbearkey": "/mnt1/usr/local/bin/dropbearkey",
        "sh": "/mnt1/bin/sh", "ls": "/mnt1/bin/ls", "cat": "/mnt1/bin/cat",
    }
    auth = "/mnt2/root/.ssh/authorized_keys"
    keys = [f"/mnt2/dropbear/dropbear_{kind}_host_key"
            for kind in ("rsa", "ecdsa", "dss")]
    matching = True
    for name, target in destinations.items():
        exists = usb.script(f"test -f {q(target)} && echo YES || true").strip() == "YES"
        matching &= exists and digest(usb.read(target)) == NORMAL_SSH_FILES[name][1]
    matching &= (usb.script(f"test -f {q(auth)} && echo YES || true").strip() == "YES" and
                 usb.read(auth) == patches["authorized_keys"])
    matching &= all(usb.script(f"test -s {q(path)} && echo YES || true").strip() == "YES"
                    for path in keys)
    if matching:
        print("[+] normal-boot Dropbear binaries, client key and host keys already match; skipping", flush=True)
        return

    stage = f"/mnt2/xra1n-stage/{ticket_id}/normal-ssh"
    usb.script(f"/bin/mkdir -p {q(stage)} /mnt1/usr/local/bin /mnt1/bin /mnt2/root/.ssh /mnt2/dropbear")
    for name, target in destinations.items():
        expected = NORMAL_SSH_FILES[name][1]
        exists = usb.script(f"test -f {q(target)} && echo YES || true").strip() == "YES"
        if exists and digest(usb.read(target)) != expected:
            raise RuntimeError(f"refusing unknown normal-SSH target: {target}")
        staged = f"{stage}/{name}"
        usb.call(f"/bin/cat > {q(staged)}", input_data=patches[name])
        usb.script(f"/bin/cat {q(staged)} > {q(target)}; /usr/sbin/chown 0:0 {q(target)}; /bin/chmod 0755 {q(target)}")
        if digest(usb.read(target)) != expected:
            raise RuntimeError(f"normal-SSH readback mismatch: {name}")
    if usb.script(f"test -f {q(auth)} && echo YES || true").strip() == "YES" and usb.read(auth) != patches["authorized_keys"]:
        raise RuntimeError("refusing to overwrite unknown authorized_keys")
    usb.call(f"/bin/cat > {q(auth)}", input_data=patches["authorized_keys"])
    usb.script(f"/usr/sbin/chown -R 0:0 /mnt2/root/.ssh; /bin/chmod 0700 /mnt2/root/.ssh; /bin/chmod 0600 {q(auth)}")
    usb.script(r'''
set -eu
for spec in "rsa 2048 dropbear_rsa_host_key" "ecdsa 256 dropbear_ecdsa_host_key" "dss 1024 dropbear_dss_host_key"; do
    set -- $spec; path=/mnt2/dropbear/$3
    if [ ! -s "$path" ]; then /usr/local/bin/dropbearkey -t "$1" -s "$2" -f "$path" >/dev/null; fi
    /usr/sbin/chown 0:0 "$path"; /bin/chmod 0600 "$path"
done
''')
    if usb.read(auth) != patches["authorized_keys"]:
        raise RuntimeError("normal-SSH authorized_keys readback mismatch")
    print("[+] installed and verified normal-boot key-only Dropbear", flush=True)


def install_pairing_and_launchd_hook(usb: USBSSH, ticket_id: str,
                                     patches: dict[str, bytes]) -> None:
    """Install the validated pairing weak loads and PID-1 lhook idempotently."""
    targets = {
        "pair-lockdownd": "/mnt1/usr/libexec/lockdownd",
        "pair-coreauthd": "/mnt1/System/Library/Frameworks/LocalAuthentication.framework/Support/coreauthd",
        "xra1n-pair.dylib": "/mnt1/usr/lib/xra1n-pair.dylib",
        "xra1n-coreauth.dylib": "/mnt1/usr/lib/xra1n-coreauth.dylib",
        "launchd": "/mnt1/sbin/launchd", "lhook": "/mnt1/usr/lib/lhook",
    }
    marker = "/mnt2/root/Library/Lockdown/.xra1n-pairing-fallback"
    probe = "/mnt1/usr/lib/l8pair-probe.dylib"
    probe_sha256 = "ffd208fcdb43c8dce8c00f2fb000c1ecd27091c714708042a772b7d35f01b195"
    probe_exists = usb.script(f"test -f {q(probe)} && echo YES || true").strip() == "YES"
    if probe_exists and digest(usb.read(probe)) != probe_sha256:
        raise RuntimeError("refusing to delete unknown l8pair-probe diagnostic")
    if (all(usb.script(f"test -f {q(target)} && echo YES || true").strip() == "YES" and
            digest(usb.read(target)) == digest(patches[name]) for name, target in targets.items()) and
            usb.script(f"test -f {q(marker)} && echo YES || true").strip() == "YES" and
            not probe_exists):
        print("[+] pairing weak loads, marker, patched launchd and lhook already match; skipping", flush=True)
        return

    allowed = {
        "pair-lockdownd": {"b74a41d3a5215e08dcc451048fef9175b85b0335b9cd519701beb3b0b7c00491",
                       "f2e15d6c21dece0d60d47b80560fd6be40fbf23730e3911fbd49790fc4c6a383",
                       "f9a61f872e5bf9917317b6d3c724c568257a9557c90274af4319cb826f72103b",
                       PAIR_FILES["pair-lockdownd"][1]},
        "pair-coreauthd": {TARGETS["coreauthd"][1], "879ddb3eb9bfe03c558ce584bb0507572926de2fe517b3bfeb38a8f2ef0ab862",
                       PAIR_FILES["pair-coreauthd"][1]},
        "launchd": {"e2323c09df447b6d57bc3d9132be82f7186af167a656921212ca9880e29dea06",
                     LAUNCHD_HOOK_FILES["launchd"][1]},
        # Recognized hook-experiment builds may already be installed on Data's
        # paired System volume. Replace only these measured pre-images with the
        # guarded pre-CommCenter lhook; reject every unknown binary.
        "lhook": {LAUNCHD_HOOK_FILES["lhook"][1],
                  "1f34fd117e5bd46050e0cacffd60dabd417884e8deebc6c8dc754186bcf1946a",
                  "77583f688ae9890e2799e09995f7d04244e515d5cfd6110313e040375dec6cbb"},
    }
    backup = f"/mnt2/xra1n-backup/{ticket_id}/pairing-launchd"
    stage = f"/mnt2/xra1n-stage/{ticket_id}/pairing-launchd"
    usb.script(f"/bin/mkdir -p {q(backup)} {q(stage)} /mnt1/usr/lib")
    for name, target in targets.items():
        expected = digest(patches[name])
        exists = usb.script(f"test -f {q(target)} && echo YES || true").strip() == "YES"
        if exists:
            current = usb.read(target); current_hash = digest(current)
            permitted = allowed.get(name, {expected})
            if current_hash not in permitted:
                raise RuntimeError(f"refusing unknown {name} pre-image: {current_hash}")
            backup_path = f"{backup}/{name}.before"
            if usb.script(f"test -f {q(backup_path)} && echo YES || true").strip() != "YES":
                usb.call(f"/bin/cat > {q(backup_path)}", input_data=current)
        staged = f"{stage}/{name}"
        usb.call(f"/bin/cat > {q(staged)}", input_data=patches[name])
        usb.script(f"/bin/cat {q(staged)} > {q(target)}; /usr/sbin/chown 0:0 {q(target)}; /bin/chmod 0755 {q(target)}")
        if digest(usb.read(target)) != expected:
            raise RuntimeError(f"installed {name} readback mismatch")
    usb.script(f"/bin/mkdir -p /mnt2/root/Library/Lockdown; : > {q(marker)}; /usr/sbin/chown 0:0 {q(marker)}; /bin/chmod 0600 {q(marker)}")
    if probe_exists:
        usb.script(f"/bin/rm {q(probe)}; /bin/sync")
        if usb.script(f"test -e {q(probe)} && echo YES || true").strip() == "YES":
            raise RuntimeError("l8pair-probe diagnostic removal failed")
    print("[+] installed and verified pairing weak loads, patched launchd and lhook", flush=True)


def install_guarded_tweak_stack(usb: USBSSH, ticket_id: str,
                                patches: dict[str, bytes]) -> None:
    """Install session-guarded lhook control and pinned ElleKit only."""
    targets = {
        "lhookctl": ("/mnt2/jb/usr/bin/lhookctl", 0o755),
        "lhook.deny": ("/mnt2/jb/etc/lhook.deny", 0o644),
        "libinjector.dylib": ("/mnt2/jb/usr/lib/ellekit/libinjector.dylib", 0o755),
        "MobileSafety.dylib": ("/mnt2/jb/usr/lib/ellekit/MobileSafety.dylib", 0o755),
        "ELLEKIT-LICENSE": ("/mnt2/jb/usr/share/doc/ellekit/LICENSE", 0o644),
        "ellekit.deb": ("/mnt2/jb/var/root/ellekit_1.2_iphoneos-arm64.deb", 0o644),
    }
    links = {
        "/mnt2/jb/usr/lib/TweakLoader.dylib": "/var/jb/usr/lib/ellekit/libinjector.dylib",
        "/mnt2/jb/usr/lib/TweakInject.dylib": "/var/jb/usr/lib/ellekit/libinjector.dylib",
    }

    def link_matches(path: str, target: str) -> bool:
        line = usb.call(f"/bin/ls -ld {q(path)} 2>/dev/null || true").stdout.decode(
            "utf-8", "replace").strip()
        return " -> " in line and line.rsplit(" -> ", 1)[1] == target

    matching = True
    for name, (target, mode) in targets.items():
        exists = usb.script(f"test -f {q(target)} && test ! -L {q(target)} && echo YES || true").strip() == "YES"
        matching &= exists and digest(usb.read(target)) == TWEAK_STACK_FILES[name][1]
        if exists:
            installed_mode = usb.call(f"/usr/bin/stat -f %Lp {q(target)}").stdout.strip()
            matching &= installed_mode == f"{mode:o}".encode()
    matching &= all(link_matches(path, target) for path, target in links.items())
    markers_absent = all(usb.script(f"test ! -e {q(path)} && echo YES || true").strip() == "YES"
                         for path in ("/mnt2/jb/.lhook_enabled", "/mnt2/jb/.lhook_debug"))
    if matching and markers_absent:
        print("[+] guarded lhook controller and signed ElleKit already match; skipping",
              flush=True)
        return

    accepted = {
        "libinjector.dylib": {
            TWEAK_STACK_FILES["libinjector.dylib"][1],
            "85b70775dcc76f178a265f22f4f90ad4915b6a314aacc3849f4622a71eb0b4b2",
        },
        "MobileSafety.dylib": {
            TWEAK_STACK_FILES["MobileSafety.dylib"][1],
            "e8ad92038d7c93582997d9d4d8b5cdc89d501ea959e11fb4015b75f23246c676",
        },
    }
    backup = f"/mnt2/xra1n-backup/{ticket_id}/guarded-tweak-stack"
    stage = f"/mnt2/xra1n-stage/{ticket_id}/guarded-tweak-stack"
    usb.script(f"/bin/mkdir -p {q(backup)} {q(stage)} /mnt2/jb/usr/bin /mnt2/jb/etc "
               "/mnt2/jb/usr/lib/ellekit /mnt2/jb/usr/lib/TweakInject "
               "/mnt2/jb/usr/share/doc/ellekit /mnt2/jb/var/root")

    for name, (target, mode) in targets.items():
        expected = TWEAK_STACK_FILES[name][1]
        exists = usb.script(f"test -f {q(target)} && test ! -L {q(target)} && echo YES || true").strip() == "YES"
        if exists:
            current = usb.read(target)
            current_hash = digest(current)
            if current_hash not in accepted.get(name, {expected}):
                raise RuntimeError(f"refusing unknown tweak-stack pre-image {name}: {current_hash}")
            backup_path = f"{backup}/{name}.before"
            if current_hash != expected and usb.script(
                    f"test -f {q(backup_path)} && echo YES || true").strip() != "YES":
                usb.call(f"/bin/cat > {q(backup_path)}", input_data=current)
        staged = f"{stage}/{name}"
        usb.call(f"/bin/cat > {q(staged)}", input_data=patches[name])
        if digest(usb.read(staged)) != expected:
            raise RuntimeError(f"staged tweak-stack readback mismatch: {name}")
        usb.script(f"/bin/cat {q(staged)} > {q(target)}; /usr/sbin/chown 0:0 {q(target)}; "
                   f"/bin/chmod {mode:04o} {q(target)}")
        if digest(usb.read(target)) != expected:
            raise RuntimeError(f"installed tweak-stack readback mismatch: {name}")
        installed_mode = usb.call(f"/usr/bin/stat -f %Lp {q(target)}").stdout.strip()
        if installed_mode != f"{mode:o}".encode():
            raise RuntimeError(f"installed mode mismatch for {name}: {installed_mode!r}")

    for path, target in links.items():
        usb.script(f"/bin/rm -f {q(path)}; /bin/ln -s {q(target)} {q(path)}")
        if not link_matches(path, target):
            raise RuntimeError(f"ElleKit loader link mismatch: {path}")

    # A marker created in SSHRD is either unsafe (old hook) or invalid for the
    # normal boot session (new hook). Normal userspace must opt in with lhookctl.
    usb.script("/bin/rm -f /mnt2/jb/.lhook_enabled /mnt2/jb/.lhook_debug; /bin/sync")
    print("[+] installed and verified session-guarded lhook controller, signed ElleKit "
          "and pinned deb; injection remains disabled", flush=True)


def install_xr_runtime_fixes(usb: USBSSH, ticket_id: str,
                             patches: dict[str, bytes]) -> None:
    """Install PhotoForce helpers and three narrowly filtered tweaks.

    Provision the confirmed persona, LocalAuthentication and Files markers.
    The guarded loader keeps injection disabled; lhook enablement and daemon
    restarts remain manual after normal boot.
    """
    current: dict[str, str | None] = {}
    matching = True
    for name, (_, expected, target, mode) in XR_RUNTIME_FILES.items():
        kind = usb.script(f"if test -L {q(target)}; then echo SYMLINK; "
                          f"elif test -f {q(target)}; then echo FILE; "
                          f"elif test -e {q(target)}; then echo OTHER; "
                          "else echo ABSENT; fi").strip()
        if kind not in {"FILE", "ABSENT"}:
            raise RuntimeError(f"refusing non-regular XR runtime target {name}: {kind}")
        current_hash = digest(usb.read(target)) if kind == "FILE" else None
        if current_hash is not None and current_hash not in (
                {expected} | XR_RUNTIME_UPGRADABLE.get(name, set())):
            raise RuntimeError(f"refusing unknown XR runtime pre-image {name}: {current_hash}")
        current[name] = current_hash
        if current_hash == expected:
            metadata = usb.call(f"/usr/bin/stat -f '%u:%g:%Lp' {q(target)}").stdout.strip()
            matching &= metadata == f"0:0:{mode:o}".encode()
        else:
            matching = False
    if matching:
        print("[+] PhotoForce and XR tweak files already match; skipping writes", flush=True)
    else:
        _write_xr_runtime_fixes(usb, ticket_id, patches, current)
    ensure_persona_marker(usb)
    ensure_localauth_marker(usb)
    ensure_files_marker(usb)


def _write_xr_runtime_fixes(usb: USBSSH, ticket_id: str,
                            patches: dict[str, bytes],
                            current: dict[str, str | None]) -> None:

    backup = f"/mnt2/xra1n-backup/{ticket_id}/xr-runtime-fixes"
    stage = f"/mnt2/xra1n-stage/{ticket_id}/xr-runtime-fixes"
    usb.script(f"/bin/mkdir -p {q(backup)} {q(stage)} /mnt1/usr/local/bin "
               "/mnt2/jb/usr/lib/TweakInject")
    for name, (_, expected, target, mode) in XR_RUNTIME_FILES.items():
        if current[name] is not None and current[name] != expected:
            backup_path = f"{backup}/{name}.before"
            if usb.script(f"test -e {q(backup_path)} && echo YES || true").strip() == "YES":
                if digest(usb.read(backup_path)) != current[name]:
                    raise RuntimeError(f"XR runtime backup mismatch: {name}")
            else:
                usb.call(f"/bin/cat > {q(backup_path)}", input_data=usb.read(target))
                if digest(usb.read(backup_path)) != current[name]:
                    raise RuntimeError(f"XR runtime backup readback mismatch: {name}")
        staged = f"{stage}/{name}"
        usb.call(f"/bin/cat > {q(staged)}", input_data=patches[name])
        if digest(usb.read(staged)) != expected:
            raise RuntimeError(f"XR runtime staging readback mismatch: {name}")
        # Write through an existing System vnode where one exists; do not
        # rename over it or change the verified executable's byte layout.
        usb.script(f"/bin/cat {q(staged)} > {q(target)}; "
                   f"/usr/sbin/chown 0:0 {q(target)}; /bin/chmod {mode:04o} {q(target)}")
        if digest(usb.read(target)) != expected:
            raise RuntimeError(f"XR runtime installed readback mismatch: {name}")
        metadata = usb.call(f"/usr/bin/stat -f '%u:%g:%Lp' {q(target)}").stdout.strip()
        if metadata != f"0:0:{mode:o}".encode():
            raise RuntimeError(f"XR runtime metadata mismatch: {name}: {metadata!r}")
    usb.script("/bin/sync")
    print("[+] entitlement-free PhotoForce and filtered XR tweaks installed; "
          "activation markers verified separately", flush=True)


def ensure_runtime_marker(usb: USBSSH, marker: str, name: str) -> None:
    """Provision one secure marker without enabling lhook or restarting daemons."""
    result = usb.script(f"""
set -eu
marker={q(marker)}
[ ! -L "$marker" ] || exit 1
if [ -e "$marker" ]; then
    [ -f "$marker" ] || exit 1
else
    (umask 077; : > "$marker")
fi
/usr/sbin/chown 0:0 "$marker"
/bin/chmod 0600 "$marker"
/bin/sync
/usr/bin/stat -f '%u:%g:%Lp' "$marker"
""").strip()
    if result != "0:0:600":
        raise RuntimeError(f"{name} marker metadata mismatch: {result!r}")
    print(f"[+] {name} marker verified; lhook remains disabled", flush=True)


def ensure_persona_marker(usb: USBSSH) -> None:
    """Arm the tested App Store persona fallback on fresh installs."""
    ensure_runtime_marker(usb, "/mnt2/jb/.liter8-persona", "App Store persona")


def ensure_localauth_marker(usb: USBSSH) -> None:
    """Arm the tested nehelper-only VPN approval fallback on fresh installs."""
    ensure_runtime_marker(usb, "/mnt2/jb/.liter8-localauth", "VPN LocalAuthentication")


def ensure_files_marker(usb: USBSSH) -> None:
    """Arm the reviewed process-scoped Files payload on fresh installs."""
    ensure_runtime_marker(usb, "/mnt2/jb/.xra1n-files", "Files LocalStorage")


def install_staged_system_apps(usb: USBSSH, ticket_id: str) -> None:
    """Copy restored Data-staged Apple apps into System without overwrites."""
    record = f"/mnt2/xra1n-stage/{ticket_id}/apps-copied.txt"
    result = usb.script(f"""
set -eu
source=/mnt2/staged_system_apps
destination=/mnt1/Applications
test -d "$source"
test -d "$destination"
/bin/mkdir -p {q(str(Path(record).parent))}
: > {q(record + '.tmp')}
count=0
for app in "$source"/*.app; do
    test -d "$app" || continue
    name=${{app##*/}}
    if test ! -e "$destination/$name"; then
        /bin/cp -Rp "$app" "$destination/$name"
        /usr/sbin/chown -R 0:0 "$destination/$name"
    fi
    test -d "$destination/$name"
    printf '%s\n' "$name" >> {q(record + '.tmp')}
    count=$((count + 1))
done
/bin/mv -f {q(record + '.tmp')} {q(record)}
/usr/sbin/chown 0:0 {q(record)}
/bin/chmod 0644 {q(record)}
if test "$count" -gt 0; then
    /bin/rm -f /mnt2/jb/.xra1n-system-apps-registered
fi
/bin/sync
printf 'STAGED_SYSTEM_APPS_OK %s\n' "$count"
""").strip()
    if not result.startswith("STAGED_SYSTEM_APPS_OK "):
        raise RuntimeError(f"staged system-app copy failed verification: {result!r}")
    print(f"[+] {result}; first normal boot will run guarded uicache -a", flush=True)


def install_rootless_runtime_compat(usb: USBSSH) -> None:
    """Install the persistent paths required by Sileo and rootless scripts."""
    env_link = "/mnt1/usr/bin/env"
    env_target = "/var/jb/usr/bin/env"
    env_on_data = "/mnt2/jb/usr/bin/env"
    state = usb.script(f"""
set -eu
test -f {q(env_on_data)} && test ! -L {q(env_on_data)}
if test -L {q(env_link)}; then
    /usr/bin/stat -f %Y {q(env_link)}
elif test -e {q(env_link)}; then
    echo REAL_FILE
else
    echo ABSENT
fi
""").strip()
    if state == "REAL_FILE":
        raise RuntimeError("refusing to replace an existing Apple /usr/bin/env")
    if state not in {"ABSENT", env_target}:
        raise RuntimeError(f"unexpected /usr/bin/env symlink target: {state!r}")

    usb.script(f"""
set -eu
if test ! -L {q(env_link)}; then
    /bin/ln -s {q(env_target)} {q(env_link)}
fi
test "$(/usr/bin/stat -f %Y {q(env_link)})" = {q(env_target)}

/bin/mkdir -p /mnt2/jb/var/lib/apt/sileolists/partial
/usr/sbin/chown 501:501 /mnt2/jb/var/lib/apt/sileolists \
    /mnt2/jb/var/lib/apt/sileolists/partial
/bin/chmod 0755 /mnt2/jb/var/lib/apt/sileolists \
    /mnt2/jb/var/lib/apt/sileolists/partial

/bin/mkdir -p /mnt2/jb/var/cache/apt/archives/partial
/usr/sbin/chown 501:501 /mnt2/jb/var/cache/apt/archives \
    /mnt2/jb/var/cache/apt/archives/partial
/bin/chmod 0755 /mnt2/jb/var/cache/apt/archives \
    /mnt2/jb/var/cache/apt/archives/partial

: > /mnt2/jb/.installed_usbl8r
/usr/sbin/chown 0:0 /mnt2/jb/.installed_usbl8r
/bin/chmod 0644 /mnt2/jb/.installed_usbl8r

/bin/mkdir -p /mnt2/log
if test ! -e /mnt2/log/lastlog; then : > /mnt2/log/lastlog; fi
test -f /mnt2/log/lastlog && test ! -L /mnt2/log/lastlog
/usr/sbin/chown 0:0 /mnt2/log/lastlog
/bin/chmod 0644 /mnt2/log/lastlog
/bin/sync
""")

    verification = usb.script(f"""
set -eu
test -L {q(env_link)}
test "$(/usr/bin/stat -f %Y {q(env_link)})" = {q(env_target)}
test "$(/usr/bin/stat -f %u /mnt2/jb/var/lib/apt/sileolists)" = 501
test "$(/usr/bin/stat -f %u /mnt2/jb/var/lib/apt/sileolists/partial)" = 501
test "$(/usr/bin/stat -f %u /mnt2/jb/var/cache/apt/archives)" = 501
test "$(/usr/bin/stat -f %u /mnt2/jb/var/cache/apt/archives/partial)" = 501
test -f /mnt2/jb/.installed_usbl8r
test "$(/usr/bin/stat -f %Lp /mnt2/log/lastlog)" = 644
printf 'ROOTLESS_RUNTIME_COMPAT_OK\n'
""")
    if verification.strip() != "ROOTLESS_RUNTIME_COMPAT_OK":
        raise RuntimeError("rootless runtime compatibility verification failed")
    print("[+] Sileo directories, /usr/bin/env compatibility and lastlog verified",
          flush=True)


def install_screentime_overrides(usb: USBSSH, ticket_id: str) -> None:
    """Disable the five Setup-blocking ScreenTime launchd jobs on Data."""
    remote = SCREENTIME_DISABLED_PATH
    backup = f"/mnt2/xra1n-backup/{ticket_id}/disabled.plist.before-screentime"
    stage = f"/mnt2/xra1n-stage/{ticket_id}/disabled.plist.screentime"

    exists = usb.script(f"if test -f {q(remote)}; then echo YES; else echo NO; fi").strip() == "YES"
    original = usb.read(remote) if exists else None
    if original is None:
        values: dict[str, object] = {}
    else:
        try:
            values = plistlib.loads(original)
        except Exception as error:
            raise RuntimeError(f"launchd disabled.plist is invalid: {error}") from error
        if not isinstance(values, dict):
            raise RuntimeError("launchd disabled.plist root is not a dictionary")

    changed = [label for label in SCREENTIME_LABELS if values.get(label) is not True]
    for label in SCREENTIME_LABELS:
        values[label] = True
    patched = plistlib.dumps(values, fmt=plistlib.FMT_BINARY, sort_keys=True)
    verified = plistlib.loads(patched)
    if any(verified.get(label) is not True for label in SCREENTIME_LABELS):
        raise RuntimeError("failed to construct ScreenTime disabled overrides")

    usb.script(f"/bin/mkdir -p {q(str(Path(remote).parent))} "
               f"{q(str(Path(backup).parent))} {q(str(Path(stage).parent))}")
    backup_exists = usb.script(
        f"if test -e {q(backup)}; then echo YES; else echo NO; fi"
    ).strip() == "YES"
    if original is not None and backup_exists:
        try:
            saved = plistlib.loads(usb.read(backup))
        except Exception as error:
            raise RuntimeError(f"existing ScreenTime override backup is invalid: {error}") from error
        if not isinstance(saved, dict):
            raise RuntimeError("existing ScreenTime override backup root is not a dictionary")
    elif original is not None:
        usb.call(f"/bin/cat > {q(backup)}", input_data=original)
        if usb.read(backup) != original:
            raise RuntimeError("ScreenTime override backup readback mismatch")

    if not changed and original == patched:
        print("[+] ScreenTime/FamilyControls launchd overrides already enabled", flush=True)
        return

    usb.call(f"/bin/cat > {q(stage)}", input_data=patched)
    if usb.read(stage) != patched:
        raise RuntimeError("staged ScreenTime override readback mismatch")
    try:
        usb.script(f"""
set -eu
/usr/sbin/chown 0:0 {q(stage)}
/bin/chmod 0644 {q(stage)}
/bin/mv -f {q(stage)} {q(remote)}
/bin/sync
""")
        installed = usb.read(remote)
        parsed = plistlib.loads(installed)
        if installed != patched or any(parsed.get(label) is not True for label in SCREENTIME_LABELS):
            raise RuntimeError("installed ScreenTime overrides failed readback verification")
    except Exception:
        if original is not None:
            usb.call(f"/bin/cat > {q(remote)}", input_data=original)
        else:
            usb.call(f"/bin/rm -f {q(remote)}")
        raise
    print(f"[+] disabled {len(SCREENTIME_LABELS)} ScreenTime/FamilyControls launchd jobs "
          f"({len(changed)} changed)", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true",
                        help="write Procursus, XR patches/runtime fixes, CommCenter, signed Setup and Sileo")
    parser.add_argument("--ssh-public-key", type=Path,
                        help="use this tester-owned ECDSA P-256 public key instead of generating one")
    args = parser.parse_args()
    configure_host_tools()
    ensure_install_bundle()
    patches = offline_preflight()
    if not args.install:
        print("[+] offline preflight only; pass --install after booting SSHRD", flush=True)
        return
    patches["authorized_keys"] = (validate_ssh_public_key(args.ssh_public_key.read_bytes())
                                  if args.ssh_public_key is not None else normal_ssh_public_key())
    ticket = selected_ticket()
    ticket_id = "22H374-" + hashlib.sha384(ticket.read_bytes()).hexdigest().upper()[:24]
    usb = USBSSH(ROOT / "logs/install" / ticket_id)
    try:
        usb.start()
        print(usb.script(MOUNT_SCRIPT), end="", flush=True)
        install_bootstrap(usb, ticket_id)
        install_rootless_runtime_compat(usb)
        install_daemons(usb, ticket_id, patches)
        install_commcenter(usb, ticket_id, patches["CommCenter"])
        install_pairing_and_launchd_hook(usb, ticket_id, patches)
        install_guarded_tweak_stack(usb, ticket_id, patches)
        install_xr_runtime_fixes(usb, ticket_id, patches)
        install_normal_ssh(usb, ticket_id, patches)
        install_setup(usb, ticket_id, patches["Setup"])
        install_staged_system_apps(usb, ticket_id)
        install_jbboot(usb, ticket_id, patches["xra1n-jbboot"])
        install_sileo(usb, ticket_id, patches["Sileo.app.tar.gz"])
        install_screentime_overrides(usb, ticket_id)
        print(f"[+] complete; on-device original backups: /mnt2/xra1n-backup/{ticket_id}")
        print("[+] ScreenTime jobs disabled through Data's launchd override plist")
        print("[+] xra1n-jbboot and its launchd-cache job are verified")
        print("[+] normal-boot key-only Dropbear and pairing fallback are verified")
        print("[+] guarded lhookctl and signed ElleKit are verified; injection remains disabled")
        print("[+] PhotoForce and three filtered XR tweaks installed; persona/LocalAuthentication/Files markers verified, lhook disabled")
        print("[+] Liter8-compatible Sileo is installed and verified")
        print("[+] signed three-gate CommCenter cellular patch is installed and verified")
        print("[+] rootless /usr/bin/env, Sileo state directories and lastlog are verified")
        print("[+] staged Apple apps copied; jbboot will register them once on normal boot")
        print("[+] patched launchd/lhook and both jbboot/Dropbear cache jobs are installed; prep_bootstrap.sh was not run")
    finally:
        usb.close()


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired) as error:
        raise SystemExit(f"[!] {error}") from error
