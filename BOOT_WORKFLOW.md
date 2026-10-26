# XRa1n iPhone XR erase restore

Target: iPhone11,8 (`n841ap`), iOS 18.7.10 (`22H374`), CPID `0x8020`.

## Inputs

- A stock IPSW extraction already at `work/cfw`. From the repository root,
  run `mkdir -p work/cfw` and
  `unzip -q /path/to/iPhone11,8_18.7.10_22H374_Restore.ipsw -d work/cfw`.
  Confirm `work/cfw/BuildManifest.plist` exists directly in that directory.
  The scripts do not download or extract another IPSW and do not clone the
  8-GB directory.
- `kernelcache.patched-0x891-full-aks.im4p` and
  `RestoreRamDisk.patched.im4p` at the repository top level. Their SHA-256
  values are pinned by `prepare_erase_cfw.py`.
- The pinned Linux/aarch64 tools and Apple TSS CA bundle under
  `tools/host/linux-aarch64/`. Verify them with `python3 host_tools.py --check`.
  `XRA1N_TOOLS_DIR` can relocate the same hash-checked bundle; no
  `/picotool` directory is needed. See `tools/host/README.md` for the
  remaining system prerequisites.
- DCSD serial hardware is optional. Without `--serial`, boot arguments omit
  `serial=3` and no serial logger is started.

## Prepare without touching the phone

```sh
python3 run_restore.py --prepare-only
```

For DCSD capture, add `--serial` to preparation and restore commands. The
selected console mode is recorded and checked; `--reuse-prepared` must use the
same `--serial` choice as the preparation record.

This saves only the pristine iBSS, iBEC, DeviceTree, kernelcache and restore
ramdisk under `work/pristine-cfw/`, then prepares a separate patched iBSS raw
file. It changes these four CFW files **in place** after verifying their
stock BuildManifest digests and every prepared payload:

| Component | CFW path | Prepared input |
| --- | --- | --- |
| iBEC | `Firmware/dfu/iBEC.n841.RELEASE.im4p` | restore-patched pristine iBEC |
| DeviceTree | `Firmware/all_flash/DeviceTree.n841ap.im4p` | restore-patched pristine DeviceTree |
| KernelCache | `kernelcache.release.iphone11b` | pinned top-level kernel IM4P |
| RestoreRamDisk | `094-32147-038.dmg` | pinned top-level `rdsk` IM4P |

The `.dmg` path is an IM4P container in this IPSW; it is not converted to a
bare DMG. Patched iBSS stays outside CFW at
`work/restore/iBSS.restore.raw.patched` for the DFU handoff. Re-running the
preparation is allowed only if each CFW file is stock or exactly matches the
reviewed prepared result; unknown changes fail closed. Check without writes:

```sh
python3 prepare_erase_cfw.py --check
```

## Erase restore

With the target USBLiter8-pwned in DFU (`05ac:1227`), run:

```sh
python3 -u run_restore.py
```

Use `python3 -u run_restore.py --serial` only with a working DCSD logger on
`/dev/ttyACM0`. The default path verifies restore-OS USB enumeration and
`idevicerestore` completion without relying on serial output.

The runner prepares/verifies the CFW, pins the connected XR's ECID, and starts
DCSD serial logging only with `--serial`. It sends patched iBSS and calls `restore_with_ticket.py`
in **ticket-only** mode: the TSS proxy captures a fresh AP ticket but withholds
the response before `idevicerestore` can upload iBEC. It then builds
`work/restore-bootset/` with that ticket, sends its images through `irecovery`,
and runs `bootx`. Only after restore OS enumerates as `05ac:12a8` does it
refresh `usbmuxd` and launch `idevicerestore` in restore mode. The second TSS
proxy rejects an AP ticket that differs from the one used to boot restore OS.
Before the handoff, the runner reads the single USBLiter8-pwned XR's ECID from
DFU (or the already-patched Recovery device), validates its board and model,
and pins that ECID for the session. The helper reads it again in Recovery and
stops before erase if it changed. No device ECID is hardcoded.
Only a successful restore with the matching ticket writes
`tickets/restore-complete.json`.

This automates the prior ticket-only + manual restore-bootset sequence. The
earlier direct-from-recovery `idevicerestore` path uploaded iBEC but timed out
waiting for its USB disconnect, including with template-replacement packaging.
The automated restore-OS handoff has not yet been validated end-to-end on
hardware; offline tests do not establish erase-restore success.

### Compare old and new iBEC packaging without restoring

```sh
python3 prepare_erase_cfw.py --compare-ibec \
  --ticket tickets/apticket.im4m \
  --output-dir /tmp/xra1n-ibec-comparison
```

This produces two ticketed images with the **same patched iBEC payload and
ticket**: `template-replace.img4` uses the retired bootset's `img4 -R`
method; `plain-raw-wrap.img4` uses the current CFW's `img4 -A -T ibec`
method. It verifies both extracted payloads and embedded tickets and records
hashes in `comparison.json`. It does not touch CFW or USB. The retired
`restore-bootset/iBEC.img4` template was deleted; without `--old-template`
the command uses the pristine IPSW iBEC as a **stand-in**, not an assertion
that the old bootset image was byte-identical. Supply a saved old image with
`--old-template /path/to/iBEC.img4` for an exact comparison. Use a new empty
output directory for each run; the command refuses to overwrite prior data.

The template-replacement format is available as an **opt-in CFW preparation**:

```sh
python3 run_restore.py --prepare-only --ibec-container template
python3 prepare_erase_cfw.py --check --ibec-container template
```

This selects the pristine-IM4P `img4 -R` path while leaving the payload patch,
other CFW components, and restore transport unchanged. The preparation record
pins the selected format, so `--reuse-prepared` must use the same
`--ibec-container` value. The default remains `plain`; these commands alone
perform no device I/O and do not start an erase restore.

For a later erase restore using the **same already-prepared CFW**, skip all
patching and require the recorded component hashes and pristine inputs to pass
the read-only verifier first:

```sh
python3 -u run_restore.py --reuse-prepared
```

This stops before device I/O if the preparation record, any CFW component,
top-level artifact, pristine backup, or patched iBSS does not verify. Use the
default command only when preparing stock CFW for the first time.
If the local CFW was prepared with the experimental template iBEC option,
include `--ibec-container template` when using `--reuse-prepared`; that CFW
iBEC is verified but not uploaded by the restore-OS bootset path.

## Later SSHRD and normal boots

Their bootsets are generated on demand using the completed restore ticket.
`prepare_normal_bootset.py` and `prepare_sshrd_bootset.py` take iBEC and
DeviceTree templates from `work/pristine-cfw/`, not from the restore-patched
CFW or a stale restore bootset. No second extracted IPSW is required.
