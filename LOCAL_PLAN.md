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
proven explanation for XR's initial Hello/passcode box.

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
pairing **after** normal boot, not the first-run Setup pane directly. No local
pristine XR `lockdownd` or `remotepairingdeviced` binary has yet been collected
for an exact-build structural port.

Isolation note: the existing `IMG_20261019_184210.jpg` shows a full-screen
`Enter Passcode` UI with a lock glyph, alphanumeric keyboard, and **Emergency**
button. This is not a small LAContext confirmation sheet. The already-collected
crash archive contains repeated `passd` reports
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
was then ported into `xr-runtime-fixes/` and built as universal arm64/arm64e
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

## Roadmap after pairing validation

### Completed and now treated as baseline

1. **Erase restore and tethered boot:** 22H374 erase restore completes and the
   ticketed normal/SSHRD boot chains are reproducible for iPhone11,8.
2. **Normal-boot lock UI:** the full AKS `externalMethod` kernel patch removes
   the prior passcode/setup lock screen and allows Setup.app to run.
3. **Native Setup:** Setup completes without the earlier forced lock screen.
4. **Pairing-key persistence:** the narrow Security interposer persists only
   lockdownd's `lockdown-identities` pairing key; the key is root-owned mode
   0600.
5. **Trust authentication:** the final hook accepts only the measured private
   Trust policy failures:
   * iOS 27 policy 1028 / LAError -1000 / ACM `failed: -3`; or
   * XR 22H374 policy 1028 / LAError -1008 / DTO unavailable before first
     unlock.
6. **Pairing survives reboot:** after installing final commit `c943ce0`,
   `idevicepair validate` succeeds after a new tethered normal boot and
   `ideviceinfo -k ProductVersion` returns `18.7.10`.
7. **PID 1 weak-load bootstrap:** the XR 22H374 launchd now weak-loads
   `/usr/lib/lhook`. The 40-byte `LC_LOAD_WEAK_DYLIB` was fitted by removing
   the unused 16-byte `LC_FUNCTION_STARTS` command and empty 16-byte
   `LC_DATA_IN_CODE` command; no section contents were moved. After repairing
   the affected CodeDirectory page hash, the device reached `Early boot
   complete` with no launchd, dyld, code-signature, missing-library, or panic
   errors. A subsequent SSHRD inspection proved constructor execution in PID
   1 from `/mnt2/jb/tmp/lhook.log`:

   ```text
   [lhook] loaded into pid 1
   ```

   Installed hashes:

   ```text
   641583bb8eb5f274bc31a502552bcb3b46990ffaeb0892c053f25ec583450807  /sbin/launchd
   d219a0b72484530e431c946151040b8c8af0f38fb58e7705756b723a245e99  /usr/lib/lhook
   ```

   The pristine launchd rollback copy is
   `/mnt2/xra1n-backup/22H374-launchd-lhook-test/launchd`. Injection remained
   disabled for this proof: neither `/var/jb/.lhook_enabled` nor
   `/var/jb/.lhook_debug` was present.
8. **Guarded lhook propagation:** the signed universal diagnostic payload was
   installed at `/var/jb/usr/lib/systemhook.dylib` with SHA-256
   `7d0a8800dd95008cbb2e3256443c9698e9d413e6bb143059adf4739246a38807`.
   Both `/var/jb/.lhook_enabled` and `/var/jb/.lhook_debug` were created as
   root-owned mode 0600 files. No custom `lhook.deny` was installed, so the
   conservative denylist compiled into lhook remained active. The following
   normal boot stayed healthy, pairing still validated, and
   `ideviceinfo -k ProductVersion` returned `18.7.10`.

   A subsequent SSHRD read of `/mnt2/jb/tmp/lhook.log` contained 47 lhook
   records. It proved constructor execution in PID 1 and several injected
   children (`14`, `16`, `18`, `19`, `21`, `23` through `29`), and recorded
   injection requests for SpringBoard, Preferences, Spotlight, PosterBoard,
   widgets, and extensions. Representative evidence:

   ```text
   [lhook] loaded into pid 1
   [lhook] loaded into pid 18
   [lhook] injecting /System/Library/CoreServices/SpringBoard.app/SpringBoard
   [lhook] injecting /Applications/Preferences.app/Preferences
   ```

   Sandboxed applications need not append a shared `loaded` record because
   the constructor falls back to each process's private `TMPDIR`; PID 1's
   central debug trace still proves that the modified spawn environment was
   submitted. The next payload test should use a target-specific observable
   result rather than treating the shared log as universal.

9. **Launchd-managed normal-boot root SSH:** normal iOS now starts Dropbear
   from a new entry in `/System/Library/xpc/launchd.plist`. The cache remains
   VersionNumber 7 and now contains 629 LaunchDaemons, including
   `/System/Library/LaunchDaemons/com.dropbear.plist`. Its installed SHA-256
   is:

   ```text
   855c72f84bf29765befdccc78d411fb7f01a9562af773c39bc8857c6209ea6b5  /System/Library/xpc/launchd.plist
   ```

   The pristine cache is retained both at
   `/mnt2/xra1n-backup/22H374-normal-ssh/launchd.plist` and beside the live
   cache as `/System/Library/xpc/launchd.plist.xra1n-original`. The unmodified
   detached cache signature is intentionally bypassed by the already audited
   `launchd_unsecure_cache=1` loader path.

   The installed SSH executables and hashes are:

   ```text
   89e789cd3ca755daa0cbc9b52a10f2373b021460a7c5a0cbd7a3a705126c5553  /usr/local/bin/dropbear
   3c5747c3fce3e226e6fff132a305c67b948b83e41d8143de00e17087df9e6b67  /usr/local/bin/dropbearkey
   ```

   Unique RSA, ECDSA and DSS host keys live under `/private/var/dropbear`.
   The client public key is installed at
   `/private/var/root/.ssh/authorized_keys`; the matching host-side private
   key is kept under
   `work/artifacts/normal-ssh/22H374/device-key/id_ecdsa`. Passwordless root
   SSH through `iproxy 2222:22` was verified on normal iOS with UID/GID 0,
   Darwin 24.6.0, and ProductVersion 18.7.10.

   Dropbear needed three command binaries that were absent from the stock
   System image. They were installed with valid CodeDirectory page hashes:

   ```text
   d6baf3862004da95510b4d831e5447e699daa99338e6fcb2f2c44267a290a403  /bin/sh
   84575bd31e2918494d60787dcdcf4a2b6c95837f433da95148717f288f1fa831  /bin/ls
   139b84461327f3c2ec6caea9baf7e4bcb773133741427a551b6059246a38807  /bin/cat
   ```

   The normal boot arguments are now exactly:

   ```text
   -v debug=0x2014e wdt=-1 launchd_unsecure_cache=1 serial=3
   ```

   `patch_iboot.py` supplies the first four fields and the bootset builder
   appends `serial=3`. Serial output confirmed this exact value. Pairing still
   validates after enabling SSH.

