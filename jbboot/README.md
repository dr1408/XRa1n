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
It is an executable, not a dylib, and contains no injection or interposition.
