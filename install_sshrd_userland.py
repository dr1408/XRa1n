#!/usr/bin/env python3
"""Install Procursus, XR userland patches, and Setup overrides over SSHRD SSH.

Default is an offline local-artifact check. --install performs device writes.
Original executables are backed up ONLY on the device's Data volume.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import plistlib
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

from boot_stage import selected_ticket

ROOT = Path(__file__).resolve().parent
ARCHIVE = ROOT / "payloads/bootstrap_1900.tar.zst"
ARTIFACTS = ROOT / "userland-output-22H374"
USBMUXD = Path("/usr/sbin/usbmuxd")
IPROXY = Path("/picotool/spironolactone/Linux/aarch64/iproxy")
SSHPASS = Path("/picotool/spironolactone/Linux/aarch64/sshpass")
BOOTSTRAP_SHA256 = "8354c3aa1ecdad8ebc47d9a76dfca6f830a2b757278068bd33b98bf1d638a9cb"
SETUP_PATH = "/mnt1/Applications/Setup.app/Setup"
SETUP_PRISTINE_SHA256 = "595a9e839dcdba3dbaa93f74e63627f984ab56537ce6687a1a1c9a633338344d"
SETUP_SIGNED_SHA256 = "68c7b8c1db70763465996ac3e152d8a92cd175e60b46523997caddbe33b8880f"
SETUP_SIGNED = ROOT / "bins/Setup.patched.signed"
JBBOOT_ARTIFACT = (ROOT / "work/artifacts/xra1n-jbboot" /
                   "90db8e40336e8a9c4a7f3527774f2a0e60b74104/xra1n-jbboot")
JBBOOT_SHA256 = "c82fa988cd501eb007aab668f20b8f320ab66e052187ebb48896c97915198d5b"
JBBOOT_UPGRADABLE_SHA256 = {
    "8d6f719206c8245b9ce1364fec0877b6503aa5f1c2dc803c7288badda714e1bb",
    "42fd6bca0327fc7759768382e325e4a9564b044fac3d0f9c1be9c4f052a28088",
    "7fcaa73960ba36bacfa3e99eed5e4ef834f737629dc7c3f18a2b5f041287dd30",
}
JBBOOT_PATH = "/mnt1/usr/local/bin/xra1n-jbboot"
SILEO_ARTIFACT = (ROOT / "work/artifacts/xra1n-jbboot" /
                  "3b1876546cd7d42e5e9f68e96964f6494fdea0b1/Sileo.app.tar.gz")
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
    "POSIXSpawnType": "Interactive",
    "EnablePressuredExit": False,
    "EnableTransactions": False,
    "StandardOutPath": "/var/mobile/xra1n-jbboot.log",
    "StandardErrorPath": "/var/mobile/xra1n-jbboot.log",
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
/sbin/mount_apfs "$system" /mnt1
/sbin/mount -u -o rw "$system"
/sbin/mount_apfs "$data" /mnt2
/sbin/mount_apfs -o rdonly "$preboot" /mnt6
for target in /mnt1 /mnt2 /mnt6; do
    /sbin/mount | /usr/bin/grep " on $target "
done
testfile=/mnt1/Applications/.xra1n-rw-$$
: > "$testfile" && /bin/rm "$testfile"
printf 'MOUNTS_AND_SYSTEM_RW_OK\n'
'''


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def q(value: str | Path) -> str:
    return shlex.quote(str(value))


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
        pristine = ROOT / "bins" / name
        patched = ARTIFACTS / "daemons" / name
        if digest(pristine.read_bytes()) != pristine_hash:
            raise RuntimeError(f"local pristine {name} hash mismatch")
        data = patched.read_bytes()
        if digest(data) != sums.get(f"daemons/{name}") or digest(data) == pristine_hash:
            raise RuntimeError(f"patched {name} hash mismatch")
        patches[name] = data
    if digest((ROOT / "bins/Setup.app/Setup").read_bytes()) != SETUP_PRISTINE_SHA256:
        raise RuntimeError("local pristine Setup hash mismatch")
    setup = SETUP_SIGNED.read_bytes()
    if digest(setup) != SETUP_SIGNED_SHA256:
        raise RuntimeError("signed Setup artifact hash mismatch")
    patches["Setup"] = setup
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
    subprocess.run([sys.executable, str(ROOT / "verify_userland_signature.py"),
                    *(str(ARTIFACTS / "daemons" / name) for name in TARGETS),
                    str(SETUP_SIGNED), str(JBBOOT_ARTIFACT)], check=True)
    subprocess.run([sys.executable, str(ROOT / "patch_setup_xr.py"),
                    str(SETUP_SIGNED), "--verify", "--expect-count", "58"],
                   check=True, stdout=subprocess.DEVNULL)
    for tool in (USBMUXD, IPROXY, SSHPASS):
        if not tool.is_file():
            raise RuntimeError(f"missing USB/SSH tool: {tool}")
    print("[+] offline Procursus, daemon, Setup, jbboot and Sileo artifacts verified",
          flush=True)
    return patches


def build_jbboot_cache(source: bytes) -> bytes:
    """Add only the proven one-shot jbboot job to a Version 7 launchd cache."""
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
    patched = plistlib.dumps(document, fmt=plistlib.FMT_BINARY, sort_keys=False)
    verified = plistlib.loads(patched)
    if verified["LaunchDaemons"].get(JBBOOT_CACHE_KEY) != JBBOOT_JOB:
        raise RuntimeError("constructed launchd cache failed jbboot verification")
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
        result = self.call("/bin/sh -s", input_data=script.encode())
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
    if all(current[name] == digest(patches[name]) for name in TARGETS):
        for name, (_, pristine_hash) in TARGETS.items():
            if digest(usb.read(f"{backup}/{name}")) != pristine_hash:
                raise RuntimeError(f"already-patched {name} lacks a pristine on-device backup")
        print("[+] three daemons already match signed patches; pristine backups verified", flush=True)
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
    expected_count = 630 if current_job is not None else 629
    if len(jobs) != expected_count:
        raise RuntimeError(
            f"launchd cache has {len(jobs)} jobs; expected {expected_count} for XR 22H374"
        )

    patched_cache = build_jbboot_cache(original_cache)
    if original_helper == helper and patched_cache == original_cache:
        print("[+] xra1n-jbboot executable and launchd-cache job already match", flush=True)
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
            if mode != b"4755":
                usb.script(f"/bin/chmod 4755 {q(SILEO_PATH + '/giveMeRoot')}")
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
chmod 4755 {q(unpack + '/Sileo.app/giveMeRoot')}
rm -rf {q(SILEO_PATH)}
mv {q(unpack + '/Sileo.app')} {q(SILEO_PATH)}
chown -R 0:0 {q(SILEO_PATH)}
chmod 4755 {q(SILEO_PATH + '/giveMeRoot')}
/bin/sync
""")
        if digest(usb.read(SILEO_PATH + "/Sileo")) != SILEO_BINARY_SHA256:
            raise RuntimeError("installed Sileo executable readback mismatch")
        if digest(usb.read(SILEO_PATH + "/giveMeRoot")) != SILEO_HELPER_SHA256:
            raise RuntimeError("installed Sileo helper readback mismatch")
        mode = usb.call(f"/usr/bin/stat -f %Lp {q(SILEO_PATH + '/giveMeRoot')}").stdout.strip()
        if mode != b"4755":
            raise RuntimeError(f"installed giveMeRoot mode is {mode!r}, expected 4755")
    except Exception:
        usb.script(f"/bin/rm -rf {q(SILEO_PATH)}")
        if current_exists:
            usb.script(f"/bin/cp -Rp {q(backup)} {q(SILEO_PATH)}")
        raise

    print("[+] installed and verified Liter8-compatible Sileo.app", flush=True)


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
for app in "$source"/*.app; do
    test -d "$app" || continue
    name=${{app##*/}}
    if test ! -e "$destination/$name"; then
        /bin/cp -Rp "$app" "$destination/$name"
        /usr/sbin/chown -R 0:0 "$destination/$name"
        printf '%s\n' "$name" >> {q(record + '.tmp')}
    fi
