# XRa1n local implementation plan

This file is a local working note. Do not commit or push it.

## Scope

- Standalone XRa1n flow for iPhone XR (`iPhone11,8`, `n841ap`) on iOS
  18.7.10 (`22H374`).
- Linux is the operator host.
- GitHub Actions is used only where macOS/APFS tooling is required.

## Completed

### RestoreRamDisk workflow

- Added `patch_ramdisk_bins.py`.
- Added `.github/workflows/patch-restore-ramdisk.yml`.
- The workflow installs `ipsw` using the same trusted-tap sequence as the
  standalone Pongo KPF test.
- The workflow resolves the IPSW through AppleDB without downloading the full
  IPSW.
- It range-downloads `BuildManifest.plist`, parses it with Python `plistlib`,
  and resolves the exact numbered RestoreRamDisk member for the selected
  identity.
- It range-downloads only the RestoreRamDisk IM4P.
- It extracts the already-unencrypted ramdisk payload and mounts it writable on
  the macOS runner.
- It validates the pristine SHA-256 and every instruction pre-image before
  writing anything.
- It patches:
  - `usr/local/bin/restored_external`
  - `usr/sbin/asr`
- It re-signs both binaries with `ldid`, preserving their entitlements and
  expected identifiers.
- It patches through the existing inode, preserving inode, owner, group, and
  mode.
- It repacks and independently reopens the generated IM4P for verification.
- Successful artifact obtained locally as:
  `RestoreRamDisk-iPhone11,8-22H374.zip`.
- The ZIP passed integrity testing and contains
  `RestoreRamDisk.patched.im4p` plus hashes, metadata, and patch records.

### Current remote commit

- `aadb726 Add RestoreRamDisk patch workflow`
- Only the ramdisk patcher and workflow are tracked by that commit.

### 2026-10-19: SSHRD boot, APFS mounts, and Procursus files

- Booted the dedicated `RestoreRamDisk.sshrd.im4p` with
  `kernelcache.patched-0x891.im4p`. USB enumerated as `05ac:12a8`; root SSH
  worked through `iproxy 2222 22`. The live serial capture was
  `logs/serial/dcsd-sshrd-20261019-062820.log`.
- This boot used a fresh `--ticket-only` TSS capture after patched iBSS entered
  recovery, then `prepare_sshrd_bootset.py --ramdisk-im4p
  RestoreRamDisk.sshrd.im4p`. It did **not** start an erase restore.
- On this device, APFS roles were: `disk1s1` System, `disk1s2` Data,
  `disk1s3` xART, `disk1s4` Baseband Data, `disk1s5` Hardware,
  `disk1s6` Preboot, `disk1s7` Update, and `disk1s8` User. These numbers
  are observations for this boot, not a cross-device assumption.
- Data was mounted read-write at `/mnt2`; System and Preboot were mounted
  read-only at `/mnt1` and `/mnt6`. Neither xART nor User was needed for the
  bootstrap file extraction.
- `/mnt2/jb` was initially empty. Extracted the SHA-256-verified
  `payloads/bootstrap_1900.tar.zst` **contents** of `var/jb` into it over
  SSH. Post-extraction checks found 6,527 entries, including 1,151 symlinks;
  `/mnt2/jb/bin`, `/mnt2/jb/usr`, and `/mnt2/jb/prep_bootstrap.sh` exist.
  These are files on the persistent Data volume, not on the temporary md0
  ramdisk. `prep_bootstrap.sh` was **not** run; Sileo, launchd jobs, and XR
  userland changes were **not** installed.

Commands used, from the ramdisk shell (the `apfs.util -p` command prints its
role but exits 255 on this image, so do not discard nonzero-status output):

```sh
APFS_UTIL=/System/Library/Filesystems/apfs.fs/apfs.util
for dev in /dev/disk1s[1-9]; do
    [ -e "$dev" ] || continue
    role=$("$APFS_UTIL" -p "$dev" 2>/dev/null)
    [ -n "$role" ] && printf '%-18s %s\n' "$dev" "$role"
done

/bin/mkdir -p /mnt1 /mnt2 /mnt6 /mnt2/jb
/sbin/mount_apfs /dev/disk1s2 /mnt2       # Data; already mounted on this run
/sbin/mount_apfs -o rdonly /dev/disk1s1 /mnt1
/sbin/mount_apfs -o rdonly /dev/disk1s6 /mnt6
/sbin/mount | /usr/bin/grep -E '^/dev/disk1s(1|2|6) on /mnt(1|2|6) '
```

Exact host-side extraction pattern (run from `/Project-Cascadia/XRa1n` after
verifying an empty `/mnt2/jb` and the Data mount). The local temporary tree
ensures that the archive's `./var/jb/` prefix is stripped before streaming;
tar preserves owners, modes, and symlinks. The SSH options avoid the host's
unwanted tunnel-forwarding configuration:

```sh
cd /Project-Cascadia/XRa1n
sha256sum payloads/bootstrap_1900.tar.zst
# Expected: 8354c3aa1ecdad8ebc47d9a76dfca6f830a2b757278068bd33b98bf1d638a9cb
stage=$(mktemp -d /tmp/xra1n-bootstrap.XXXXXX)
zstd -dc payloads/bootstrap_1900.tar.zst | tar -xf - -C "$stage"
test -f "$stage/var/jb/prep_bootstrap.sh"
tar -C "$stage/var/jb" -cpf - . |
  /picotool/spironolactone/Linux/aarch64/sshpass -p alpine \
    ssh -F /dev/null -T -o Tunnel=no -o ConnectTimeout=20 \
    -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    -o LogLevel=ERROR -p 2222 root@localhost \
    '/usr/bin/tar -xpf - -C /mnt2/jb'
rm -rf "$stage" # only the temporary directory created above
```

The on-device tar was GNU tar 1.17. The actual transfer and read-back
verification completed successfully. For future runs, select APFS volumes by
role rather than hard-coding the observed disk numbers.

### 2026-10-19: second SSHRD boot and System read-write confirmation