10. **Normal-boot System write boundary measured:** the running normal OS can
    modify Data and use `/var/jb`, but the mounted System/root volume rejected
    a test file with `Read-only file system`. Running
    `/sbin/mount -u -o rw /` also failed with `mount_apfs: volume could not be
    mounted: Operation not permitted` and mount status 77. This differs from
    the patched SSHRD kernel, where mounting `disk1s1` at `/mnt1` and then
    `/sbin/mount -u -o rw /dev/disk1s1` was proven to break authenticated-APFS
    protection and permit writes. Until a separate normal-boot mount is
    tested successfully, automate all persistent System mutations through
    SSHRD, and reserve normal SSH for Data-volume changes, inspection, service
    tests, and log collection.

11. **Dropbear Procursus PATH installed:** through SSHRD, System was mounted at
    `/mnt1` and remounted writable, while Data was mounted at `/mnt2`. The
    Dropbear dictionary in `/System/Library/xpc/launchd.plist` now contains:

    ```text
    EnvironmentVariables/PATH =
    /var/jb/usr/bin:/var/jb/usr/sbin:/var/jb/bin:/usr/bin:/bin:/usr/sbin:/sbin:/usr/libexec
    ```

    This applies to interactive and noninteractive Dropbear children and does
    not depend on shell startup files. The binary plist still parses as
    VersionNumber 7 with 629 LaunchDaemons. Its previous SHA-256 was
    `855c72f84bf29765befdccc78d411fb7f01a9562af773c39bc8857c6209ea6b5`;
    the installed/read-back SHA-256 is
    `37d26032c44fdc8613fcbd2abffed4db0a4384b1faa31525bdce627050c0b4cb`.
    The immediate pre-PATH cache is backed up at
    `/mnt2/xra1n-backup/22H374-normal-ssh-path/launchd.plist.before-path`, and
    the staged replacement remains at
    `/mnt2/xra1n-stage/22H374-normal-ssh-path/launchd.plist.pathfix`.
    Byte-for-byte read-back and plist decoding both passed. The subsequent
    normal-boot runtime test showed that Dropbear replaces the inherited value
    with its compiled/default SSH-session PATH:

    ```text
    /usr/bin:/bin:/tmp/bin:/tmp/usr/bin:/binpack/usr/bin:/binpack/bin
    ```

    Consequently `dpkg`, `apt-get`, `bash`, `id`, and `head` were not found by
    bare name even though normal root SSH and pairing both worked. The launchd
    environment edit is valid but insufficient: the next implementation must
    patch/rebuild Dropbear's default PATH or launch a wrapper that exports the
    Procursus PATH before executing Dropbear/session commands. Keep absolute
    `/var/jb/...` paths in automation until that is validated.

12. **Native jbboot PATH helper installed:** workflow run `39507148327` built
    commit `1c57e16a41a21281178c672e67d58f8f9ffa0f48` as a signed universal
    arm64/arm64e executable. The downloaded and installed helper is:

    ```text
    42fd6bca0327fc7759768382e325e4a9564b044fac3d0f9c1be9c4f052a28088  /usr/local/bin/xra1n-jbboot
    ```

    It waits for root-owned, non-group/other-writable `/var/jb/usr/bin` and
    `/var/jb/usr/sbin`, then securely creates the ephemeral mappings already
    present in Dropbear's compiled PATH:

    ```text
    /private/var/tmp/bin     -> /var/jb/usr/bin
    /private/var/tmp/usr/bin -> /var/jb/usr/sbin
    ```

    Unexpected occupants are moved aside, not deleted. A one-shot
    `/System/Library/LaunchDaemons/com.xra1n.jbboot.plist` dictionary was added
    directly to the Version 7 launchd cache. The cache now has 630 jobs and
    SHA-256
    `e175e024ed19142a7313058ca4184e8a8374252e3046f7a14ce24ca9e2cdc37e`.
    It launches `/usr/local/bin/xra1n-jbboot` at load and writes stdout/stderr
    to `/var/mobile/xra1n-jbboot.log`.

    The ineffective Dropbear `EnvironmentVariables/PATH` edit was removed by
    constructing this cache from the verified pre-PATH cache; the installed
    Dropbear dictionary has no `EnvironmentVariables` member. Device staging
    is under `/mnt2/xra1n-stage/22H374-jbboot/`; the immediate pre-jbboot live
    cache is preserved at
    `/mnt2/xra1n-backup/22H374-jbboot/launchd.plist.before-jbboot`.
    Helper and cache both passed byte-for-byte device read-back. The following
    normal boot validated the complete runtime contract: pairing validated,
    key-only root SSH connected, both links were root-owned and pointed to the
    intended Procursus directories, and Dropbear retained its stock compiled
    PATH while resolving `dpkg`, `apt-get`, and `id` through `/tmp/bin`.
    Bare `dpkg --version` reported version 1.22.6 (`iphoneos-arm64`). The log
    `/var/mobile/xra1n-jbboot.log` recorded creation of both links followed by
    `xra1n-jbboot: PATH links ready`. This PATH item is now complete; preserve
    the stock Dropbear binary and automate the helper/cache installation.

13. **Rootless icon sandbox-token automation prepared:** commit
    `879676b450cc3b664e48a267e9c1211fb8a1e9a0`, workflow run `39514997021`,
    extends the same System-resident `xra1n-jbboot` executable instead of
    adding another launchd job. After the PATH links are ready it resolves
    `sandbox_extension_issue_file`, issues a
    `com.apple.app-sandbox.read` extension for `/private/var/jb`, and
    atomically publishes the bearer token at
    `/private/var/tmp/sbext.token` with root ownership and mode 0644. This is
    the producer expected by the already installed `systemhook` consumer in
    `iconservicesagent`. The signed universal arm64/arm64e artifact is pinned
    in `install_sshrd_userland.py` as:

    ```text
    7fcaa73960ba36bacfa3e99eed5e4ef834f737629dc7c3f18a2b5f041287dd30  xra1n-jbboot
    ```

    Local syntax checks, six installer tests, signature/page-hash validation,
    and offline preflight pass. Deployment still requires SSHRD because the
    replacement target is `/usr/local/bin/xra1n-jbboot` on the sealed System
    volume. After installation, normal boot must verify the producer marker
    `xra1n-jbboot: sandbox read token refreshed`, the consumer result
    `/private/var/tmp/sbext.iconservicesagent.result`, then rerun `uicache` and
    confirm that Sileo has an icon and launches.

    Deployment and first normal-boot measurement are now complete. The helper
    log contains `xra1n-jbboot: sandbox read token refreshed`; the mailbox is a
    root-owned mode-0644, 186-byte token. A disposable non-platform executable
    named `iconservicesagent` loaded the installed `systemhook.dylib` and wrote
    `consume OK, /var/jb verified readable`, proving both token generation and
    the consumer implementation. The native platform `iconservicesagent`,
    however, did not load `systemhook`: its PID never appeared in the lhook
    marker and no consumer-result file was created. Installing the repository's
    validated `/var/jb/etc/lhook.deny` (which intentionally allows xpcproxy),
    then restarting the launchd service, did not change that result. The next
    task is therefore platform-process injection/weak loading for
    `iconservicesagent`, not token generation or Sileo registration.

