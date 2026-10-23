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
- PhotoForce: `pfruntimeprobe` and `pfwatch`. This is not a dylib; the entitled
  watcher applies a runtime PosterFoundation cache repair to every PosterBoard
  process. Its resolver accepts both observed adjacent-global layouts while
  requiring one unique match.

All are built with an iOS 18 deployment target. The persona tweak carries no
entitlements. The PhotoForce executables are signed exactly as Liter8 does,
using `ldid -S... -Cadhoc`, with only the task-port entitlements declared in
`pfruntimeprobe.ent`.