- Rebooted SSHRD from USBLiter8-pwned DFU using the same ramdisk and patched
  kernel hashes above. Captured a **new** nonce-matched ticket with
  `restore_with_ticket.py work/cfw --ticket-only --skip-pico-boot`; no erase
  restore was run. Rebuilt the SSHRD bootset and sent 14 IMG4 images followed
  by `bootx`. Device enumerated as `05ac:12a8`, and root SSH succeeded.
- Active serial log: `logs/serial/dcsd-sshrd-20261019-065133.log`.
  Upload log: `logs/restore/sshrd-upload-20261019-065133.log`.
- In the ramdisk, mounted System at `/mnt1`, Data at `/mnt2`, and Preboot at
  `/mnt6`. Liter8's System remount command worked on this XR boot:

  ```sh
  /sbin/mount_apfs /dev/disk1s1 /mnt1
  /sbin/mount -u -o rw /dev/disk1s1
  /sbin/mount_apfs /dev/disk1s2 /mnt2
  /sbin/mount_apfs -o rdonly /dev/disk1s6 /mnt6
  /sbin/mount | grep ' on /mnt1 '
  ```

- `/mnt1` still showed the `sealed` flag but no `read-only` flag. A unique
  temporary file was created and removed under `/mnt1/Applications` via SSH;
  the command returned `MNT1_WRITE_TEST_OK`. No test file was left behind.
- Serial confirms `disk1s1` System mount, then
  `authapfs_seal_break: disk1s1 integrity protection will be disabled`, then
  `disk1s1 updating mounted volume System`. It also confirms Data
  (`disk1s2`) and Preboot (`disk1s6`) mount completion and describes Data as
  `unencrypted`. No `panic(cpu` or kernel panic was found in this serial log.
- Consequently, the prior assumption that a sealed `/mnt1` could not be
  remounted writable was wrong. The actual remount and write test succeeded
  **for this running patched SSHRD kernel**; this is not yet evidence that
  XR's userland patch deployment or normal boot succeeds.

### 2026-10-19: coreauthd IDA audit (XR 18.7.10 / 22H374)

- Windows IDA MCP session `9f70be37` loaded pristine `coreauthd` at image base
  `0x100000000`; local input SHA-256 is
  `d479cb7f9de2531e85c5af0edd7d91ed9f4a29fea7e192565f7754f1440b22d7`.
- **Confirmed instruction patch:** `-[DTOService startServices]` at
  `0x100007AA0` dispatches block `sub_100007B44`. Its unique call to
  `_objc_msgSend$startController` is at `0x100007B68` (file offset `0x7B68`),
  immediately followed by `LDR X0,[SP,#8]`. Pristine words are
  `0x94007CE6, 0xF94007E0`; the Liter8/XRa1n NOP site is correct. The block
  then calls `performMigrationIfNeeded`, so the NOP does not remove that path.
- **Not yet validated:** the separate Liter8 `l8coreauth.dylib` short-state
  guard. IDA shows two `coreauthd` call sites into
  `LACDTORatchetSEPStateParser ratchetStateFromState:` (at `0x10000A730` and
  `0x10000A9C0`), but the parser implementation is in XR's
  `LocalAuthenticationCore` framework, not in the daemon. Extract or obtain
  the **22H374 XR** framework from the dyld shared cache and inspect its
  length checks, source offsets, and expected state layout before adapting
  Liter8's 0x14B-byte padding. Do not assume the iPad8/iOS 27 layout matches.
- **Framework extracted, parser not yet validated:** the exact 22H374 XR
  `LocalAuthenticationCore` was reconstructed from local dyld cache slices as
  `bins/LocalAuthenticationCore.analysis.dylib` (SHA-256
  `97e95cccdf3322331cb31b7234a254cc6a2299afb0a15d01751d346581b2ac8f`).
  Its LC_ID_DYLIB is the expected framework; IDA sees
  `LACDTORatchetSEPStateParser` and `ratchetStateFromState:`. This is an
  analysis-only repack: dyld-cache fixups/selector references are not fully
  materialized, so a selector string hit alone does **not** identify its IMP.
  The class metadata resolves to a 15-entry method list; none of the entries
  has yet been proven to be `ratchetStateFromState:`. Do not transplant
  Liter8's `0x14b` assumption or install its hook on that basis.
- Liter8's `device/coreauthfix/l8coreauth.m` explicitly handles a **SEP-less**
  empty-state crash and keeps valid SEP state unchanged. XR's observed ramdisk
  logs show SEP initialization/firmware acceptance, not the same crash. The
  daemon NOP and the separate short-state hook are different interventions;
  the hook needs an XR-specific parser-layout proof or an XR crash trace.
- XRa1n's current `patch_userland_bins.py` only performs the NOP. Liter8 also
  adds `LC_LOAD_WEAK_DYLIB /usr/lib/l8coreauth.dylib` and deploys/signs the
  companion. That port/deployment remains open pending framework validation.
- Next daemon for IDA review: `mobileactivationd`; then `ctkd`.

### 2026-10-19: mobileactivationd IDA audit and fixed CFString target

- Windows IDA MCP session `f356172a` loaded pristine `mobileactivationd` at
  image base `0x100000000`; local SHA-256 is
  `17076de71a65e1096c74ee87ee6d4460748f2dfc7ffb9760ba4cf8439bfbb26e`.
- `0x100017350` is exactly `-[DeviceType should_hactivate]`, a two-instruction
  `LDRB W0,[X0,#0x14]; RET` BOOL getter. Replacing its load with `MOV W0,#1`
  is structurally correct.
- `0x1000018A0` is the `TBZ W22,#0` data-migration gate inside
  `-[MobileActivationDaemon getActivationStateWithCompletionBlock:]`.
  NOPing it reaches the existing DataArk/reporting path; the unmodified gate
  otherwise returns `ActivationState=Unavailable` before migration completes.