14. **Manual Xploite/Liter8 System-app trial installed:** from SSHRD, copied
    all 40 bundles still present in Data's `/staged_system_apps` into System
    `/Applications` without overwriting an existing bundle. The System bundle
    count increased from 190 to 231 after also copying Sileo. The exact copied
    names are recorded on-device at
    `/mnt2/xra1n-stage/22H374-liter8-manual/apps-copied.txt`. Sileo was copied
    from the existing rootless package to `/Applications/Sileo.app`; its main
    executable read back identically at both locations with SHA-256
    `90fe8971b8e9b6f0acefc26824ea50c569995d67981e6a1bc068c51564153552`,
    and `giveMeRoot` is setuid mode 4755. `/var/jb/var/mobile` is UID/GID
    501:501 and `.installed_usbl8r` exists. The icon consumer was also installed
    at Xploite's System path `/usr/lib/systemhook.dylib`, read-back SHA-256
    `7d0a8800dd95008cbb2e3256443c9698e9d413e6bb143059adf4739246a38807`.
    Final destination, setuid and marker checks returned
    `MANUAL_LITER8_INSTALL_VERIFIED`. Next normal boot must run the guarded
    one-time `uicache -a`, validate Apple app icons and Sileo launch, and only
    then should these mutations be integrated into the installer.

    First normal-boot registration succeeded. The guarded precheck found no
    container application and no prior marker, so `uicache -a` ran once and
    created `/var/jb/.liter8-system-apps-registered`; `uicache -r` then
    restarted SpringBoard. LaunchServices reports 228 applications. Safari,
    Camera and Messages now resolve to their correct `/Applications/*.app`
    paths with normal containers, and Sileo now resolves to
    `/Applications/Sileo.app` rather than `/var/jb/Applications/Sileo.app`.
    SpringBoard remained alive after the respring. A URL-scheme launch did not
    leave a Sileo process running, so Apple app population/registration is
    proven but Sileo launch is not yet complete. Before automating Sileo, build
    Xploite's constrained/re-signed bundle instead of copying the package
    bundle unchanged, then capture its first-launch failure or success.

### Immediate remaining work

1. **Replace the broad AKS bypass with a coherent selector model.** The current
   normal kernel returns success at `AppleKeyStoreUserClient::externalMethod`
   before normal dispatch. It fixes the visible lock UI but also prevents real
   keybag state transitions. Restore normal dispatch and model only the proven
   missing selectors, starting with:
   * selector 7: preserve `ss & 4` for unlocked-since-boot;
   * selector 17: report `sls == 3` for no-passcode state;
   * retain valid output structures and counts rather than returning success
     with no payload.
2. **Restore first-unlock/keybag service authorization.** Pairing and basic
   Lockdown requests work, but `com.apple.os_trace_relay` returns
   `Password protected (-17)` even while the UI is unlocked. Validate with
   `MKBGetDeviceLockState`, `MKBGetDeviceLockStateInfo`, and
   `MKBDeviceUnlockedSinceBoot`, then retest `idevicesyslog`.
3. **Remove diagnostic persistence from the release pairing dylib.** Keep a
   separately named diagnostic build, but the final release should not append
   every LA reply to `xra1n-pair-auth.log`. Preserve only narrow syslog events
   or compile diagnostics behind an explicit build flag.
4. **Automate the final userland installation.** Extend
   `install_sshrd_userland.py` to install the two versioned pairing dylibs,
   patch and rehash `lockdownd`/`coreauthd` idempotently, install and rehash
   launchd plus `/usr/lib/lhook`, install `systemhook.dylib`, create the
   guarded lhook markers, install the normal-boot Dropbear job/binaries/keys,
   and patch the launchd cache's Dropbear `EnvironmentVariables/PATH` to
   include Procursus. Every input and installed file needs a pinned hash,
   read-back verification, and a pristine rollback copy on Data. Do not
   depend on `/tmp` copies of Liter8 tools; vendor a generic guarded weak-load
   patcher and binary-plist job editor in XRa1n.
5. **Consolidate local artifacts.** Keep generated binaries only under
   `work/artifacts/<component>/<commit>/`, logs under `logs/`, and device-side
   rollback material under `/mnt2/xra1n-backup/<version>/`. Remove obsolete
   top-level experimental outputs only after their hashes and provenance are
   recorded.

### Functional validation after the AKS refinement

Run the following in order on one fresh normal boot:

1. `idevicepair validate` succeeds without another Trust prompt.
2. `ideviceinfo` and Lockdown value queries succeed.
3. `idevicesyslog` starts `com.apple.os_trace_relay` without error `-17`.
4. Reconnect USB and repeat steps 1–3.
5. Verify Wi-Fi association, cellular/baseband state, Phone.app inventory, and
   normal app population independently; pairing success does not establish any
   of those services.
6. Warm tether-boot again and confirm pairing, first-unlock state, Setup
   completion, and activation state all persist through their separate native
   stores.

### Later integration work

1. Convert the proven launchd-managed normal-boot SSH experiment into the
   guarded installer described above, then add a read-only health command and
   rollback mode. The listener itself is already working; packaging and
   repeatability remain.
2. Install and validate the intended `TweakLoader.dylib`/ElleKit stack before
   enabling actual tweak packages. The current `systemhook.dylib` is a narrow
   iconservicesagent diagnostic payload, not a general tweak loader.
3. Package Sileo only after launchd job loading and `/var/jb` bootstrap
   preparation are proven.
4. Investigate Wi-Fi and cellular against local Liter8/Xplo8E commits and
   device logs after keybag-dependent services are healthy.
5. Remove the constructor-only `l8pair-probe.dylib` load command once the full
   hook has sufficient regression coverage; it is no longer needed for normal
   operation.

## 2026-10-21: Photos/Camera failure, Files persona gap, Wi-Fi/cellular next work

### Photos and Camera measurement

- Normal boot remained stable and root SSH worked through `iproxy 2222:22`.
- Latest Photos crash (`Photos-2026-10-20-172135.ips`) is `SIGABRT` from
  `-[PLPhotoLibrary loadDatabaseWithOptions:error:]`; the exception propagates
  through `PHPhotoLibrary` initialization.
- Latest Camera report (`Camera-2026-10-20-172159.ips`) is a `scene-create`
  watchdog transgression after 19.95 seconds, not an AMFI or dyld failure.
- `assetsd` and `applecamerad` were both running normally under user 501.
  `/var/mobile/Media/PhotoData`, `Photos.sqlite`, WAL, and SHM were owned
  `501:501` with plausible modes.
- Reversible test: moved PhotoData to
  `PhotoData.xra1n-backup-20261021-002602`, created an empty `501:501` PhotoData,
  restarted assetsd, and retried both apps. The issue persisted and assetsd did
  not initialize a new database. The original directory was restored and its
  `Photos.sqlite` read back successfully.
- Conclusion: this is not stale/corrupt PhotoData and not missing app
  registration. It matches the profile-wide absence of working Data Protection:
  the boot log says `/private/var` does not support Data Protection, while
  Photos opens a protected library and Camera depends on that media path.
  Upstream Liter8 explicitly describes its present boot as lacking a
  content-protected Data volume. A fix needs a narrowly measured PhotoLibrary /
  AKS compatibility path or genuine Data Protection, not another database wipe.

