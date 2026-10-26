# XR 22H374 post-SSHRD installation (local Linux)

Run only after the patched erase restore completes, produces
`tickets/restore-complete.json`, and SSHRD boots with that same AP ticket.
This is separate from `run_restore.py` and `run_sshrd_boot.py`.

## One-script path

```sh
cd /Project-Cascadia/XRa1n
python3 install_sshrd_userland.py            # offline hashes/signatures only
python3 -u install_sshrd_userland.py --install
```

If the install bundle directory is missing, the installer restores it from
`dist/install-bundle-iphone11.8-22H374.tar.gz` and verifies the pinned archive
and every bundled file before any device action. The normal-boot finalizer
uses the same local-only check. No download or separate staging command is
required. The release archive contains no SSH client key. On `--install`, the
installer generates an ECDSA P-256 key pair at `~/.ssh/xra1n-xr` if absent,
checks that an existing pair matches, and installs only its public half on the
device. The private key stays on the host. Set `XRA1N_SSH_KEY` to choose a
different private-key path, or pass `--ssh-public-key path.pub` to use an
existing tester-owned public key without generating a pair. `tools/ssh-normal.sh`
uses the same default private-key path and environment override.

The installer stops stale `idevicesyslog`/`iproxy`, restarts foreground
`usbmuxd`, detects the ramdisk's `SSHRD_Script` USB identifier, then starts
identifier-bound `iproxy -u ... 2222:22`. It uses local `sshpass` and the SSHRD `root/alpine`
login; no device pairing or host backup is required. Logs go under
`logs/install/22H374-<ticket-hash>/`.

The script discovers APFS devices by their **System**, **Data**, and
**Preboot** roles, mounts `/mnt1` System read-write, `/mnt2` Data read-write,
and `/mnt6` Preboot read-only, then confirms an actual System write/delete.
On this previously tested XR those roles were `disk1s1`, `disk1s2`, and
`disk1s6`, but the installer does not assume those numbers.

For manual inspection on that same observed layout:

```sh
/sbin/mount_apfs /dev/disk1s1 /mnt1
/sbin/mount -u -o rw /dev/disk1s1
/sbin/mount_apfs /dev/disk1s2 /mnt2
/sbin/mount_apfs -o rdonly /dev/disk1s6 /mnt6
/sbin/mount | grep -E ' on /mnt(1|2|6) '
```

The installer SHA-256 checks `payloads/bootstrap_1900.tar.zst`, stages its
`var/jb` tree on Data, verifies the extracted tree and atomically moves it to
`/mnt2/jb`. Existing unmanaged `/mnt2/jb` contents cause a stop, not an
overwrite. It then verifies the three **pristine** on-device binaries,
copies only those originals to `/mnt2/xra1n-backup/22H374-<ticket-hash>/`,
checks each backup by readback, stages the signed patched artifacts on Data,
rewrites the existing System inodes, and reads back all installed hashes.
An immediate transfer/readback failure attempts to restore changed binaries
from those on-device backups. No original is downloaded to Linux. It also
verifies `bins/Setup.patched.signed` against the workflow artifact hash,
its CodeDirectory and all 58 XR patch targets. The pristine on-device Setup
executable is backed up to the same Data directory as `Setup`; the signed
copy is staged on Data, written through the existing System inode, and read
back. A Setup write/readback failure restores that pristine backup.

The installer also verifies the filtered runtime payloads and creates
root-owned, mode-0600 markers for `l8persona` (`/mnt2/jb/.liter8-persona`),
`l8localauth` (`/mnt2/jb/.liter8-localauth`) and the Files fix. This does
**not** enable lhook in SSHRD or normal iOS or restart any daemon. After
manually enabling lhook on normal boot, restart `installd` and
`installcoordinationd` before testing App Store installs, and `nehelper`
before testing VPN-profile approval.

The only replaced System paths are:

- `/mnt1/System/Library/Frameworks/LocalAuthentication.framework/Support/coreauthd`
- `/mnt1/System/Library/Frameworks/CryptoTokenKit.framework/ctkd`
- `/mnt1/usr/libexec/mobileactivationd`
- `/mnt1/Applications/Setup.app/Setup`

The current installer also stages the reviewed pairing, launchd, Sileo and
runtime fixes. It deliberately does **not** run `prep_bootstrap.sh`, register
booted-userland packages, enable lhook, or restart running daemons. Run the
normal-boot finalizer separately after the tethered iOS boot.