- The fallback at `0x100001900` loads `Unactivated`; after replacing its
  ADRP/ADD with `ADRP X0; ADD X0,X0,#imm` and NOPing the dereference, the
  existing retain and dictionary construction receive a CFString object.
- **Fixed an actual XRa1n porting bug:** IDA names the `Activated` CFString
  object `cfstr_Activated` at **`0x100279AD0`**. The old hard-coded
  `0x100279AE0` is its `__cstring` pointer field, not the object. Its
  backing bytes are `Activated\0` at `0x1002535C0`; the on-disk chained
  pointer at object+16 is `0x00100000002535C0` and length at object+24 is 9.
  `patch_userland_bins.py` now targets `0x100279AD0` with a layout guard.
  The new ADRP/ADD pair (`0x900013C0`, `0x912B4000`) decodes back to that
  object address.
- `python3 patch_userland_bins.py bins --check` passes all three pristine
  binary digests and all eight instruction pre-images; **no patched daemon
  has yet been executed on-device**. Next IDA target: `ctkd`.

### 2026-10-19: ctkd IDA audit (XR 18.7.10 / 22H374)

- Windows IDA MCP session `9447e697` loaded pristine `ctkd` at image base
  `0x100000000`; local input SHA-256 is
  `97c328e8a85248c0b3b0471a92aa0f13bebdc8a7730f1a43014dbeaf214e8657`.