### Files.app is empty

- This is a distinct persona/FileProvider problem, not evidence that the Files
  bundle is absent. Xplo8E Liter8 commit `ae07474` documents and fixes the same
  visible symptom on iPad11,6/23H30: no personal persona causes fileproviderd to
  reject LocalStorage and leaves no usable provider DB.
- That fix is `device/filesfix/l8files.m`, injected only into `fileproviderd`
  and `LocalStorageFileProvider`. It supplies a process-local single-user
  persona view, strips the synthetic persona from the ExtensionKit launch, and
  admits only `com.apple.FileProvider.LocalStorage`. It deliberately excludes
  iCloud and Photos providers.
- The upstream payload is fail-closed for iPad11,6/23H30. It must be ported and
  measured for this iPhone11,8/22H374 build; do not install the current binary
  unchanged. We also need to confirm our normal boot has the required ElleKit
  injection/TweakInject path or adapt the already-proven launchd hook.

### DeviceTree discrepancy to test, not assume

- Current XRa1n normal DeviceTree plan in `patch_devicetree.py` removes
  `/defaults/content-protect` and sets `/chosen/ephemeral-storage=1`.
- It explicitly does **not** add `/defaults/no-effaceable-storage` or
  `/product/boot-ios-diagnostics`.
- The reviewed Xplo8E 24A435 RC worksheet lists all four normal-boot changes:
  remove content-protect, add no-effaceable-storage, add boot-ios-diagnostics,
  and set ephemeral-storage.
- `usbliter8-fun/work-27.0b3/patch_dt2.py` also adds both properties and labels
  them AppleKeyStore SEP workarounds. The older beta-2 source explains that
  `no-effaceable-storage` selects keybagd's no-effaceable path.
- These flags could change keybag/persona startup, but they do not create real
  content protection. Prepare them as a separate normal-DT A/B boot artifact;
  preserve the current known bootset and compare keybag state, Files
  LocalStorage, Photos, Wi-Fi, and pairing before adopting them.

### Wi-Fi source paths

- `usbliter8-fun/work-27.0-rc-iphone11/fixes/` contains an exact-build Wi-Fi
  experiment: weak-load a shim into `wifid` and file-back only `AirPort`
  generic-password records. It was explicitly rolled back after causing
  pairing/lock-state regressions and is not a verified general Wi-Fi fix.
- It can solve credential persistence only when the physical interface and
  driver already work. It does not repair a missing Wi-Fi driver, WPA3, EAP,
  SEP, or Data Protection. First measure current `wifid`, interface state,
  association behavior, and whether the failure is precisely the AirPort
  keychain operation before porting its hash-bound 24A435 patch to 22H374.

### Cellular source paths and current upstream status

- Xplo8E main contains the complete cellular research series merged by
  `c3cbcd1`: restore-stage baseband predicates (`0ab9b9d`), FDR calibration
  unsealing (`c9e909f`), CommCenter data-context gates (`dc6883d`, `04488f0`),
  and ARI tracing/localization (`6e77c3b`).
- The upstream investigation still localizes the remaining blocker to the
  modem/ARI path; `docs/BACKLOG.md` still lists cellular functionality as open.
  Therefore this is not a finished payload to copy blindly.
- Our restore log did update/stage baseband and created/mounted Baseband Data,
  unlike the old `usbliter8-fun` custom restore which deliberately patched
  restored_external to report no baseband. Start by measuring CommCenter,
  baseband firmware/state, calibration/FDR query results, SIM visibility and
  ARI failure on 22H374. Then port only matching gates with exact hashes and
  readback tests.

### Ordered next actions

1. Capture current Wi-Fi interface/wifid and CommCenter/baseband state over
   normal-boot root SSH; classify each failure before changing binaries.
2. Port and build the Files LocalStorage compatibility tweak for
   iPhone11,8/22H374, with process/build guards and a marker-controlled rollback.
3. Build a separate DT A/B artifact adding only the two omitted properties;
   compare personas/keybag and preserve pairing/Sileo before adoption.
4. For Wi-Fi, use the local AirPort keychain shim only if logs prove credential
   storage is the blocker.
5. For cellular, reproduce the upstream diagnostic chain before considering
   FDR or CommCenter patches; upstream does not yet claim a complete modem fix.

### 2026-10-21 live Wi-Fi/cellular classification (normal boot)

Read-only checks were run after restarting usbmuxd and validating the existing
pair record.  The device was iPhone11,8, 18.7.10/22H374 and normal-root SSH was
available through iproxy.

#### Wi-Fi

- `wifid` is running as a stable user/501 launchd service (`runs = 1`, no prior
  exit) and `WirelessRadioManagerd` is running.
- The kernel has created the full Wi-Fi interface family: `en0`, `ap1`,
  `awdl0`, and `llw0`.  `en0` is `UP,RUNNING`, so this is not a missing-driver
  problem.
- `en0` has only the self-assigned address `169.254.15.162`, link quality is
  unknown, and media reports none.  The hardware/driver exists, but there is no
  successful infrastructure association/DHCP lease in this capture.
- `/var/preferences/SystemConfiguration/com.apple.wifi.plist` exists.  The next
  Wi-Fi probe must capture a join attempt and determine whether it fails at the
  AirPort keychain read/write.  Only that result would justify porting the
  experimental file-backed `wifid` shim from `usbliter8-fun`; do not patch the
  interface or driver path.
- User observation after this capture: the Settings Wi-Fi switch does not stay
  on / Wi-Fi never enters the enabled state.  This moves the first diagnostic
  boundary earlier than credentials or DHCP.  Capture `wifid`,
  `WirelessRadioManagerd`, WiFiManager and Apple80211 messages while changing
  the switch, and inspect the requested/current power-state values.  Do not
  install the AirPort password shim until the radio can remain enabled.
- Focused toggle capture attempted on 2026-10-21. Pairing validated, but
  `com.apple.os_trace_relay` refused the syslog session with
  `Password protected (-17)` even while the UI was unlocked. This is another
  consequence of the emulated/broad AKS lock state. Root SSH confirmed that
  `wifid` did not crash or restart (`runs = 1`, same PID) and `en0` remained
  `UP,RUNNING` with the same 169.254 address after the failed UI toggle.
  Persisted tracev3 data exists under `/private/var/db/diagnostics`, but the
  phone has no `log` client. Either repair the lock-state contract enough for
  os_trace_relay, or extract/decode the persisted log archive before choosing
  a Wi-Fi patch.
- The complete persisted diagnostics directory was pulled over root SSH to
  `logs/diagnostics/wifi-toggle-20261021/diagnostics.tar` (419,133,440 bytes,
  125 source files). SHA-256:
  `4589bd1500eebb1e66f23e9cb59f00915cc40943064ff867cf2210f139416c5d`.
  `file-list.txt` and `SHA256SUMS` sit beside it. Preserve this capture and
  decode its tracev3 store rather than repeating the toggle without logging.
