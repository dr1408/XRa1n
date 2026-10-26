# XR normal tethered boot (22H374)

Normal boot is separate from erase-restore (`run_restore.py`) and SSHRD. The
local 22H374 BuildManifest maps `KernelCache` and `RestoreKernelCache` to the
same source `kernelcache.release.iphone11b`; it also maps both DeviceTree roles
to the same source. The **patch plans and boot arguments still differ**.

## Files

- `work/pristine-cfw/`: BuildManifest-verified stock iBSS, iBEC and DeviceTree
  saved before the erase-restore CFW is patched.
- `work/normal/`: normal-patched raw iBSS/iBEC/DeviceTree made from those
  pristine sources. No device access is needed.
- `work/normal-bootset/`: ticketed normal iBEC, firmware, DeviceTree, SEP and
  **the downloaded `kernelcache.patched-0x891-full-aks.im4p`**, with per-file hashes.
  No restore ramdisk is included.
- `logs/serial/dcsd-*.log` and `logs/normal/upload-*.log`: device-side and
  transfer-side evidence. The runner does not delete previous logs or tickets.

## Commands

After `run_restore.py --prepare-only` has saved the pristine sources, prepare
normal iBoot sources without touching the device or requiring a ticket:

```sh
python3 prepare_normal_bootset.py --sources-only
```

From USBLiter8-pwned DFU (`05ac:1227`):

```sh
python3 -u run_normal_boot.py --boot
```

The default omits `serial=3` and does not require DCSD hardware. If a DCSD
logger is connected on `/dev/ttyACM0`, run
`python3 -u run_normal_boot.py --boot --serial` instead. That variant builds
separate serial-patched stages and checks the serial log for panics. Without
serial, image transfer is not proof of a completed boot; confirm the device
and SSH state.

With `--serial`, the runner starts DCSD. It sends **normal-mode** raw iBSS, reuses the AP ticket
from the completed patched erase restore without contacting TSS, builds and
verifies the normal IMG4 set, sends ticketed iBEC and `go`, then sends firmware,
normal-patched DeviceTree, SEP, the downloaded patched kernel, and `bootx`.
The iBEC/DeviceTree/kernel payloads and embedded tickets are byte-checked
before upload. `tickets/restore-complete.json` must match the selected ticket;
ticket capture alone does not prove that the matching Preboot tree was installed.

## Kernel USB policy patch

The downloaded kernel artifact contains `kernel.usb.restore-mode.*`. This is
**also part of Liter8's normal `boot-public` plan**: its
`KernelBootCompatibilityResolver` includes `KernelBootPolicyResolver`, whose
`restoreModeStub` reports restore mode to the USB Restricted Mode policy. It
does not set `rd=md0`, load a ramdisk, or itself choose the root filesystem.
The normal builder records the patch in `xra1n-boot.json` for verification.
The normal iBSS/iBEC boot arguments have no `rd=md0` or `-restore`, and the
normal transfer omits the ramdisk. With `--serial`, check DCSD for root selection and
launchd progress; successful `bootx` transfer alone is not boot confirmation.

The previous automatic runner only covered restore OS, and an ordinary local
reboot enters the persistent boot chain rather than this tethered iBSS/iBEC
chain. That is why previous recovery-mode observations did not test normal
`bootx` with the patched kernel.

## First device test: 2026-10-19 09:39 UTC

`logs/serial/dcsd-20261019-093949.log` and
`logs/normal/upload-20261019-093949.log` show a fresh-ticket normal boot.
The first `bgcolor` call raced USB re-enumeration after iBEC `go`; retrying it
from the same iBEC session worked. The runner now waits/retries automatically.
All firmware, DeviceTree, SEP, and the downloaded patched kernel transferred;
`bootx` returned 0. Device serial then showed `BSD root: disk0s1`, ignition
`restore: 0x0`, boot spec `local`, and launchd with normal `serial=3` args.
Thus iBEC and the patched kernel **did** reach normal userspace. The boot did
not complete: `init_data_protection` could not open
`/private/preboot/18376C0D.../usr/standalone/firmware/sep-firmware.img4`
(`ENOENT`), then `seputil[6]` exited status 5 and launchd triggered a kernel
panic. This is the next concrete investigation target; it is not evidence of
kernel-image rejection or ramdisk-root selection.

## Read-only Preboot inspection after first test

