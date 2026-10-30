# XRa1n normal-boot finalization

`install_sshrd_userland.py --install` stages Procursus and a pinned ElleKit deb
from SSHRD. It intentionally does not execute either package's booted-userland
setup. After the first **normal boot**, run:

```sh
python3 tools/xra1n-finalize.py --check   # read-only; exit 2 means pending
python3 tools/xra1n-finalize.py --apply   # complete pending steps
python3 tools/xra1n-finalize.py --check
```

The script uses `tools/ssh-normal.sh` for the USB SSH session. It rejects
SSHRD. It does not change injection state, respring, replace System files, or
run a blanket `uicache -a` scan. It can target-register the pinned system
Sileo after APT has registered Procursus's second Sileo copy.

`--apply` verifies the Procursus prep script's pinned SHA-256, makes a retry
copy, runs it with `NO_PASSWORD_PROMPT=1`, then requires the two password
databases, zsh shell entries, and removal of the original prep script. On
failure it preserves/restores the verified retry copy rather than claiming
completion. The script itself runs the package post-install setup and removes
itself; the finalizer does not bypass those steps.

ElleKit is checked through `dpkg-query`. If absent, `--apply` verifies and
installs the pinned staged deb. It validates the TweakLoader link and applies
an XRa1n-owned `dpkg-divert` to keep ElleKit's competing loader out of `rc.d`
across package upgrades. An unexpected package version or diversion destination
stops the operation rather than being overwritten.
An existing compatible Liter8 diversion is recognized without replacing it.

After ElleKit, the finalizer runs `apt-get check`. Only if dependencies are
broken does `--apply` refresh APT's package lists, simulate
`apt-get -f install`, refuse any removal or a plan changing more than 20
packages, then perform the repair and recheck. This refresh lets APT resolve
the packaged Sileo and libkrw provider required by the staged bootstrap. A
healthy installation is never modified and does not contact repositories in
this step.

The final step installs `tools/xra1n-tweaksign.sh` and an XRa1n-owned dpkg
post-invoke entry. The hook signs only regular, non-ad-hoc TweakInject dylibs,
preserving their identifiers and entitlements. A failed signing attempt leaves
the original file in place and does not fail `apt`/Sileo. `--check` lists any
pending dylibs without signing them. If `ldid` is absent, `--apply` installs
the pinned Procursus `ldid=2.1.5-procursus7` package noninteractively and
verifies its dpkg state before checking signatures.

The finalizer also checks the pinned local PreferenceLoader 2.3.3 deb. On
`--apply` it installs the package only if absent, verifies the package-owned
MobileSubstrate dylib and filter, and ensures verified copies exist in
`/var/jb/usr/lib/TweakInject/` for ElleKit. It signs the copied dylib through
the existing tweak-sign hook when needed. `--check` reports missing or
unsigned copies without modifying them; an unexpected package version or file
hash is rejected. This does not enable lhook or respring.

Procursus's `essential` package requires `org.coolstar.sileo` in dpkg. The
dependency repair installs that rootless package under `/var/jb/Applications`,
which can take over the `org.coolstar.SileoStore` registration from XRa1n's
patched `/Applications/Sileo.app` and make its icon launch the wrong binary.
After all package operations, `--apply` verifies the pinned system executable
hash, runs targeted `uicache -p /Applications/Sileo.app` only if necessary, and
checks that `uicache -i org.coolstar.SileoStore` reports the system path.
`--check` reports a mismatched registration without changing it. The Procursus
package remains installed so APT dependencies stay healthy.