- Decoded the newest Persist segment with Mandiant `macos-UnifiedLogs` after
  also pulling `/private/var/db/uuidtext`. The exact switch sequence at
  00:50:40, 00:50:44 and 00:50:47 UTC is now conclusive:

      -[WFClient setPoweredToggle:handler:] ON
      WiFiManagerSetPowerExt: device hasn't been unlocked since boot.
      Overriding power state to OFF.

  A later state report agrees:

      __WiFiManagerSetEnableState: state TRUE, manager->enable.setting TRUE,
      manager->unlockedSinceBoot FALSE
      WiFiDeviceManagerStartAutoJoinAndReply: AUTO-JOIN: FAILED to start

  Bluetooth logs independently report `_powerMonitor.firstUnlocked no`.
  Therefore Wi-Fi hardware, firmware power control, DHCP and AirPort password
  storage are not the current blocker. The broad AKS externalMethod stub
  returns success without writing selector outputs, so the userspace
  first-unlock observation remains false. This also explains os_trace_relay's
  `Password protected (-17)` refusal.
- Decoded artifacts:
  `logs/diagnostics/wifi-toggle-20261021/decoded/persist-32.jsonl` and
  `decoded/wifi-events.tsv`. The raw UUID catalog is `uuidtext.tar`, SHA-256
  `2f81943645186a8920a290a114a1cb3271a0845fa35b44d124ecc806b5f0fc7b`.
- Required correction: replace the indiscriminate AKS entry return with
  selector-aware output contracts. Selector 7 must return a state with the
  unlocked-since-boot bit set, while selector 17 must independently return the
  no-passcode lock state. Preserve the no-lock Setup result while restoring
  actual output writes; only after both read-side probes agree should Wi-Fi be
  retested. Do not install the Wi-Fi keychain shim for this failure.

#### Cellular

- `CommCenter`, `CommCenterMobileHelper`, and all `pdp_ip0..pdp_ip7`
  interfaces exist.  CommCenter is stable (`runs = 1`) and exposes its ARI and
  CoreTelephony XPC endpoints, so launchd/CommCenter startup is not the first
  blocker.
- Lockdown reports an empty `BasebandVersion`, IMEI and ICCID, with
  `kCTSIMSupportSIMStatusNotReady` and unknown tray state.
- The decisive local record is
  `/var/wireless/Library/Logs/log-bb-live-stats.txt`; every attempt reports:

      modem boot up failure [Baseband Firmware Path Not Found]

  Its header is `AP=22H374; BB=; Machine=iPhone11,8`.
- `/var/wireless/baseband_data` is mounted but empty.  Therefore the first
  cellular fix is earlier than the upstream CommCenter data-context gates or
  FDR calibration shim: restore/stage the expected baseband firmware payload
  and make the normal-boot firmware path resolvable.  After the modem boots and
  publishes a BasebandVersion, repeat SIM/FDR/ARI diagnostics before applying
  later cellular patches.

#### Files and DeviceTree implication

- `fileproviderd` is running, but there is no
  `/private/var/mobile/Library/Application Support/FileProvider` state and no
  LocalStorage extension process.  This supports the upstream no-persona
  diagnosis and the guarded `l8files` port remains the direct Files.app task.
- These live results do not justify globally enabling
  `no-effaceable-storage` or `boot-ios-diagnostics` yet.  Test those two
  DeviceTree properties as an A/B artifact: they may alter keybag/persona
  initialization, but they cannot supply a missing baseband firmware path and
  they do not create genuine content-protected storage.

## 2026-10-21: selector-aware AKS result, working Wi-Fi, and Sileo queue diagnosis

### AKS correction and Wi-Fi result

- Removed the broad `AppleKeyStoreUserClient::externalMethod` success return.
  That patch suppressed failures but did not populate selector output buffers;
  userspace consequently observed `unlockedSinceBoot = false`, blocked Wi-Fi,
  prevented `os_trace_relay` access, and produced misleading no-passcode UI.
- The normal kernel now keeps the selector-specific behavior:
  - selector 7 returns DER state with `bh = -6` and `ss = 4`, preserving the
    first-unlock bit consumed by `MKBDeviceUnlockedSinceBoot`;
  - selector 17 returns the complete 30-byte DER set containing
    `bh = -6`, `ss = 4`, and `sls = 3`.  `sls` is the second decoded word and
    is the `ls` value consumed by `MKBGetDeviceLockState`; value 3 maps to
    no-passcode in SpringBoardFoundation.
- The selector-17 redirect is at kernel file offset `0x0223F51C` and its
  response handler is at `0x02241238`.  Its pre-image was corrected from
  `ff6300f9` to the exact XR 22H374 instruction `ff7300f9`.
- The corrected patch is in commit `7186da1` (`Use selector-aware AKS state
  shims`).  The rebuilt normal bootset booted without the password/PIN sheet.
- Live result after that boot:
  - pairing validates;
  - `idevicesyslog` works instead of returning `Password protected (-17)`;
  - Wi-Fi can be enabled and operates normally;
  - Photos launches and works.
- This confirms that the Wi-Fi fault was the missing selector output, not an
  AirPort password/keychain shim or radio firmware problem.  Preserve the
  selector-aware AKS implementation; do not restore the broad externalMethod
  return-zero patch.
- Remaining keybag limitation: securityd still reports locked-keychain errors
  such as `ks_crypt ... e00002e2` and OSStatus `-25308`.  The read-side AKS
  observations are now coherent enough for first-unlock consumers, but this is
  not a real SEP/keybag unlock or durable credential transition.
- 22H374 selector-11 trace (read-only; no patch change): the exact device
  AppleKeyStore.framework exports `_aks_wrap_key` at `0x224293520` and
  `_aks_unwrap_key` at `0x224292db0`; the wrappers call `IOConnectCallMethod`
  with selectors 10 and 11 respectively.  `securityd`'s `ks_crypt` at
  `0x1000171f4` invokes `aks_unwrap_key` at `0x100017434` on the failing
  decrypt path.  In the kernel `AppleKeyStoreUserClient::externalMethod`, the
  selector-11 switch case starts at `0xfffffff009242268`, calls
  `sub_FFFFFFF00923A0B4`, and propagates its nonzero return.  That helper
  calls the keybag backend and translates its signed status through
  `sub_FFFFFFF00924AD30`; the table at `0xfffffff00752f190` maps backend
  `-2` to `0xe00002e2`.  The trace does **not** yet establish why the backend
  returns `-2` (missing key material, locked bag, or another backend failure).
  A blanket success return for selector 11 would leave its required key
  output unwritten and is not a valid fix.

### Bluetooth and camera remaining work

- Opening the Bluetooth pane still hangs Settings after the selector-35 test.
  The live capture `logs/syslog/bluetooth-selector35-20261022.log` records
  repeated `securityd` `ks_crypt ... e00002e2` / `-25308` locked-keychain
  errors, then `bluetoothd` `Failed to set new identity root!` and restart
  reason 130.  Selector 35 did not remove the passcode prompt: that was
  already fixed by selectors 7 and 17.  Selector 35 also did not resolve this
  Bluetooth failure.  The next step is to identify the selector-11 backend
  `-2` source and compare actual keybag/SEP availability; do not infer it from
  the read-only state shims or blanket-success the unwrap method.
