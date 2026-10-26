# Linux/aarch64 host tools

XRa1n was developed and hardware-tested on Kali NetHunter Linux/aarch64.
The bundled host tools were collected from the RBIS environment used for
those tests; they are not portable binaries for macOS or x86-64 Linux.

The checked release bundle lives in `tools/host/linux-aarch64/`. The restore,
SSHRD, normal-boot and SSHRD installer scripts select it relative to this
checkout, so `/picotool` is not required. Run the offline integrity check:

```sh
python3 host_tools.py --check
(cd tools/host/linux-aarch64 && sha256sum -c SHA256SUMS)
```

`XRA1N_TOOLS_DIR=/absolute/path/to/linux-aarch64` relocates the *same pinned*
bundle. The override is hash-checked; it is not a way to bypass reviewed tool
versions. If neither the bundled directory nor an override exists, normal
system `PATH` resolution remains available for development. The Apple TSS CA
bundle is selected from the same directory; the old `.run/` path is a
development-only fallback.

The bundle contains `usbliter8ctl`, `img4`, the tested custom
`idevicerestore`, `irecovery`, `iproxy`, `usbmuxd`, `sshpass`, `idevice_id`, and
`idevicepair`; `lib/libirecovery-1.0.so.5` is the SONAME link required by the
custom `idevicerestore`. The CA PEM is data, not an executable. PyUSB is
required for `usbliter8ctl`. This is an **aarch64 Linux** build; the remaining
shared-library dependencies come from the host distribution. The four
`tools/lib/` files are legacy build outputs; only the pinned libirecovery
library is required by the current restore executable.

On a different host, provide compatible builds of the host executables and
their shared libraries, including `idevicerestore` and `libirecovery`; rebuilding
only `idevicerestore` is not enough when the host architecture or ABI differs.
The pinned `idevicerestore` source revision is
[`60192e97`](https://github.com/libimobiledevice/idevicerestore/commit/60192e97f87d1bbab5c493684e0a245b0966363f)
from libimobiledevice, not a Liter8-specific source fork. Liter8 pins that
same revision as its `vendor/idevicerestore` Git submodule and builds it in
[`setup.sh`](https://github.com/Xplo8E/Liter8/blob/main/setup.sh) against
host-installed libraries. Liter8's macOS build is not a drop-in replacement
for this Linux/aarch64 bundle. The current tool
resolver checks pinned hashes, so alternative builds also require updating
the tool manifest and validating the restore flow on that host.

The scripts still expect ordinary host programs such as Python 3, `lsusb`,
`ssh`, `ssh-keygen`, `zstd`, and `tar`. `tools/ssh-normal.sh` uses the same
bundle resolution and validates it before starting usbmuxd.
