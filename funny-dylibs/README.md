# Funny dylibs

Small native iOS dylibs used to validate a hook before adding functional
interposition.

`l8pair-probe.dylib` is intentionally constructor-only.  When weak-loaded by
`lockdownd`, it appends a line to:

```text
/private/var/root/Library/Lockdown/xra1n-l8pair-probe.log
```

It also emits `XRA1N_L8PAIR_PROBE` through `syslog`.  It does not hook Security
APIs or change pairing behaviour.  Its artifact contains both arm64 and arm64e
slices and is ad-hoc signed without entitlements.