- Camera remains separate from the former Wi-Fi gate.  Its observed failure is
  `AppleH10CamIn::ISP_Init - ANE power state is not ready`, result
  `0xe00002d9`, followed by `PowerUpFailure`/`SequenceError`.  Photos working
  does not resolve this ISP/ANE power-up path.

### Sileo: fixed repository-list error

- Sileo initially showed three upgrades but failed with a missing directory
  named like `apt.procursus..._Packages`.  Standard `apt-get update` populated
  `/var/jb/var/lib/apt/lists`, but patched Liter8 Sileo maintains a separate
  Data-backed list directory.
- Matched Xplo8E/Liter8's bootstrap contract on the live device:
  - created `/var/jb/var/lib/apt/sileolists/partial`;
  - set `sileolists` and `partial` to numeric owner `501:501`, mode `0755`;
  - created `/var/jb/var/cache/apt/archives/partial`;
  - set `archives` and `partial` to `501:501`, mode `0755`;
  - created root-owned `/var/jb/.installed_usbl8r`;
  - verified `/Applications/Sileo.app/giveMeRoot` is root-owned and setuid
    mode `4755`;
  - verified the Sileo executable contains both private patches:
    `/var/jb/.installed_usbl8r` and `000501:000501`;
  - ran `apt-get update`, re-registered Sileo, and restarted SpringBoard.
- This fixed the missing-Packages-folder error: the three upgrades now enter
  Sileo's queue.
- `apt-get -s upgrade` resolves successfully.  The CLI package database and
  repository are usable, so installing the packages manually would only hide
  the remaining Sileo integration fault and is not the intended fix.

### Sileo: remaining queue/root-spawn fix

- The Sileo queue currently stalls and presents no usable Confirm action.
  `giveMeRoot` itself executes and rejects a non-Sileo parent as expected, so
  its setuid mode and parent check are intact.
- The missing upstream prerequisite is persona 99.  Xplo8E/Liter8 explicitly
  creates persona ID 99 at every boot because Sileo's `spawnAsRoot()` uses
  `posix_spawn` with persona 99 and UID/GID 0.  Personas are kernel state and
  do not survive reboot.
- The installed `/usr/local/bin/xra1n-jbboot` currently logs only PATH-link
  preparation and sandbox-token refresh.  It has no persona creation step.
  No `personaalloc` helper exists on the installed System, and launchd/device
  diagnostics contain missing-persona errors.  This is the current leading
  cause of the silent queue stall.
- Required implementation:
  1. add a signed persona-99 allocator (or equivalent `kpersona_alloc()` code
     directly in `xra1n-jbboot`);
  2. invoke it on every normal boot before Sileo can perform an install;
  3. retain the kernel `spawn_validate_persona` UID/GID policy patches;
  4. log and verify `xra1n-jbboot: persona 99 created`;
  5. test a Sileo install/upgrade through the UI, including download,
     privileged dpkg execution and completion, rather than merely checking
     that an item enters the queue.
- After live validation, fold all of this into
  `install_sshrd_userland.py`: install the updated signed jbboot artifact,
  preserve its launchd-cache job, prepare both Sileo directories and owners,
  create the private marker, verify `giveMeRoot` mode/ownership, and fail the
  installer when any prerequisite is absent.  Post-boot verification must read
  `/var/mobile/xra1n-jbboot.log` and require the persona-success marker.

### Sileo/JBBoot validation result — fixed

- Implemented persona allocation directly in `jbboot/xra1n_jbboot.c`.  The
  helper resolves `kpersona_alloc` and `kpersona_info`, accepts an already
  existing ID 99, otherwise tries the upstream-compatible persona types and
  verifies that allocation produced exactly ID 99.
- The JBBoot workflow now signs the universal arm64/arm64e executable with the
  minimal working persona-management entitlement set.  Verification thins the
  universal binary and compares the entitlement plist independently for both
  slices (running `ldid -e` directly on a fat binary emits concatenated plists
  and is not a valid plist verification).
- Validated artifact:
  - commit: `81dfa979d12b11d554de4a7f90afbf59569a21ee`;
  - workflow run: `39538928173`;
  - SHA-256: `8d6f719206c8245b9ce1364fec0877b6503aa5f1c2dc803c7288badda714e1bb`;
  - local artifact:
    `work/artifacts/xra1n-jbboot/81dfa979d12b11d554de4a7f90afbf59569a21ee/xra1n-jbboot`.
- Replaced `/usr/local/bin/xra1n-jbboot` through SSHRD by rewriting the
  existing System inode.  The previous binary is preserved on Data as
  `/xra1n-jbboot.before-persona99`.
- Normal-boot proof in `/var/mobile/xra1n-jbboot.log`:

      xra1n-jbboot: persona 99 ready (type=3 name=sileo)
      xra1n-jbboot: persona 99 created
      xra1n-jbboot: sandbox read token refreshed

  Launchd reported one run and exit status zero.  Sileo then completed its
  queued upgrade and completed a new `neofetch` installation.  This validates
  the complete Sileo path: repository list, archive download, persona root
  spawn, privileged dpkg execution and completion.  Mark the Sileo queue/root
  spawn issue fixed.
- Installer integration still required: point `install_sshrd_userland.py` at
  the validated JBBoot artifact/hash, verify its persona marker strings and
  entitlements offline, install it inode-preservingly, prepare the Sileo list
  and archive directories as `501:501`, create `.installed_usbl8r`, verify
  `giveMeRoot` as root:root/4755, and require the persona-success line during
  the post-normal-boot check.

### Rootless script interpreter compatibility

- The Sileo-installed `neofetch` initially failed with:

      /tmp/bin/neofetch: /usr/bin/env: bad interpreter: No such file or directory

  Its package installs `#!/usr/bin/env bash`, while Procursus provides `env`
  at `/var/jb/usr/bin/env`; PATH links cannot satisfy an absolute shebang
  interpreter lookup.
- The live fix changed only the first line to
  `#!/var/jb/usr/bin/env bash`.  `neofetch --version` then returned
  `Neofetch 7.3.11`.  `/var/log/lastlog` was also created root:root/0644 to
  remove Dropbear's warning on every SSH login.
- This is not a general tweak-loading failure.  Mach-O executables and dylib
  tweaks loaded by ElleKit do not use shebang interpreters and are unaffected.
  It affects command packages whose executable entry point is a script with an
  unavailable absolute interpreter, especially `#!/usr/bin/env ...`.  A script
  can still fail later if the named rootless interpreter itself is not
  installed.
- Do not special-case only neofetch in the final installer.  During SSHRD
  installation, provide a durable compatibility path for `/usr/bin/env` to the
  Procursus `/var/jb/usr/bin/env` (after confirming an absolute System-to-Data
  symlink survives normal boot and execution), or install a narrowly scoped
  dpkg post-invoke shebang normalizer.  Prefer the compatibility symlink because
  it also covers packages installed later; retain a guarded pre-image/type
  check and refuse to replace a real Apple `/usr/bin/env` if a future build
  contains one.  Validate with a fresh unmodified `#!/usr/bin/env bash` probe,
  not merely the already-rewritten neofetch file.
