# xra1n-jbboot

`xra1n-jbboot` is a signed, System-resident iOS executable run once by a
launchd-cache job during normal boot. It recreates the two ephemeral links
already named by Dropbear's compiled-in PATH:

```text
/private/var/tmp/bin     -> /var/jb/usr/bin
/private/var/tmp/usr/bin -> /var/jb/usr/sbin
```

It waits for the Procursus directories on Data, accepts only exact root-owned
links, and moves unexpected path occupants aside rather than deleting them.

The same process issues a `com.apple.app-sandbox.read` extension for
`/private/var/jb` and atomically publishes it as
`/private/var/tmp/sbext.token`. `systemhook` consumes that token inside
`iconservicesagent`, allowing rootless application bundles such as Sileo to be
indexed. The token is recreated on every boot because `/private/var/tmp` is
ephemeral.

After completing the one-time setup, it replaces itself with the native
`/usr/local/bin/pfwatch` process.  This keeps PosterBoard repair under the same
launchd job, matching Liter8's `exec pfwatch` lifecycle without adding a second
`com.jbboot` service or an intermediate wrapper script.

It is an executable, not a dylib, and contains no injection or interposition.
