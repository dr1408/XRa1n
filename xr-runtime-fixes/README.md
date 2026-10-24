# XR runtime fixes

Native iOS hook payloads and runtime-repair tools used by XRa1n.

The pairing payloads are `xra1n-pair.dylib` and `xra1n-coreauth.dylib`.
Both artifacts contain arm64 and arm64e slices and are ad-hoc signed without
entitlements.

The workflow also builds reviewed Liter8 compatibility fixes for
iPhone11,8 / 22H374:

- `l8persona.dylib` plus its filter plist: a marker-gated
  `MIAppIdentity` fallback loaded only into `installd` and
  `installcoordinationd`.
- `l8localauth.dylib` plus its filter plist: a marker-gated
  LocalAuthentication error translator loaded **only into `nehelper`**. It
  maps the observed SEP-less ACM `-3` / LA `-1000` failure to
  `LAErrorPasscodeNotSet` for VPN-profile confirmation. Both the plist and
  constructor check the executable name; it does not load in App Store.
  Activation requires a root-owned, non-group-writable regular file at
  `/var/jb/.liter8-localauth`. The workflow builds it but does not install or
  activate it on a device.
- PhotoForce: `pfruntimeprobe` and `pfwatch`. This is not a dylib; the ad-hoc-signed
  watcher applies a runtime PosterFoundation cache repair to every PosterBoard
  process. Its resolver accepts both observed adjacent-global layouts while
  requiring one unique match.

All are built with an iOS 18 deployment target. The tweaks and PhotoForce
executables carry no entitlements. On XR 22H374 the entitlement-bearing PhotoForce
builds were rejected before `main`; the entitlement-free probe reached
`task_for_pid` but got `KERN_FAILURE (5)`. PhotoForce therefore uses
`ldid -S -Cadhoc` and is tested with the separate AMFI task-port kernel patch.