- Add idempotent creation of `/var/log/lastlog` to the installer as a separate
  shell-quality fix.

## 2026-10-21 fresh erase baseline and completed bootstrap automation

The new erase restore used ticket SHA-256
`e10f03d104be0679a76734f1f34d904eea8671d898243a95285d68cde87ca64e`.
The corrected restore ramdisk allowed the native baseband update path to run;
the restore log contains two BasebandData/TSS rounds, received Baseband SHSH
blobs, `Updating Baseband completed`, and `Restore Finished`.

`install_sshrd_userland.py` now reproduces the complete working userland state
instead of relying on manual SSHRD edits. It pins and validates every artifact,
backs up recognized originals on Data, skips an already matching complete
component, upgrades only known hashes, and rejects unknown or mixed states.
The successful fresh-device installation includes:

- Procursus and rootless runtime compatibility;
- the Setup and daemon patches;
- the final selector-aware AKS-compatible pairing chain: weak-loaded
  `xra1n-pair.dylib` and `xra1n-coreauth.dylib`, their guarded marker, and the
  exact patched lockdownd/coreauthd images;
- the validated patched `/sbin/launchd` plus `/usr/lib/lhook`;
- key-only normal-boot Dropbear, persistent host keys, pinned client key, and
  the required shell command binaries;
- a Version 7 launchd cache with both `com.xra1n.jbboot` and `com.dropbear`
  (630 jobs, installed cache SHA-256
  `b11574c273c6b801eacf478175914c3d94f855039108bfa9867d988d82fe3e0d`);
- JBBoot persona 99, PATH links, sandbox token and one-time app registration;
- Sileo, 40 staged Apple apps and the five ScreenTime/FamilyControls overrides.

The subsequent normal boot proved the runtime results:

- the Trust dialog appeared and host pairing succeeded;
- `idevicepair validate` succeeded after pairing;
- `ideviceinfo -k ProductVersion` returned `18.7.10`;
- key-only normal root SSH returned UID/GID 0;
- Dropbear's compiled PATH resolved `apt-get` and `dpkg` through `/tmp/bin`;
- Sileo, pairing and SSH are now baseline-complete rather than remaining work.

### Remaining hardware/service work

#### Confirmed normal-boot bugs (2026-10-23)

The current selector-aware AKS kernel boots without the passcode prompt, reports
`deviceLocked: 0` and `deviceFirstUnlocked: 1`, permits USB Lockdown pairing,
and enables both Wi-Fi and Bluetooth.  The following records the current bug
status; unresolved items must not be marked fixed merely because selector 7
returns `noPin | beenUnlocked` (`0x6`):

- **Wallpaper:** wallpaper creation/selection still does not work.  The empty
  or unavailable PosterBoard store and the earlier "Wallpaper limit reached"
  behavior remain unresolved.
- **Camera:** Camera still does not produce a usable capture and may fail or
  crash independently of the Photos application's library access.
- **Bluetooth pairing: resolved.** Pairing with an Android device succeeds.
  The earlier failing target was another iPhone.  Its trace contained device
  discovery, an SDP/service connection, missing pre-existing keys,
  `STATE_IDLE`, and an L2CAP PSM 1 timeout, but no
  `CBMsgIdPairingAgentPair`, SSP request, numeric comparison, new link key, or
  pairing-completed event.  iOS does not offer ordinary Bluetooth Classic
  Settings pairing between two iPhones, so that result was an unsupported
  target test rather than an AKS, Keychain or `bluetoothd` failure.  Do not add
  a Bluetooth hook for it.
- **App Store downloads: resolved.** With `l8persona` armed, respawning
  `installd` and `installcoordinationd` loads the tweak and installs complete
  after the native Apple-account password flow. The flow can be slow. The
  broad LocalAuthentication translation was tested and removed because it
  suppressed that password flow. Do not reintroduce it or a patched system
  persona daemon.

#### Two focused Liter8 compatibility fixes

1. **`l8persona.dylib` — App Store/install persona fallback**
   - Inject only into `installd` and `installcoordinationd`.
   - Hook `-[MIAppIdentity resolvePersonaWithError:]` only when its exact ABI
     matches and `/var/jb/.liter8-persona` is a secure root-owned marker.
   - Substitute `CONTAINER_PERSONA_PRIMARY` only for the measured placeholder
     forms while the stock personal-persona lookup is absent; preserve all
     explicit persona requests and unrelated failures.
   - Local 22H374 validation confirmed `MIAppIdentity`, every required setter,
     `MIUserManagement`, the `B24@0:8^@16` method ABI, and the exported
     `_CONTAINER_PERSONA_PRIMARY` constant. Runtime injection and one App Store
     install remain the acceptance test.

2. **PhotoForce (`pfruntimeprobe` + `pfwatch`) — wallpaper store repair**
   - This fix is two ad-hoc-signed executables, not an injected dylib. `pfwatch`
     observes PosterBoard PIDs and invokes the probe to replace the cached
     PosterFoundation URL-resource contract in that process.
   - Exact 22H374 analysis found `_PFPosterPathURLResourceValues` at
     `0x2592e0148`, once slot `0x268f55d58`, and object slot `0x268f55d50`.
   - Liter8's original resolver assumed `object == once + 8`; 22H374 reverses
     those globals. The repository resolver now accepts either adjacent order
     while still requiring one unique pair. The local matcher passes.
   - Entitlement-bearing builds were rejected before `main`. An entitlement-free
     probe reached `task_for_pid(PosterBoard)` but returned `KERN_FAILURE (5)`.
     The workflow now signs both PhotoForce binaries with `ldid -S -Cadhoc`,
     without task-port entitlements. The XR kernel patcher redirects the
     terminal denial branch of AMFI's get-task policy to its existing success
     cleanup (`0x020834B0`, stock `0x17FFFFF1`). Liter8-iPad8's TASKAC2 analysis
     identified another, separate XNU rejection in `task_conversion_eval_internal`:
     an inline Developer Mode byte check, not the exported accessor. For XR
     22H374, IDA confirms the corresponding compare at VA `0xFFFFFFF007E622D8`
     (file offset `0x00E5E2D8`, stock word `0xEB01001F`). The guarded patch changes
     only that compare to `CMP XZR,XZR` (`0xEB1F03FF`), taking its existing
     zero-result edge while preserving prior task validation and later
     platform/corpse checks. It is now in `patch_kernel.py`, but remains
     hardware-unverified. Do not call wallpaper repair fixed until `task_for_pid`
     returns a live port and the probe successfully reads/writes the intended
     PosterBoard state.
   - The remaining XR task-access path is now ported as guarded records as well:
     Seatbelt `mpc_ops[97]`, `[98]`, and `[157]` resolve to file offsets
     `0x02E62740`, `0x02E625C8`, and `0x02E5C494`. Each keeps a `BTI C` landing
     pad and returns errno zero. The later caller-versus-victim comparison at
     `0x00E5E30C` is changed from `CMP X0,X1` to `CMP XZR,XZR`, taking the
     existing self path before the platform-asymmetry rejection. The exact iPad
     resolver expects two conversion sites; XR has only this one reviewed
     semantic equivalent, so no second offset was fabricated.
   - Acceptance: watcher remains alive, its log records ensure exit 0, the
     default wallpaper appears, and creating/selecting a wallpaper survives a
     PosterBoard restart.

