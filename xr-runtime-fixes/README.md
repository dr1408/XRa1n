# XR runtime fixes

Native iOS hook payloads and runtime-repair tools used by XRa1n.

`l8pair-probe.dylib` is intentionally constructor-only.  When weak-loaded by
`lockdownd`, it appends a line to:

```text
/private/var/root/Library/Lockdown/xra1n-l8pair-probe.log
```

It also emits `XRA1N_L8PAIR_PROBE` through `syslog`.  It does not hook Security
APIs or change pairing behaviour.  Its artifact contains both arm64 and arm64e
slices and is ad-hoc signed without entitlements.

The workflow also builds two reviewed Liter8 compatibility fixes for
iPhone11,8 / 22H374:

- `l8persona.dylib` plus its filter plist: a marker-gated
  `MIAppIdentity` fallback loaded only into `installd` and
  `installcoordinationd`.
- PhotoForce: `pfruntimeprobe` and `pfwatch`. This is not a dylib; the ad-hoc-signed
  watcher applies a runtime PosterFoundation cache repair to every PosterBoard
  process. Its resolver accepts both observed adjacent-global layouts while
  requiring one unique match.

All are built with an iOS 18 deployment target. The persona tweak and PhotoForce
executables carry no entitlements. On XR 22H374 the entitlement-bearing PhotoForce
builds were rejected before `main`; the entitlement-free probe reached
`task_for_pid` but got `KERN_FAILURE (5)`. PhotoForce therefore uses
`ldid -S -Cadhoc` and is tested with the separate AMFI task-port kernel patch.