- The unique `-[TKSEPKeyServer serverAttributesOfKey:error:]` starts at
  `0x100006CD0` (file offset `0x6CD0`, 0x1BC-byte function). Preceding word
  `0xD65F0FFF` is RETAB; first two words are `0xD503237F` (PACIBSP) and
  `0xD10303FF` (SUB SP,SP,#0xC0). This matches Liter8's entry guard.
- Replacing those two entry words with `MOV X0,#0; RET` makes the method
  return nil before the PAC prologue/stack frame and before its
  `publicKeyWithError:` call. Thus no unmatched authentication instruction or
  frame teardown is executed. The original method builds a six-key attributes
  dictionary if a public key is returned.
- IDA shows two daemon call paths: key generation and get-attributes. The
  former treats nil attributes as failure and replies with null outputs plus
  its current error (which may be nil); the latter replies with nil attributes
  and its existing error. Therefore this patch **suppresses SEP key attribute
  operations**; it does not make SEP keys work, nor does it guarantee a
  non-null error to clients. This is the same narrow behavior as Liter8's
  resolver, but on-device end-to-end behavior remains untested.
- All eight three-daemon instruction patches are now structurally audited in
  IDA. Outstanding userland item: validate/port the `l8coreauth.dylib` guard
  against XR's `LocalAuthenticationCore` parser, then test signed/deployed
  binaries and downstream clients on-device.

## Remaining work

### 2026-10-19: stock XR launchd IDA audit

- IDA loaded the pristine `launchd` at image base `0x100000000`; the local
  `bins/launchd` is 577,296 bytes, SHA-256
  `e2323c09df447b6d57bc3d9132be82f7186af167a656921212ca9880e29dea06`.
- At `0x100019810`, PID 1 maps `/System/Library/xpc/launchd.plist`; at
  `0x100019948` it parses the cache as a plist and extracts `LaunchDaemons`,
  `SystemLibraryTreeState`, `AppExtensions`, and `AppRemovalServices`. The
  early boot path `0x100046120` runs `/usr/libexec/launchd_cache_loader`
  and receives its cache through shared memory. Thus the loader, not
  launchd, is the next binary needed to validate whether XR honors
  `launchd_unsecure_cache=1`. That argument is currently configured by
  `patch_iboot.py` but **has not been verified against XR's loader**.
- **XR loader now verified in IDA:** pristine `bins/launchd_cache_loader`
  (SHA-256 `c2ec07393f62847a072b5d93fdf62de058460e3c32f0d495956b9b8078d8c47d`)
  uses the two default paths above in `start` at `0x1000008C8`. In
  `sub_100000A98`, it reads `kern.bootargs` through `sysctlbyname` and parses
  decimal `launchd_unsecure_cache=` using `strstr` + `strtoul` in
  `sub_100001454`. A nonzero value logs `Using unsecure cache` and branches
  directly to `sub_100000F68` (tail branch at `0x100000EF8`), **before**
  `SecStaticCodeCreateWithPath`, `SecCodeSetDetachedSignature`,
  `SecStaticCodeCheckValidity`, and the AMFI trust-cache query. The default
  path performs those checks, then calls the same send function.
  `sub_100000F68` opens/maps the plist and transfers it to launchd through
  the inherited Mach port. This confirms XR has the Liter8 boot-arg mechanism;
  it does **not** prove our normal-boot iBoot path actually supplies that arg.
- Liter8's `device/patch_launchd_cache.py` completed a read-only dry run on
  XR's pristine cache: binary plist VersionNumber 7, exactly 628
  LaunchDaemons, no `com.dropbear` job. The detached `.sig` exists; per
  Liter8's design it is retained unchanged when the unsecure path is used.
- Service execution in `0x100025C30` calls `posix_spawnp` at `0x100026430`;
  early boot task execution in `0x100045960` calls `posix_spawnp` at
  `0x100045E04`. These are the paths Liter8's PID 1 interposer aims to
  cover. No evidence that putting a hook only in the service cache would
  interpose PID 1's already-bound spawn calls.
- The Liter8 weak-load-command patcher fails safely on the XR binary:
  20 load commands end at file offset `0xC78`, the first `__TEXT` section
  begins at `0xC88`, so there are only **16 zero-padding bytes**. A
  `LC_LOAD_WEAK_DYLIB /usr/lib/lhook` needs **40 bytes**. The three existing
  dylib commands are libSystem, libobjc, and a weak libsandbox dependency;
  none can simply be removed or overwritten as a safe way to gain space.
  Do not run Liter8's `patch_launchd.py --apply` on XR or assume its
  iPad launchd layout carries over.
- **Offline XR-specific candidate created (not boot-tested):**
  `patch_launchd_weakload.py` pins the pristine XR SHA-256 and load-command
  layout. It removes only `LC_FUNCTION_STARTS` (16-byte command; its LINKEDIT
  payload is retained) and `LC_DATA_IN_CODE` (16-byte command with zero data
  size), then inserts Liter8's 40-byte weak dependency on `/usr/lib/lhook`
  before `LC_CODE_SIGNATURE`. Commands change 20 -> 19, end `0xC78` ->
  `0xC80`, leaving 8 bytes before the unchanged first section at `0xC88`.
  `bins/launchd.xr-lhook.unsigned` parses as arm64e Mach-O and lists the
  original three dylib dependencies plus `/usr/lib/lhook`; all bytes from
  `0xC88` onward are identical to pristine. Candidate SHA-256:
  `d7a28f8cb96d1455fa732bace139dcc9f9b1a0abeeab2038a808fd522a8411e6`.
  The preserved original code signature is invalid after header edits; this
  artifact must not be deployed until re-signed and tested with the hook
  present. Static parsing does not prove dyld or PID 1 accepts the removed
  metadata commands.
- Liter8's hook depends on being present at launch so dyld resolves its
  `__interpose` entries before PID 1 binds `posix_spawn/p`. XR's launchd
  only has observed startup `dlopen` calls for system libraries, not a
  suitable late hook-loading route. A robust XR solution needs a separately
  validated Mach-O repack/relocation and re-signing strategy, or another
  proven load-at-start mechanism; neither has been implemented or run.
- No launchd binary or cache was modified during this audit. The immediate
  next static target is the exact `22H374` XR
  `/usr/libexec/launchd_cache_loader` and the matching
  `/System/Library/xpc/launchd.plist` layout; only after that should a
  dropbear/jbboot cache edit be planned.

### 1. Prove the patched RestoreRamDisk on-device

- Extract `RestoreRamDisk.patched.im4p` from the workflow artifact.
- Integrate it into the existing boot/restore transport without modifying the
  original IPSW.
- Capture complete serial and restored logs.
- Confirm both patched code paths are reached and that restore proceeds beyond
  the previous failure point.
- Confirm APFS container/volume discovery is dynamic and does not assume that
  System is always `disk1s1` or Data is always `disk1s2`.

### 2. Finish the standalone kernel patch path

- Detailed kernel coverage and implementation plan: `KERNEL_PLAN.md`.
- Audit the current kernel patchfinder against the exact `22H374` XR
  kernelcache.
- Turn every required patch into a guarded match with expected hit counts.
- Produce a machine-readable patch report in CI, as done by the standalone KPF
  test.
- Verify the final kernelcache on-device, not only with the host test harness.
- Keep watchdog suppression diagnostic-only; do not use it to hide a blocked
  service.

### 3. Finish iBoot and DeviceTree changes

- Audit `patch_iboot.py` and `patch_devicetree.py` locally before deciding what
  belongs in the repository.
- Define the minimum required iBoot patches for booting the custom kernel,
  trust caches, DeviceTree, and patched RestoreRamDisk.
- Define explicit DeviceTree edits and validate their types and lengths.
- Avoid renaming `wlan`; the logs proved that renaming the node does not solve
  DriverKit startup.
- Add pre-image checks, output verification, and CI artifacts for both stages.

### 4. Complete the real userland/bootstrap design

- Determine the final three userland binaries and compare their behavior with
  Liter8 rather than copying the old fake-var design.
- Define where bootstrap files, Sileo, package management, trust caches, and
  launch daemons live on the real Data/Preboot layout.
- Implement installation as an explicit overlay/bootstrap operation from
  Linux or the restore ramdisk.
- Do not rely on an ephemeral `/private/var` ramdisk.
- Preserve stock ownership, modes, entitlements, and launchd plist placement.
- Add uninstall/rollback behavior before testing persistent installation.

### 5. Build one reproducible release workflow

- Inputs: device, build, restore identity, and pinned source revisions.
- Outputs: patched RestoreRamDisk, kernelcache, iBoot components, DeviceTree,
  trust caches, bootstrap overlay, hashes, and patch reports.
- Pin tool/action versions where practical.
- Never place extracted Apple binaries or the downloaded IPSW in Git.
- Keep large build products as CI artifacts/releases.
- Add a final manifest tying every output hash to its input build and patch
  revision.

## 2026-10-19: preserved-ticket SSHRD and three-daemon test install

- Added `run_sshrd_boot.py --ticket PATH`; it never captures a new ticket or
  runs `idevicerestore`. It rebuilt the dedicated SSHRD bootset with the
  installed Preboot ticket SHA-384
  `4EF616888D5E8EC38A935B845743A5D2BDC8BC2043AB8A4E53D3916C48CEB8EFCE103BC30456ED1B380A14AE4F1A4243`
  and verified every IMG4's embedded ticket. Other local ticket files were
  removed as requested; this archived installed ticket was preserved.
- Booted SSHRD from USBLiter8-pwned DFU; `bootx` returned 0, USB enumerated
  `05ac:12a8`, and root SSH confirmed `/dev/md0` as the APFS ramdisk root on
  patched Darwin 24.6.0. DCSD process stayed alive but wrote **zero bytes** to
  `logs/serial/dcsd-20261019-103537.log`; the SSH shell and USB state, not
  serial output, establish successful SSHRD boot in this test.
- Mounted System `/mnt1`, Data `/mnt2`, and Preboot read-only `/mnt6`.
  The three original System daemons matched the local pristine `bins/` files
  byte-for-byte. Backed them up locally at
  `work/device-backups/20261019-preserved-ticket/` and on-device at
  `/mnt2/xra1n-backup/20261019-preserved-ticket/`; all backups were read back
  and compared byte-for-byte.
- Offline `verify_userland_signature.py` passed all three patched daemons, as
  did the artifact `SHA256SUMS` check from its own directory. Staged patched
  files under `/mnt2/xra1n-stage/20261019-preserved-ticket/`, verified
  readback, remounted System writable, then installed **only** `coreauthd`,
  `ctkd`, and `mobileactivationd`. Each installed file was read back and
  matched its artifact byte-for-byte; owner/group `0:0`, mode `755` verified.
  The local `install-status.json` records the completed install.
- Did **not** replace `launchd`, modify launchd cache, install hooks, or enable
  `/var/jb/.lhook_enabled`. Normal boot testing followed as recorded below.

### Normal boot after daemon install, 2026-10-19 10:43 UTC

- `run_normal_boot.py` now retries individual firmware and kernel uploads up
  to five times after the recovery protocol becomes responsive. It never
  captures a new ticket on `--ticket` runs and does not restart from DFU after
  a transient firmware-transfer failure.
- Booted with the same preserved installed ticket and patched kernel. The
  first `ANE.img4` transfer lost USB; retry 1/5 succeeded in the same iBEC
  session. All remaining images uploaded and `bootx` returned 0. Upload log:
  `logs/normal/upload-20261019-104313.log`; serial:
  `logs/serial/dcsd-20261019-104313.log`.
- Serial reached `BSD root`, Gigalocker completion, `Early boot complete`,
  and SpringBoard. USB enumerated as `05ac:12a8`; no `panic(cpu` was seen at
  the 10:44 UTC check. This validates kernel/SEP/normal userspace startup with
  the three replaced daemons present; it does **not** by itself establish the
  activation UI outcome, which needs an on-device observation.

## Current next action: stock → patched restore retest (2026-10-19)

- No GitHub workflows were changed or pushed. The user is performing a stock
  iTunes restore separately; **do not** treat `work/cfw` as a pristine IPSW.
- The old XRa1n `logs/` and `tickets/` files were explicitly cleared after
  stopping the stale DCSD logger. This removes the prior installed ticket;
  obtain one only as part of the next patched erase restore.
- `prepare_boot_stages.py --check` rebuilds restore and SSHRD iBSS/iBEC from
  the pinned pristine 22H374 iBoot raw and byte-compares all four outputs.
  This passed offline. Normal iBSS/iBEC are rebuilt from the same verified
  source when preparing the normal bootset. Stage boot args remain distinct:
  restore `rd=md0 -restore`, SSHRD `rd=md0` without `-restore`, normal neither,
  all with `serial=3`.
- `run_restore.py` is the **only** runner that invokes the ticket-only TSS
  capture. It verifies recovery protocol and ECID before idevicerestore,
  checks Apple TSS TLS with the local CA, retries capture-only transport
  failures, then boots restore OS. After restore-OS USB enumeration it restarts
  usbmuxd and requires the target UDID before calling the custom destructive
  idevicerestore. Recovery/TSS capture does **not** use usbmuxd.
- Only successful completion of that custom erase restore writes
  `tickets/restore-complete.json` with the captured ticket hash. Normal and
  SSHRD runners require that marker and reuse that ticket. Neither captures
  a fresh AP ticket, avoiding the earlier Preboot SEP-path mismatch.
- Offline checks passed: 3 unit tests, four iBoot stage byte-comparisons,
  Python compilation and `git diff --check`. No device transfer, TSS request,
  erase, ramdisk mount or userland installation was performed in this audit.

Next sequence after stock iTunes setup/activation: pwn DFU; run the local
patched erase restore; preserve its ticket and completion marker; boot SSHRD
with that same ticket; mount System/Data/Preboot by APFS role and verify read/
write state; stage and read back Procursus files and three daemon replacements
with backups; **complete the Setup.app gate below**; only then perform normal
tethered boot with the same ticket. The Procursus/daemon deployment has its
own guarded local script, `install_sshrd_userland.py`, documented in
`POST_SSHRD_INSTALL.md`; it was tested offline only after this stock restore.

### Mandatory Setup.app gate before the next normal boot

XR 22H374 local parser validation (2026-10-19): the pristine Setup executable
is `bins/Setup.app/Setup`, SHA-256
`595a9e839dcdba3dbaa93f74e63627f984ab56537ce6687a1a1c9a633338344d`.
`patch_setup_xr.py` accepts relative method types from `__objc_methtype` or
`__cstring` (the first XR target uses `__cstring`), and pins this hash for
`--apply` plus exactly 58 class-owned `controllerNeedsToRun` targets. The
dry-run records are `work/setup/records.dryrun.json`. A local unsigned copy
at `work/setup/Setup.patched.unsigned` passed `--verify` for all 58 targets;
an exact byte-diff check found no changes outside the recorded 8-byte
replacement sites. Workflow run 39272593858 then signed it with Liter8's
identifier/entitlement-preserving `ldid` sequence; the signed artifact is
`bins/Setup.patched.signed`, SHA-256
`68c7b8c1db70763465996ac3e152d8a92cd175e60b46523997caddbe33b8880f`.
On 2026-10-19 it was manually staged and installed through the existing
System inode, with matching readback and owner/mode. The pristine backup is
`/mnt2/xra1n-backup/22H374-94C87303E6AE5C414D134F8A/Setup`.
`install_sshrd_userland.py` now automates the same guarded Setup step.

- The three daemon patches do **not** skip Setup.app's first-run panes. Liter8
  separately patches the class-owned `controllerNeedsToRun` methods in
  `/mnt1/Applications/Setup.app/Setup`. Do not use the iPad8/iOS 27 expected
  method count or assume its parser/layout matches XR 18.7.10 (22H374).
- While SSHRD is running, read the **pristine XR** Setup executable and its
  on-device SHA-256, signing identifier, entitlements, ownership and mode.
  Keep a byte-identical original backup **on the device** before replacement.
  Do not patch the live file during discovery.
- Run the local Liter8 `device/patch_setup.py` on a copy in dry-run mode;
  enumerate its class-owned targets and validate each selector, type,
  original ARM64 instruction pair and unique file offset against this exact
  XR binary. Record the XR target count and hashes. A parser failure, count
  mismatch, or non-pristine input blocks deployment and normal boot.
- Port/guard the patch for this exact binary, apply it to the copy, and run
  its `--verify` mode with the discovered XR count. Re-sign while preserving
  Setup's original identifier and entitlements; verify the resulting
  CodeDirectory/signature and target patches **before** any device write.
- Stage the signed result on Data, verify upload/readback SHA-256, replace
  only `/mnt1/Applications/Setup.app/Setup`, and verify final on-device hash,
  owner/mode and executable state. On any failure, restore the on-device
  pristine backup and verify rollback. Record all evidence alongside the
  three-daemon install record.
- The local Setup replacement gate passed on the current SSHRD session.
  This concerns local Setup pane behavior only; it is not proof of
  server-side activation or Apple ID status.


Do not install `launchd` or bootstrap hooks until their separate cache/hook
and rollback prerequisites are met.

### 2026-10-19: upstream Liter8 SSHRD/launchd audit

Reviewed `Liter8-iPad8` remote `upstream/main` at
`d69fc6fe12222fb2871c09722e6fc44b339898a2` (Xplo8E/Liter8). Its
`device/sshrd_provision.sh` is a staged, idempotent pipeline: mounts, ticket,
Setup, userland, pairing, cellular, ScreenTime, injection, tweaks, service
cache, jbtools, Sileo, DNS, apps, verify. XRa1n currently implements only the
four signed Setup/daemon replacements and, on the 17:03 SSHRD session, the
five ScreenTime overrides in Data's `disabled.plist`. The 17:07 normal-boot
serial log confirms `Using unsecure cache` and `Cache sent to launchd
successfully`, but the **stock** System service cache was loaded.

Do not conflate the three launchd mechanisms: Data's `disabled.plist` disables
jobs; System's `/System/Library/xpc/launchd.plist` registers jobs (upstream
adds Dropbear, jbboot and DDI, and mitigates watchdogd); a separately patched
`/sbin/launchd` weak-loads `/usr/lib/lhook` for PID-1 spawn interposition.
Upstream provisions injection **disarmed for first boot** and enables it later
using session-bound `lhookctl`; therefore a missing lhook is not by itself a
proven explanation for XR's initial Hello/passcode box. Upstream's newer
`l8localauth` is an ElleKit tweak loaded through TweakLoader/lhook, with a
separate root-owned marker, translating only exact SEP-less ACM -3 / LA -1000
errors; it does not repair the observed `passd` null dereference. Its selector
encodings and other guards are build-specific and must be validated for
22H374 before any XR deployment.

