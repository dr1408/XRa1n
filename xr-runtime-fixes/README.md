# XR runtime fixes

Native iOS hook payloads and runtime-repair tools used by XRa1n.

The pairing payloads are `xra1n-pair.dylib` and `xra1n-coreauth.dylib`.
Both artifacts contain arm64 and arm64e slices and are ad-hoc signed without
entitlements.

`build-userland.yml` now builds the matching release-only `pairing/lockdownd`
from the pinned 22H374 System original in `bins/lockdownd`. It adds exactly
one weak load, `/usr/lib/xra1n-pair.dylib`, repairs the preserved signature's
code-page hashes in GitHub Actions, and checks that the diagnostic
`l8pair-probe.dylib` load is absent. The SSHRD installer consumes the workflow
artifact and removes the known diagnostic dylib when upgrading the device;
the currently booted System binary is unchanged until that installation.

The workflow also builds reviewed Liter8 compatibility fixes for
iPhone11,8 / 22H374:

- `l8persona.dylib` plus its filter plist: a marker-gated
  `MIAppIdentity` fallback loaded only into `installd` and
  `installcoordinationd`. The SSHRD installer now provisions the root-owned,
  mode-0600 `/var/jb/.liter8-persona` marker after verifying the payload.
- `l8localauth.dylib` plus its filter plist: a marker-gated
  LocalAuthentication error translator loaded **only into `nehelper`**. It
  maps the observed SEP-less ACM `-3` / LA `-1000` failure to
  `LAErrorPasscodeNotSet` for VPN-profile confirmation. Both the plist and
  constructor check the executable name; it does not load in App Store.
  Activation requires a root-owned, non-group-writable regular file at
  `/var/jb/.liter8-localauth`. The SSHRD installer now installs the signed
  dylib and filter plist and provisions that marker at mode 0600.
- `l8files.dylib` plus its filter plist: XR-scoped LocalStorage persona
  compatibility, injected only into `fileproviderd` and
  `LocalStorageFileProvider`. The tested arm64/arm64e build and plist are pinned
  in the install bundle and SSHRD installer. The installer provisions the
  root-owned `/var/jb/.xra1n-files` marker (mode 0600), but does not enable
  lhook or restart any running process. After lhook is enabled in normal boot,
  existing fileprovider processes need a separate restart to load the tweak.
  This process-local fix does not provide a real kernel persona or iCloud Drive.
- PhotoForce: `pfruntimeprobe` and `pfwatch`. This is not a dylib; the ad-hoc-signed
  watcher applies a runtime PosterFoundation cache repair to every PosterBoard
  process. Its resolver accepts both observed adjacent-global layouts while
  requiring one unique match. The SSHRD installer now places the verified
  entitlement-free builds at their System paths, replacing only the known
  earlier entitlement-bearing builds.

The installer places all three filtered tweak pairs under
`/var/jb/usr/lib/TweakInject/`. It verifies hashes, filters, signatures,
ownership, modes and readback for the runtime payloads. It provisions secure
persona, LocalAuthentication and Files markers. It does not enable lhook or
restart `installd`, `installcoordinationd` or `nehelper`; those remain explicit
normal-boot actions.

All are built with an iOS 18 deployment target. The tweaks and PhotoForce
executables carry no entitlements. On XR 22H374 the entitlement-bearing PhotoForce
builds were rejected before `main`; the entitlement-free probe reached
`task_for_pid` but got `KERN_FAILURE (5)`. PhotoForce therefore uses
`ldid -S -Cadhoc` and is tested with the separate AMFI task-port kernel patch.

## XR VPN-profile confirmation: hardware result (2026-10-24)

On iPhone11,8 / iOS 18.7.10 (22H374), the `XR Runtime Fixes` workflow run
`40088992759` built the entitlement-free, arm64/arm64e `l8localauth.dylib`
(SHA-256 `a208623578cce5c12041200a736949c7bc1dfcf782209835515993157d658113`)
and its `nehelper`-only filter plist. They were installed to
`/var/jb/usr/lib/TweakInject/` via SSHRD; the root-owned, mode-0600
`/var/jb/.liter8-localauth` marker was enabled. Both installed files matched
the artifact hashes on readback.