Booted the already-built SSHRD with a new ticket and mounted Preboot read-only
at `/mnt6`. `/mnt6/active` names
`4EF616888D5E8EC38A935B845743A5D2BDC8BC2043AB8A4E53D3916C48CEB8EFCE103BC30456ED1B380A14AE4F1A4243`.
That directory contains `usr/standalone/firmware/sep-firmware.img4`. The
normal boot instead requested Preboot directory
`18376C0DDABF204F0543CCE6DC0D4BC9F15B084FE950362E823C1569CBBDFA4CD083844230863AAB7D23679B80A6CDEB`,
which does not exist. The observed failure is therefore a selected Preboot
directory mismatch, not absence of the SEP image from all of Preboot. This
inspection made **no Preboot writes**. Do not rename or rewrite `active`
without determining which component generates the boot lookup key.

The installed `sep-firmware.img4` was copied locally for comparison. Its raw
SEP payload is byte-identical to the freshly ticketed SSHRD `SEP.img4` payload,
but its embedded IM4M ticket differs from the new capture. Blindly linking the
old Preboot directory into the missing path would therefore not establish a
nonce-matched firmware image. No on-device repair has been attempted.

## Root cause established: ticket-derived Preboot directory

Three independent SHA-384 checks identify the Preboot directory key as the
**exact AP IM4M ticket bytes**:

| IM4M source | SHA-384 | Observed location |
| --- | --- | --- |
| First normal boot, extracted from `work/normal-bootset/iBEC.img4` | `18376C0DDABF204F0543CCE6DC0D4BC9F15B084FE950362E823C1569CBBDFA4CD083844230863AAB7D23679B80A6CDEB` | Missing path in `seputil` failure |
| Installed SEP IMG4 in Preboot | `4EF616888D5E8EC38A935B845743A5D2BDC8BC2043AB8A4E53D3916C48CEB8EFCE103BC30456ED1B380A14AE4F1A4243` | Existing directory and contents of `/mnt6/active` |
| Current SSHRD `tickets/apticket.im4m` | `946ED6913AFB33FDC23DE7B89FEE13F783AE860011BDE5E9408AFA6991324A296BC25C2B5973C17C19B770D507E7411B` | IODeviceTree `/chosen/boot-manifest-hash` |

The active marker is a 96-byte text file containing the installed ticket's
SHA-384, not a symlink. The existing directory includes
`usr/standalone/firmware/{sep-firmware.img4,devicetree.img4,root_hash.img4}`
and `System/Library/Caches/apticket.der`. The ramdisk inspection mounted
Preboot read-only and made no on-device changes.

A new TSS ticket changes the expected Preboot path. Merely completing an erase
restore does not prove a later *fresh-ticket* tethered boot can find its SEP
image. A future fix must arrange a complete, ticket-consistent Preboot tree
for the exact normal-boot IM4M, or establish a demonstrably valid reusable
installed ticket. The installed-ticket reuse test below is prepared but not
yet tested on the device.

## Installed-ticket reuse test prepared

The installed IM4M was archived without changing `tickets/apticket.im4m` at
`tickets/archive/4EF616888D5E8EC38A935B845743A5D2BDC8BC2043AB8A4E53D3916C48CEB8EFCE103BC30456ED1B380A14AE4F1A4243.im4m`.
`run_normal_boot.py --ticket PATH` now skips fresh TSS capture and uses the
specified ticket. Without `--boot`, it builds and validates the bootset offline;
that check passed for the installed ticket, including embedded-ticket and
patched-kernel payload checks. It has **not** been accepted by the device yet;
current nonce validity still needs a tethered boot test from pwned DFU.

Future `restore_with_ticket.py` captures archive both the old and new ticket
under `tickets/archive/<SHA384>.im4m` rather than discarding the only copy.

## Device test with installed ticket: 2026-10-19 10:03 UTC

From USBLiter8-pwned DFU, `run_normal_boot.py --boot --ticket
tickets/archive/4EF616888D5E8EC38A935B845743A5D2BDC8BC2043AB8A4E53D3916C48CEB8EFCE103BC30456ED1B380A14AE4F1A4243.im4m`
sent normal iBSS/iBEC. The first GFX transfer lost USB at 28%; iBEC stayed
responsive in recovery and the same session resumed successfully from GFX.
The remaining firmware, DeviceTree, SEP and patched kernel transferred and
`bootx` returned 0. Serial capture:
`logs/serial/dcsd-20261019-100331.log`; upload evidence:
`logs/normal/upload-20261019-100331{,-resume}.log`.

Unlike the fresh-ticket test, the device passed SEP loading and data
protection (`Gigalocker initialization completed`), mounted Data and other
volumes, entered launchd normal userspace, and enumerated as USB `05ac:12a8`.
No kernel panic was seen through the last check at 10:05 UTC. This establishes
that the preserved installed ticket resolves the missing Preboot SEP path and
is accepted in this boot. It does not yet establish that every userspace/JB
service has finished starting.
