#!/usr/bin/env python3
"""Liter8-compatible erase restore with serial and APTicket capture."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import shutil
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from host_tools import ca_bundle, configure as configure_host_tools, tool as host_tool

ROOT = Path(__file__).resolve().parent
LITER8_SCRIPTS = ROOT / "scripts" / "liter8"
sys.path.insert(0, str(LITER8_SCRIPTS))

from apticket import TICKET_NAME, tickets_from_debug_log, validate_im4m  # noqa: E402


def recovery_query(irecovery: str = "irecovery") -> str | None:
    """Return irecovery's query output when recovery is actually responsive.

    PyUSB can retain the DFU device in its process-local context across the
    iBSS handoff.  Treating that stale view as authoritative caused the first
    ticket attempt to time out even though 05ac:1281 was already present.
    A successful recovery protocol query is the definitive readiness test.
    """
    try:
        result = subprocess.run(
            [irecovery, "-q"], capture_output=True, text=True, timeout=3
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = result.stdout + result.stderr
    return output if result.returncode == 0 and "MODE: Recovery" in output else None


def wait_for_recovery(timeout: float, irecovery: str = "irecovery") -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # The protocol check also prevents starting idevicerestore during the
        # short interval where USB has enumerated but iBoot is not ready yet.
        query = recovery_query(irecovery)
        if query is not None:
            return query
        time.sleep(0.2)
    raise RuntimeError(f"device did not enter recovery within {timeout:.0f} seconds")


def ecid_from_query(query: str) -> str:
    for line in query.splitlines():
        if line.startswith("ECID:"):
            value = line.split(":", 1)[1].strip().lower().removeprefix("0x")
            if value and all(character in "0123456789abcdef" for character in value):
                return value
    raise RuntimeError("irecovery did not report a valid ECID")


def wait_for_proxy(process: subprocess.Popen, ready_file: Path) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"TSS proxy exited early with status {process.returncode}")
        if ready_file.is_file():
            try:
                ready = json.loads(ready_file.read_text())
            except (OSError, ValueError):
                ready = None
            if isinstance(ready, dict) and ready.get("pid") == process.pid:
                port = ready.get("port")
                if isinstance(port, int) and 0 < port < 65536:
                    try:
                        with urllib.request.urlopen(
                            f"http://127.0.0.1:{port}/health", timeout=2
                        ) as response:
                            if response.status == 200 and response.read() == b"ok\n":
                                return ready
                    except OSError:
                        pass
        time.sleep(0.05)
    raise RuntimeError("TSS proxy did not become ready")


def require_file(path: Path, description: str) -> Path:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise RuntimeError(f"missing {description}: {path}")
    return path


def verify_tss_tls(ca_bundle: Path) -> None:
    """Fail before iBSS handoff if Apple TSS DNS/TLS is unavailable."""
    context = ssl.create_default_context(cafile=str(ca_bundle))
    try:
        with socket.create_connection(("gs.apple.com", 443), timeout=8) as connection:
            with context.wrap_socket(connection, server_hostname="gs.apple.com"):
                pass
    except (OSError, ssl.SSLError) as error:
        raise RuntimeError(f"Apple TSS TLS preflight failed: {error}") from error
    print("[+] Apple TSS DNS/TLS verified with supplied CA bundle", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("restore_source", type=Path,
                        help="complete IPSW file or prepared CFW directory")
    parser.add_argument("--ibss", type=Path,
                        default=ROOT / "work/restore/iBSS.restore.raw.patched")
    parser.add_argument("--idevicerestore", type=Path,
                        default=host_tool("idevicerestore"))
    parser.add_argument("--usbliter8ctl", type=Path,
                        default=host_tool("usbliter8ctl"))
    parser.add_argument("--profile-id", default="iphone11,8-n841ap-22H374")
    parser.add_argument("--expected-ecid", required=True,
                        help="ECID pinned by the runner before the iBSS handoff")
    parser.add_argument("--ca-bundle", type=Path,
                        default=ca_bundle())
    parser.add_argument("--recovery-timeout", type=float, default=30)
    parser.add_argument("--irecovery", default="irecovery")
    parser.add_argument("--skip-pico-boot", action="store_true",
                        help="device is already in the required patched recovery stage")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--ticket-only", action="store_true",
                      help="capture a fresh ticket but never let idevicerestore upload stock iBEC")
    mode.add_argument("--restore-only", action="store_true",
                      help="run idevicerestore after restore OS boots, retaining its saved AP ticket")
    args = parser.parse_args()
    configure_host_tools()

    source = args.restore_source.expanduser().resolve()
    if not source.exists():
        raise RuntimeError(f"restore source does not exist: {source}")
    ibss = None if args.restore_only else require_file(args.ibss, "patched restore iBSS")
    idevicerestore = require_file(args.idevicerestore, "idevicerestore")
    usbliter8ctl = None if args.restore_only else require_file(args.usbliter8ctl, "usbliter8ctl")
    proxy_script = require_file(LITER8_SCRIPTS / "tss_proxy.py", "Liter8 TSS proxy")
    ca_path = require_file(args.ca_bundle, "Apple TSS CA bundle")
    verify_tss_tls(ca_path)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    restore_log = ROOT / "logs" / "restore" / f"restore-{timestamp}.log"
    personalized_dir = ROOT / "logs" / "restore" / f"personalized-{timestamp}"
    proxy_log = ROOT / "logs" / "tss" / f"tss-proxy-{timestamp}.log"
    ticket_dir = ROOT / "tickets"
    for directory in (restore_log.parent, proxy_log.parent, ticket_dir):
        directory.mkdir(parents=True, exist_ok=True)
    personalized_dir.mkdir(exist_ok=False)
    (ROOT / ".run").mkdir(parents=True, exist_ok=True)

    # A prior ticket must never satisfy the capture loop for a new nonce, but
    # it may be the ticket installed in Preboot by a completed erase restore.
    # Archive by content before clearing the mutable capture slot.
    prior_ticket = ticket_dir / TICKET_NAME
    saved_ticket_hash = None
    if args.restore_only:
        if not prior_ticket.is_file():
            raise RuntimeError("restore OS has no saved AP ticket")
        validate_im4m(prior_ticket.read_bytes())
        saved_ticket_hash = hashlib.sha256(prior_ticket.read_bytes()).hexdigest()
    if prior_ticket.is_file():
        data = prior_ticket.read_bytes()
        validate_im4m(data)
        archive = ticket_dir / "archive"
        archive.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha384(data).hexdigest().upper()
        archived_ticket = archive / f"{key}.im4m"
        if archived_ticket.is_file() and archived_ticket.read_bytes() != data:
            raise RuntimeError(f"ticket archive collision: {archived_ticket}")
        if not archived_ticket.is_file():
            shutil.copy2(prior_ticket, archived_ticket)
        print(f"[+] preserved prior AP ticket: {archived_ticket}", flush=True)
    if not args.restore_only:
        for stale in (ticket_dir / TICKET_NAME, ticket_dir / "apticket.json"):
            stale.unlink(missing_ok=True)
    # Capturing a new AP ticket does not install a matching Preboot tree.
    (ticket_dir / "restore-complete.json").unlink(missing_ok=True)

    with tempfile.TemporaryDirectory(prefix="xra1n-tss-", dir=ROOT / ".run") as temp:
        ready_file = Path(temp) / "ready.json"
        with proxy_log.open("wb") as proxy_output:
            proxy_command = [sys.executable, "-u", str(proxy_script),
                 "--bind", "127.0.0.1", "--port", "0",
                 "--ready-file", str(ready_file),
                 "--profile-id", args.profile_id,
                 "--ca-bundle", str(ca_path)]
            if args.restore_only:
                proxy_command += ["--expected-ticket-sha256", saved_ticket_hash]
            else:
                proxy_command += ["--ticket-directory", str(ticket_dir)]
            if args.ticket_only:
                proxy_command.append("--capture-only")
            proxy = subprocess.Popen(
                proxy_command,
                stdout=proxy_output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                ready = wait_for_proxy(proxy, ready_file)
                port = ready.get("port")
                if not isinstance(port, int) or not 0 < port < 65536:
                    raise RuntimeError("TSS proxy published an invalid port")
                tss_url = f"http://127.0.0.1:{port}"
                print(f"[+] TSS proxy: {tss_url}")
                print(f"[+] TSS log: {proxy_log}")
                print(f"[+] restore log: {restore_log}")

                if args.restore_only:
                    ecid = args.expected_ecid.removeprefix("0x")
                    print("[+] using already-booted restore OS; no iBSS/iBEC upload", flush=True)
                elif not args.skip_pico_boot:
                    print(f"[*] Pico USBLiter8 boot: {ibss}", flush=True)
                    # The RP2350 hub can expose 05ac:1227 a fraction before
                    # PyUSB can open it. Retry only while recovery has not
                    # appeared; never resend iBSS after a successful handoff.
                    result = None
                    for attempt in range(1, 6):
                        if recovery_query(args.irecovery) is not None:
                            break
                        result = subprocess.run([str(usbliter8ctl), "boot", str(ibss)])
                        if result.returncode == 0:
                            break
                        print(
                            f"[*] USBLiter8 open/transition attempt {attempt}/5 failed; retrying",
                            flush=True,
                        )
                        time.sleep(1)
                    if result is not None and result.returncode:
                        # A nonzero final status is acceptable only if CUSTOM_BOOT
                        # already made recovery responsive.
                        if recovery_query(args.irecovery) is None:
                            raise RuntimeError("USBLiter8 could not send patched iBSS after 5 attempts")
                        print("[*] transport returned during successful USB transition", flush=True)
                    query = wait_for_recovery(args.recovery_timeout, args.irecovery)
                    print("[+] patched iBSS reached recovery", flush=True)
                elif not args.restore_only:
                    query = wait_for_recovery(args.recovery_timeout, args.irecovery)

                if not args.restore_only:
                    ecid = ecid_from_query(query)
                    if int(ecid, 16) != int(args.expected_ecid, 0):
                        raise RuntimeError(
                            f"recovery ECID 0x{ecid} is not target {args.expected_ecid}"
                        )
                    print(f"[+] responsive recovery ECID: 0x{ecid}", flush=True)

                command = [
                    str(idevicerestore), "-d", "--logfile", str(restore_log),
                    "--keep-pers", "-s", tss_url, "-i", f"0x{ecid}", "-e", "-y", str(source),
                ]
                if args.restore_only:
                    # Without -R idevicerestore attempts to leave Restore mode
                    # and re-run its recovery handoff instead of continuing.
                    # Restore mode has no AP/SEP nonce query. Reuse the exact
                    # ticket that booted this restore OS rather than asking
                    # TSS for a new nonce-bound AP ticket from this mode.
                    command[1:1] = ["-R", "-T", str(prior_ticket)]
                print(f"[+] personalized components will be saved in: {personalized_dir}", flush=True)
                print("[*] capturing AP ticket only; restore upload and erase are blocked" if args.ticket_only
                      else "[*] starting erase restore", flush=True)
                if args.ticket_only:
                    ticket = ticket_dir / TICKET_NAME
                    for attempt in range(1, 4):
                        wait_for_recovery(args.recovery_timeout, args.irecovery)
                        restore = subprocess.Popen(command, cwd=personalized_dir, start_new_session=True)
                        try:
                            deadline = time.monotonic() + 90
                            while time.monotonic() < deadline and not ticket.is_file():
                                if restore.poll() is not None:
                                    break
                                time.sleep(0.05)
                            if ticket.is_file():
                                break
                            print(f"[*] no AP ticket on capture attempt {attempt}/3 "
                                  f"(idevicerestore rc={restore.poll()}); "
                                  f"checking responsive recovery before retry", flush=True)
                        finally:
                            if restore.poll() is None:
                                os.killpg(restore.pid, signal.SIGTERM)
                                try:
                                    restore.wait(timeout=5)
                                except subprocess.TimeoutExpired:
                                    os.killpg(restore.pid, signal.SIGKILL)
                                    restore.wait()
                        if attempt < 3:
                            time.sleep(1)
                    if not ticket.is_file():
                        raise RuntimeError(
                            f"AP ticket capture failed after 3 tries; inspect {restore_log} "
                            f"and {proxy_log}; recovery itself was responsive"
                        )
                    print("[+] ticket captured; stock iBEC upload was blocked", flush=True)
                else:
                    # -k writes component files in the child's working
                    # directory. Keep them alongside this attempt's log.
                    result = subprocess.run(command, cwd=personalized_dir, start_new_session=True)
                    ibec = personalized_dir / "iBEC"
                    if ibec.is_file():
                        print(f"[+] uploaded personalized iBEC: {ibec} "
                              f"sha256={hashlib.sha256(ibec.read_bytes()).hexdigest()}", flush=True)
                    if result.returncode:
                        raise RuntimeError(f"idevicerestore exited with status {result.returncode}")
            finally:
                if proxy.poll() is None:
                    os.killpg(proxy.pid, signal.SIGTERM)
                    try:
                        proxy.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(proxy.pid, signal.SIGKILL)
                        proxy.wait()

    ticket = ticket_dir / TICKET_NAME
    if not ticket.is_file():
        raise RuntimeError("restore completed but the proxy did not capture apticket.im4m")
    ticket_bytes = ticket.read_bytes()
    validate_im4m(ticket_bytes)
    if args.restore_only and hashlib.sha256(ticket_bytes).hexdigest() != saved_ticket_hash:
        raise RuntimeError("saved AP ticket changed during restore")
    archive = ticket_dir / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha384(ticket_bytes).hexdigest().upper()
    archived_ticket = archive / f"{key}.im4m"
    if archived_ticket.is_file() and archived_ticket.read_bytes() != ticket_bytes:
        raise RuntimeError(f"ticket archive collision: {archived_ticket}")
    if not archived_ticket.is_file():
        shutil.copy2(ticket, archived_ticket)
    print(f"[+] archived captured AP ticket: {archived_ticket}", flush=True)
    logged = tickets_from_debug_log(restore_log.read_text(errors="replace"))
    if logged and logged != [ticket_bytes]:
        raise RuntimeError("proxy ticket and idevicerestore debug-log ticket differ")
    print(f"[+] validated APTicket: {ticket}")
    if args.ticket_only:
        print("[+] Liter8-compatible ticket-only capture completed")
    else:
        print("[+] erase restore and Liter8-compatible ticket capture completed")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError) as error:
        raise SystemExit(f"[!] {error}") from error