Next work must not depend on live crash capture: this XR boot has no working
normal-mode USB/SSH path, and an on-screen prompt need not generate a crash
report. First compare the upstream `l8coreauth` companion guard,
pairing/lockdown fallbacks, and Setup flow against XR's 22H374 binaries and
the crash reports already collected. These components are absent from the
current installation. Any persisted reports may be recovered on a later
SSHRD boot, but they are optional evidence, not a prerequisite. Only after
exact-build static checks, signatures, rollback artifacts and a first-boot
test should a service-cache or PID-1 hook change be considered.

Offline source comparison: upstream `device/coreauthfix/l8coreauth.m` weak-loads
only into `coreauthd` and pads a short `LACDTORatchetSEPStateParser` input to
`0x14b` bytes. The XR parser layout is still unproved (see IDA audit above),
so this is not a copy-and-install fix. Upstream `l8pair` is a marker-gated
lockdownd keychain/Trust fallback, while `l8remotepairing` repairs
RemoteXPC's no-keybag state and narrow pairing items. Those target host USB
pairing **after** normal boot, not the first-run Setup pane directly. The
latest `l8localauth` needs the hook/TweakLoader path and a separate marker;
it must not be conflated with the narrower `l8pair_auth` Trust policy 1028
guard. No local pristine XR `lockdownd` or `remotepairingdeviced` binary has
yet been collected for an exact-build structural port.

