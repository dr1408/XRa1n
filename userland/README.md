# XR 22H374 userland build

The `Build XR userland hook and patches` workflow runs directly from GitHub
Actions with no dispatch inputs. Only four exact pristine inputs are checked
in under `bins/`: `coreauthd`, `mobileactivationd`, `ctkd`, and `launchd`.
Large dyld caches and other local firmware files are not needed and are not
committed. The workflow checks each input against an embedded SHA-256; it
does not download a whole IPSW or use `ipsw` for analysis.

The workflow patches/signs the three daemons using
`patch_userland_bins.py`, creates the XR-specific weak-load `launchd` with
`patch_launchd_weakload.py`, repairs its stock code-signature page hashes,
then builds/signs Liter8's `lhook`, `systemhook`, and `sbextissue` sources.
All outputs are in a short-lived Actions artifact. The stock service cache
and its detached `.sig` are **not used** in this build: no XR-specific
jbboot/dropbear cache job has yet been validated. Deployment and enabling
the hook are separate steps.

Hook sources under `liter8/` are derived from the local Liter8 project and
retain its MIT license and copyright notice in `liter8/LICENSE`.
