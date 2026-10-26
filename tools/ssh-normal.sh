#!/usr/bin/env bash
# Open key-only root SSH to the normally booted XR through usbmuxd.

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
KEY=${XRA1N_SSH_KEY:-"$HOME/.ssh/xra1n-xr"}
PORT=${XRA1N_SSH_PORT:-2222}

die() {
    printf '[!] %s\n' "$*" >&2
    exit 1
}

HOST_DIR=${XRA1N_TOOLS_DIR:-"$ROOT/tools/host/linux-aarch64"}
if [[ -d "$HOST_DIR" ]]; then
    python3 "$ROOT/host_tools.py" --check >/dev/null || die "host-tool bundle verification failed"
    HOST_DIR=$(cd "$HOST_DIR" && pwd)
    IPROXY="$HOST_DIR/iproxy"
    USBMUXD="$HOST_DIR/usbmuxd"
    IDEVICE_ID="$HOST_DIR/idevice_id"
    IDEVICEPAIR="$HOST_DIR/idevicepair"
elif [[ -n ${XRA1N_TOOLS_DIR:-} ]]; then
    die "XRA1N_TOOLS_DIR does not exist: $HOST_DIR"
else
    IPROXY=$(command -v iproxy) || die "missing iproxy"
    USBMUXD=$(command -v usbmuxd) || die "missing usbmuxd"
    IDEVICE_ID=$(command -v idevice_id) || die "missing idevice_id"
    IDEVICEPAIR=$(command -v idevicepair) || die "missing idevicepair"
fi

[[ -f "$KEY" ]] || die "missing client key: $KEY (run the SSHRD installer first or set XRA1N_SSH_KEY)"
[[ -x "$IPROXY" && -x "$USBMUXD" ]] || die "missing bundled iproxy/usbmuxd"

# Remove stale readers/proxies, then give this session a fresh usbmuxd.
pkill -9 idevicesyslog >/dev/null 2>&1 || true
pkill -9 iproxy >/dev/null 2>&1 || true
pkill -9 usbmuxd >/dev/null 2>&1 || true
sleep 1

"$USBMUXD" -pf >/tmp/xra1n-usbmuxd-normal.log 2>&1 &
USBMUXD_PID=$!
sleep 2

UDID=$("$IDEVICE_ID" -l 2>/dev/null | head -1 || true)
[[ -n "$UDID" ]] || die "no normally booted iPhone visible to usbmuxd"
printf '[+] device: %s\n' "$UDID"

if ! "$IDEVICEPAIR" validate; then
    "$IDEVICEPAIR" pair || die "pairing failed; unlock the phone and tap Trust"
fi

"$IPROXY" -u "$UDID" "$PORT:22" >/tmp/xra1n-iproxy-normal.log 2>&1 &
IPROXY_PID=$!
cleanup() {
    kill "$IPROXY_PID" >/dev/null 2>&1 || true
    # Keep the fresh usbmuxd alive for subsequent idevice commands.
    disown "$USBMUXD_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM
sleep 2
kill -0 "$IPROXY_PID" 2>/dev/null || \
    die "iproxy exited; inspect /tmp/xra1n-iproxy-normal.log"

SSH_OPTIONS=(
    -F /dev/null
    -i "$KEY"
    -o IdentitiesOnly=yes
    -o Tunnel=no
    -o ConnectTimeout=10
    -o ServerAliveInterval=5
    -o ServerAliveCountMax=6
    -o StrictHostKeyChecking=no
    -o UserKnownHostsFile=/dev/null
    -o LogLevel=ERROR
    -o HostKeyAlgorithms=+ecdsa-sha2-nistp521
    -o Ciphers=+aes128-ctr
    -p "$PORT"
)

if (( $# )); then
    ssh -T "${SSH_OPTIONS[@]}" root@localhost "$@"
else
    printf '[+] opening root SSH on localhost:%s\n' "$PORT"
    ssh "${SSH_OPTIONS[@]}" root@localhost
fi