Isolation note: the existing `IMG_20261019_184210.jpg` shows a full-screen
`Enter Passcode` UI with a lock glyph, alphanumeric keyboard, and **Emergency**
button. This is not the small LAContext confirmation sheet described by
upstream's `l8localauth` commit. Do not assume that tweak addresses this
screen. The already-collected crash archive contains repeated `passd` reports
but no Setup or SpringBoard crash reports. The ScreenTime `disabled.plist`
change left the visible symptom unchanged, lowering that hypothesis.
High-yield reversible A/B test: on the next SSHRD session, temporarily
restore only the on-device pristine Setup executable (preserving the signed
patched copy), normal-boot with the same ticket and other three daemons/kernel
unchanged, and compare the precise UI. If identical, the 58 Setup patches are
not the cause of the passcode screen. Restore the patched Setup afterward.
Do not use an unpatched kernel alone as a control for this modified System:
failure to boot would be confounded by the ad-hoc-signed replacements.

2026-10-19 17:38-17:41 UTC: SSHRD booted from pwned DFU with the same saved
ticket; serial capture `logs/serial/dcsd-20261019-173826.log`. Confirmed
`disk1s1` System and `disk1s2` Data. Before modification, on-device Setup
matched the signed patched artifact SHA-256
`68c7b8c1db70763465996ac3e152d8a92cd175e60b46523997caddbe33b8880f`
and its pristine Data backup matched SHA-256
`595a9e839dcdba3dbaa93f74e63627f984ab56537ce6687a1a1c9a633338344d`.
Saved the patched executable as
`/mnt2/xra1n-backup/22H374-94C87303E6AE5C414D134F8A/Setup.patched-before-abtest`
and verified it byte-for-byte. Remounted System writable and restored **only**
`/mnt1/Applications/Setup.app/Setup` through its existing inode from the
pristine backup. Final on-device readback matches the pristine SHA-256 above;
owner/group `root:admin`, mode `0775`. The three patched daemons, ticket,
ScreenTime overrides, kernel and bootsets were not changed in this step.
Normal boot A/B at 17:44 UTC used the same ticket/kernel and the pristine
Setup executable. Serial `logs/serial/dcsd-20261019-174427.log` confirms
`BSD root: disk0s1`, launchd, and SpringBoard; the user reported the same
full-screen Enter Passcode prompt. Setup's 58 patches are therefore not the
sole cause of that prompt.