The two fixes are built by `XR Runtime Fixes` with an iOS 18 deployment target.
The artifact contains the universal persona tweak and filter plist plus the
universal PhotoForce probe/watcher. Keep the persona marker reversible and test
PhotoForce independently.

#### Camera

- Photos now launches and reads the library, but Camera still does not work.
- Treat this as a camera/ISP/ANE service problem, not a LaunchServices, Sileo,
  pairing or generic app-registration problem.
- Capture a bounded normal-boot syslog while opening Camera and correlate
  `cameracaptured`, `mediaserverd`, ISP, ANE, Fig and sensor errors. Check
  whether the daemon crashes, cannot obtain a hardware service, or rejects a
  calibration asset. Do not add broad return-success patches.

#### Bluetooth

- **Fixed:** the Bluetooth Settings pane opens, the controller enables, device
  discovery works, and pairing with a supported Android target succeeds.
- The failed iPhone-to-iPhone attempt was fully traced and never entered the
  pairing state machine; it was ordinary unsupported peer behavior, not a
  missing-link-key persistence defect.
- Preserve the current AKS selector configuration.  It enables Bluetooth while
  retaining coherent first-unlock state, USB pairing and Wi-Fi.  Do not return
  to the unconditional `externalMethod` success patch.

#### Cellular and CommCenter

- Baseband firmware staging is now fixed at erase-restore time. First measure
  the new normal boot before modifying CommCenter: query BasebandVersion,
  IMEI/ICCID, SIM/tray state, CommCenter process stability, Baseband Data
  contents, and the current baseband live-stat log.
- The locally pulled XR 22H374 CommCenter is
  `bins/CommCenter`, SHA-256
  `77ef543a172aa293c1cbc43829789a8b83acafb468bf558b20b57dc2a7d8c44e`.
- Xplo8E's later data-context work patches the CommCenter executable; it is
  not merely a plist edit. Port it only if the newly restored modem boots and
  diagnostics reach the matching data-context gate. The upstream cellular
  series also includes separate FDR calibration and ARI work and does not
  establish a universally complete cellular-data fix.
- Required order: verify modem firmware boot and BasebandVersion, then SIM and
  FDR state, then ARI registration, and only then evaluate exact-build
  CommCenter gates. Every binary change needs a 22H374 pre-image, pinned hash,
  signature repair, Data backup and read-back check.

### Packaging follow-up

- Packaging validation is complete. Over normal root SSH, `apt-get update`
  and the available upgrades completed successfully. Sileo remained usable,
  completed package operations, and installed `neofetch`; the upgraded
  `neofetch` runs successfully. Treat APT metadata, dependency handling,
  persona-backed Sileo installation and script-interpreter compatibility as
  baseline-complete.
- Commit the current installer changes after one second SSHRD run proves every
  newly automated component reports the matching-state skip path.

## 2026-10-22: AKS selector experiment matrix

Keep these normal-boot results as the authoritative comparison; the artifact
name `kernelcache.patched-0x891-full-aks.im4p` is reused and therefore does not
identify the experiment by itself.

| AKS externalMethod behavior | Passcode / first unlock | USB pairing | Wi-Fi | Bluetooth |
|---|---|---|---|---|
| Structured selectors 7, 17 and 35; every other selector returns success | No passcode | Works | Does not enable | Pane opens and Bluetooth enables |
| Structured selectors 7, 17 and 35; selector 10 returns success; all others native | No passcode | Not regressed in this probe | Works | Settings pane hangs |
| Structured selectors 7, 17 and 35; selectors 0–16 except 7 return success; selectors above 16 native | Passcode shown at boot | Not tested | Not tested | Not tested |
| Structured selectors 7, 17 and 35; selectors 0–6 and 10 return success; all others native | Passcode shown at boot | Not tested | Not tested | Not tested |
| Structured selectors 7, 17 and 35; selectors 0–3 and 10 return success; all others native | Passcode shown at boot | Not tested | Not tested | Not tested |
| Structured selectors 7, 17 and 35; selectors 0–1 and 10 return success; all others native | Passcode shown at boot | Not tested | Not tested | Not tested |
| Structured selectors 7, 17 and 35; selectors 0 and 10 return success; all others native | Passcode shown at boot | Not tested | Not tested | Not tested |
| Structured selectors 7, 17 and 35; selectors 1 and 10 return success; all others native | No passcode | Not tested | Works | Settings pane still hangs |
| Structured selectors 7, 17 and 35; selectors 1–16 except 7 return success; selector 0 and selectors above 16 native | Not explicitly rechecked | Not tested | Enables | Pane opens and Bluetooth enables |

Conclusions:

- The structured 7/17/35 outputs remain necessary for coherent first-unlock
  and no-passcode behavior; empty success cannot replace their output ABIs.
- Selector 10 alone does not provide the behavior that lets Bluetooth open.
- Broad empty success for at least one selector in
  `{0–6, 8–9, 11–16}` destroys the coherent no-passcode state even though
  selectors 7, 17 and 35 remain structured. Selector 10 is excluded because
  its isolated test retained the no-passcode state.
- The 0–6 plus 10 probe also shows the passcode, narrowing the destructive
  selector to 0–6. Selector 10 remains the known-safe control. The next probe
  is selectors 0–3 plus 10, with every other selector native except the
  structured 7/17/35 implementations.
- The 0–3 plus 10 probe still shows the passcode, narrowing the destructive
  selector to 0–3. The next probe is selectors 0–1 plus 10.
- The 0–1 plus 10 probe still shows the passcode, narrowing the destructive
  selector to either 0 or 1. Test selector 0 plus the known-safe selector 10
  next; its result identifies the exact selector.
- Selector 0 plus 10 shows the passcode. Therefore empty success for selector
  0 is sufficient to produce the regression when the remaining selectors are
  native. This does not prove selector 0 is the only interaction in the fully
  broad configuration. Confirm selector 1 plus 10 separately before resuming
  the Bluetooth-enabling range search.
- Selector 1 plus 10 boots without a passcode and Wi-Fi works, while the
  Bluetooth pane still hangs. Selector 1 is safe in this probe but is not the
  missing Bluetooth behavior. Keep selector 0 native from now on. Resume the
  Bluetooth bisection with selectors 1–16 broadly successful except structured
  selector 7; keep structured 17/35 and selectors above 17 native.
- The 1–16-except-7 probe enables both Wi-Fi and Bluetooth. Relative to the
  selector-1-plus-10 control, the Bluetooth-enabling selector is therefore in
  `{2–6, 8–9, 11–16}`. Next test the lower candidate subset `{2–6, 8–9}` while
  retaining safe controls 1 and 10; keep 0 native and 7/17/35 structured.
- The broad-success comparison proves at least one additional selector affects
  Bluetooth, while one or more broadly stubbed selectors break Wi-Fi.
- Continue with a guarded selector-range bisection while always preserving the
  structured implementations of 7, 17 and 35. Record each generated kernel by
  selector set and SHA-256 rather than by the reused output filename.