done
/usr/bin/sort -o {q(record + '.tmp')} {q(record + '.tmp')}
/bin/mv -f {q(record + '.tmp')} {q(record)}
/usr/sbin/chown 0:0 {q(record)}
/bin/chmod 0644 {q(record)}
count=$(/usr/bin/wc -l < {q(record)} | /usr/bin/tr -d ' ')
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
    /usr/bin/readlink {q(env_link)}
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
test "$(/usr/bin/readlink {q(env_link)})" = {q(env_target)}

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
test "$(/usr/bin/readlink {q(env_link)})" = {q(env_target)}
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
                        help="write Procursus, XR patches, signed Setup and Sileo")
    args = parser.parse_args()
    patches = offline_preflight()
    if not args.install:
        print("[+] offline preflight only; pass --install after booting SSHRD", flush=True)
        return
    ticket = selected_ticket()
    ticket_id = "22H374-" + hashlib.sha384(ticket.read_bytes()).hexdigest().upper()[:24]
    usb = USBSSH(ROOT / "logs/install" / ticket_id)
    try:
        usb.start()
        print(usb.script(MOUNT_SCRIPT), end="", flush=True)
        install_bootstrap(usb, ticket_id)
        install_rootless_runtime_compat(usb)
        install_daemons(usb, ticket_id, patches)
        install_setup(usb, ticket_id, patches["Setup"])
        install_staged_system_apps(usb, ticket_id)
        install_jbboot(usb, ticket_id, patches["xra1n-jbboot"])
        install_sileo(usb, ticket_id, patches["Sileo.app.tar.gz"])
        install_screentime_overrides(usb, ticket_id)
        print(f"[+] complete; on-device original backups: /mnt2/xra1n-backup/{ticket_id}")
        print("[+] ScreenTime jobs disabled through Data's launchd override plist")
        print("[+] xra1n-jbboot and its launchd-cache job are verified")
        print("[+] Liter8-compatible Sileo is installed and verified")
        print("[+] rootless /usr/bin/env, Sileo state directories and lastlog are verified")
        print("[+] staged Apple apps copied; jbboot will register them once on normal boot")
        print("[+] No launchd binary, dylib, or prep_bootstrap.sh action was performed")
    finally:
        usb.close()


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired) as error:
        raise SystemExit(f"[!] {error}") from error