2026-10-19 17:57-18:00 UTC: from USBLiter8-pwned DFU, booted SSHRD with the
same saved ticket (no restore or new TSS request). Serial:
`logs/serial/dcsd-20261019-175731.log`. Mounted System `/mnt1` and Data
`/mnt2`; verified all three on-device pristine backups under
`/mnt2/xra1n-backup/22H374-94C87303E6AE5C414D134F8A/` byte-for-byte
against local `bins/`. Saved the installed patched copies in that same backup
directory as `coreauthd.patched-before-abtest`, `ctkd.patched-before-abtest`,
and `mobileactivationd.patched-before-abtest`. Remounted System writable and
restored **all three** original executables through their existing inodes:
`coreauthd`, `ctkd`, `mobileactivationd`. Each post-write readback matched its
local pristine binary byte-for-byte; ownership `root:wheel`, mode `0755`.
Setup remains pristine from the preceding A/B; no ticket, kernel, launchd
override, or other System path was changed in this restore.

2026-10-19 18:01 UTC: normal-booted the same patched kernel/ticket with
**pristine Setup plus pristine coreauthd, ctkd, and mobileactivationd**.
Serial `logs/serial/dcsd-20261019-180137.log` confirms `BSD root: disk0s1`,
launchd early boot complete, and SpringBoard. User reports the **same**
full-screen Enter Passcode prompt. Therefore these four patched userland
executables are not individually or collectively necessary for the symptom.
The serial still contains `mount: failed to migrate Media Keys, error = c002`,
repeated `AppleSEPKeyStore ... unexpected session uid: -1`, and
`disk1s2 rejecting class open ... not content protected`; those are correlated
signals, not proof of which component causes the UI. Remaining common factors
include the patched kernel/DeviceTree, launchd disabled overrides, and the
modified on-disk Data state.

2026-10-19 18:07-18:11 UTC: diagnostic stock-kernel/DeviceTree A/B from
pwned DFU. Stock kernel raw SHA-256
`8aa487693fbb11d9a1880159f99278dbd140bf212ad96af30067da439a301561`
and stock DeviceTree raw SHA-256
`a62d565e5c7537a7aca96b5cce89f6f1111ca48a3b06c179cf0ee570bc6be269`
were verified. Separate test bootsets are under `work/normal-stock-bootset*`;
the known-working `work/normal-bootset` was not overwritten. With both stock
payloads, iBoot rejected the kernel before Darwin (`Kernelcache image not
valid`), serial `logs/serial/dcsd-20261019-180750.log`. Recreating the stock
IM4P with the original `krnl` type and `KernelManagement_host-463.100.7`
version did not change this rejection when retried in the same iBEC session.
This is not a valid stock-kernel UI comparison; the preserved CFW ticket or
boot chain may not accept the stock payload, but the iBoot message alone does
not prove which check failed.

In that same iBEC session, replacing **only** the kernel with the known-working
patched IMG4 did start Darwin with the stock DeviceTree. Serial then showed
`MKB_INIT: No system keybag found on filesystem` and repeated
`AppleSEPKeyStore ... sks timeout strike`; no `Early boot complete` or
SpringBoard was observed, and normal USB did not enumerate. Thus stock
DeviceTree is not a compatible isolated boot control for this modified Data
state. No restore, ticket request, or on-disk file change was made by either
test. Device needs pwned DFU and the known-working normal bootset to return
to the prior iOS state. The temporary stock boot runner was removed to
prevent accidental reuse of a known-invalid test.

2026-10-19 offline security-patch audit: checked all 116 entries in
`work/kernel.patched.records.json` against the pristine 22H374 kernel and
the patched raw kernel extracted from the boot artifact. Every original and
replacement word matched (116/116); there is no evidence of a misplaced
file offset or of booting a different kernel artifact. The 50
`credential-manager` records force success in 25 ACM functions, including
`lockItem`, `unlockItem`, `sendSEPCommand`, and `getSEPEndpoint`. The AKS
selector-7 shim fabricates `noPin | beenUnlocked` state. These broad behaviors
match the local Liter8 implementation, but that does not establish that they
have the right semantics for iPhone11,8/22H374 or for every caller.

The normal DeviceTree's four changes also match local Liter8: remove
`content-protect`; add `no-effaceable-storage`, `boot-ios-diagnostics`, and
`ephemeral-storage`. The local usbliter8-fun `dt_patch3.py` analysis says
`no-effaceable-storage` intentionally makes keybagd skip loading
`systembag.kb`. Thus the stock-DeviceTree `MKB_INIT` missing-systembag
messages are expected when trying to boot this deliberately unencrypted Data
state with stock policy; they do not by themselves identify the Enter
Passcode prompt's cause. The successful patched-DeviceTree boot still logs
media-key migration failure, non-content-protected class-open rejections,
and SEPKeyStore session warnings. These are correlations, not proof that any
one kernel patch is faulty.

Next isolation target is the *source of the passcode request* and its exact
lock-state/AKS result on 22H374, not another blind stock-kernel or
stock-DeviceTree boot. Do not attribute the prompt to Setup or the three
patched daemons: it persists when all four are pristine. The stock-kernel
ticket/IMG4 rejection also prevents using that test as a kernel A/B.

IDA follow-up (192.168.1.19:8745): the open database is the **pristine** XR
22H374 kernelcache (SHA-256 matches the stock hash above), so IDA exposes the
original control flow. Its image base is `0xfffffff007004000`; kernel patch
file offsets map to IDA addresses by adding this base. At file offset
`0x0223E7F8` (IDA VA `0xfffffff0092427f8`), IDA places the AKS selector-7 case
inside `AppleSEPKeyStore`'s `externalMethod` dispatcher
(`sub_FFFFFFF009240960`). The original case validates one scalar input and
one scalar output, passes selector 7 and the input through
`sub_FFFFFFF00923FF64` (the keybag-access helper), and on success continues
through `sub_FFFFFFF009236890` before returning its status/output.