The first VPN approval attempt still failed in the already-running `nehelper`
PID 125: `canEvaluatePolicy:2` returned LocalAuthentication `-1000` with ACM
`-3` at 11:25:02. With lhook enabled for that boot, restarting `nehelper`
created PID 607. During the next approval attempt, the device log recorded
`l8localauth: SEP-less ACM -3 -> passcode-not-set (pid 607)` at 11:29:18,
followed by NetworkExtension reporting that the VPN configuration was saved.
The operator confirmed the profile-add flow worked. Local evidence is in
`logs/syslog/live-20261024-112434.log` and
`logs/syslog/nehelper-retry-20261024-112853.log` (logs are not committed).

This validates VPN **profile creation** with the newly spawned, injected
`nehelper`; it does not establish VPN tunnel connectivity or prove behavior
after a later reboot. Because lhook is session-enabled, an existing `nehelper`
must be restarted after enabling injection before this tweak can affect it.

## XR PhotoForce boot-time confirmation (2026-10-24)

The earlier System-resident `/usr/local/bin/pfwatch` and
`/usr/local/bin/pfruntimeprobe` were **not** the entitlement-free builds: their
SHA-256 hashes were `d020cbe017d51ebb4150e72ea303cf980b506d283b80886ed36e8f3f1a19d770`
and `0b3288b7a612a57b83235560acfea0e9ef9312357b349c11169397828e454948`.
Both carried `task_for_pid-allow` and other task-port entitlements. The
`com.xra1n.jbboot` job reached `entering pfwatch` repeatedly, but the watcher
never remained running or logged a repair for the new PosterBoard PID. The
entitlement-bearing System probe was killed before it printed a result.

The separate entitlement-free
`/var/jb/usr/bin/pfruntimeprobe-no-ent` successfully repaired live
PosterBoard PID 446 in place (`mach_vm_write` and readback both succeeded;
`remote mutation: PASS`), without restarting it. To make this automatic at
boot, SSHRD replaced **both System paths** with the no-entitlement, ad-hoc-signed
arm64/arm64e builds from `work/photoforce-b9ca31a/`:

| System path | Installed SHA-256 |
|---|---|
| `/usr/local/bin/pfwatch` | `b4e460dd2aa6af4b181f1606bb00cb6a3162fef3da9b763727d34ba3cd81b822` |
| `/usr/local/bin/pfruntimeprobe` | `f26002769dd753e37604de6b63caa388a812de9a4dd856382beb66eb0e86912b` |

Both files were backed up at
`/mnt2/xra1n-backup/photoforce-no-ent-20261024-130854/`, written through
their existing System vnodes, synced, and read back byte-for-byte. The source
and readback-identical binaries have **no entitlements in either slice**. The
launchd cache still contained the `com.xra1n.jbboot` job pointing to
`/usr/local/bin/xra1n-jbboot`.

On the subsequent normal boot, serial log
`logs/serial/dcsd-20261024-131034.log` reached launchd and reported
`AOP IS READY`. Device readback confirmed the two System hashes above;
`pfwatch` remained running as PID 67 and automatically repaired the first
PosterBoard process, PID 231. `/private/var/mobile/pfwatch.log` recorded
`remote mutation: PASS; protection bucket removed, readability retained`
and `[pfwatch] target 231 ensure exit 0`. No manual PhotoForce apply or
PosterBoard restart was needed on that boot. A later Wi-Fi SSH check confirmed
`lhookctl status` was **disabled for this boot**, while `pfwatch` PID 67 and
PosterBoard PID 231 remained alive with the repaired contract. The operator
confirmed **camera, flash, and audio all work**. A later PosterBoard respawn
has not yet been hardware-tested; the watcher is designed to reapply to each
new PID.

The older `/var/jb/usr/bin/pfwatch` copy remained entitlement-bearing at the
time of this test; the boot job uses the verified System copy. Future
provisioning must not overwrite `/usr/local/bin/pfwatch` with that older build.