The replacement shim repeats basic pointer/count checks, writes constant
`0x64` (`noPin | beenUnlocked`) to the scalar output, returns success, and
branches to the dispatcher's common authenticated epilogue. It therefore
skips the keybag-handle/access result and the normal selector-7 processing;
callers receive the fabricated state even when the underlying keybag path
would fail. That is now the strongest kernel-side suspect for inconsistent
lock state and is consistent with (but does not prove the cause of) the
`passd` crash at `PDGetDeviceLockState`/`CFDictionaryGetValue(NULL)`. The
visible passcode screen itself is not proven to originate from this call.
This narrows the investigation to selector-7's fake state contract and its
callers; it does not show a bad offset or malformed instruction encoding.

## 2026-10-20 broad AKS `externalMethod` normal-boot result

The selector-7-only shim was replaced in `patch_kernel.py` with the
USBLiter8-fun-style patch target at file offset `0x02245F64`, the entry of
`AppleKeyStoreUserClient::externalMethod` (IDA VA
`0xfffffff009249f64`). The intended final stub is:

```asm
mov x0, #0
ret
```

This bypasses the complete selector dispatcher rather than synthesizing only
selector 7. The source patcher now guards both original words (`0xD503237F`,
`0xD10643FF`) and emits `0xD2800000`, `0xD65F03C0`; the `sep-aks` group has
14 guarded records and the full profile has 102.

`prepare_normal_bootset.py` now selects
`kernelcache.patched-0x891-full-aks.im4p` as its default normal-boot kernel.
The bootset was rebuilt and sent on 2026-10-20. Serial capture:

```text
logs/serial/dcsd-20261020-110346.log
```

The device reached normal userland boot tasks and displayed Setup.app without
the previous lock/passcode screen. This is the first successful A/B result
that changes that UI while retaining the same restored Data state and normal
boot path. It establishes that the selector-7-only behavior was insufficient
and that changing the outer AKS user-client path removes the observed lock UI.
It does not yet identify one responsible selector; selector 17/device lock
state remains a leading candidate because it is distinct from selector 7's
first-unlock state.

Artifact qualification: the exact IM4P used in that boot had SHA-256
`fe36808c33a97c8bdd24172357ccd24cc59dd2b73bf050ec3dec1c6ac2c008cd`.
Direct extraction showed `0xD2800000 0xD10643FF` at `0x02245F64`, meaning
that particular downloaded artifact contained the first-word `mov x0, #0`
change but had not yet incorporated the subsequently corrected `ret` word.
Therefore the observed no-lock result must be recorded against that exact
one-word artifact, not misreported as proof of the final two-instruction
early-return stub. A fresh GitHub artifact built from commit `ace1c8c` must be
downloaded and booted to validate exact USBLiter8 parity.

## 2026-10-20 lockdownd pairing fallback status

The constructor-only weak-load probe proved that the restored 22H374
`lockdownd` loads an added dylib. The full local Xplo8E-Liter8 pairing design
was then ported into `funny-dylibs/` and built as universal arm64/arm64e
artifacts:

* `xra1n_pair.c` interposes exactly three Security APIs and handles only the
  `lockdown-identities` / `com.apple.lockdown.pairingkeypair` system-keychain
  item. All other requests call the originals.
* `xra1n_pair_auth.m` wraps lockdownd's private `LAContext` evaluation.
* `xra1n_coreauth.m` pads only malformed SEP ratchet state shorter than
  `0x14b` before invoking the original coreauth parser.

`lockdownd` weak-loads `/usr/lib/xra1n-pair.dylib`; `coreauthd` weak-loads
`/usr/lib/xra1n-coreauth.dylib`. Their pre-change images are backed up under
`/mnt2/xra1n-backup/pairing-full-f2988cd/`. The root-only activation marker is
`/private/var/root/Library/Lockdown/.xra1n-pairing-fallback` (mode 0600).

The original iOS 27 condition (policy 1028, LAError -1000, and diagnostic text
containing `failed: -3`) and the first XR adaptation (policy 1028 plus the same
domain/code) both still ended in `UserDeniedPairing`. This proves USB and the
Trust dialog path are reached, but not that the iOS 18 private LA callback uses
policy 1028.

Commit `cc3c3d2` is the one-cycle diagnostic build. It records every wrapped
LA reply in:

```text
/private/var/root/Library/Lockdown/xra1n-pair-auth.log
```

and temporarily converts a failed lockdownd LA reply to success after logging
its policy, result presence, NSError domain/code, and description. Successful
evaluations are unchanged, the behavior remains marker-gated, and no other
process is loaded with the dylib. Workflow run `39437215573` succeeded. The
installed/read-back dylib hash is:

```text
0e8fd1841c856570aa4fa423a308bc1dd82daf3ecd60261624735c559ae925f3
```

The previous dylib is backed up at
`/mnt2/xra1n-backup/pairing-diag-cc3c3d2/xra1n-pair.before`, and the old
diagnostic log was cleared before installation. Next: tether-boot normal,
perform one Trust attempt, then boot SSHRD once to collect the log and replace
the temporary broad failure handling with the measured narrow 22H374 policy.

### Measured 22H374 result

The diagnostic boot paired successfully. SSHRD inspection found a single
failed Trust evaluation:

```text
policy=1028
domain=com.apple.LocalAuthentication
code=-1008
NSDebugDescription=DTO policies are not available before first unlock
Subcode=1
```

The constructor loaded in `lockdownd` as PID 83, the persistent pairing key
was created at mode 0600, and host pairing validated successfully. The final
source removes the temporary any-failure fallback and accepts only policy 1028
with either the original Liter8 iOS 27 tuple or this measured XR 22H374
`-1008`/DTO-before-first-unlock tuple. Persistent logging remains available for
regression diagnosis.
