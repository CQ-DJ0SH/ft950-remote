# Faster FT8 decoders for the Raspberry Pi 5

Two builds of WSJT-X's `jt9` with the ordered-statistics decoder (OSD) — the
stage that takes about half of `jt9`'s time — rewritten in Rust. FT-950 Remote
offers them in **Settings ▸ FT8 ▸ Decoder**; measurements are in the main
[README](../README.md#ft8-decoder-performance-pi-5).

| Binary | Source | Rust part | Result |
|---|---|---|---|
| `jt9-fast` | WSJT-X 2.7.0 (Debian source 2.7.0+repack-1) | `osd-rs/` replaces `osd174_91` | bit-identical decodes, about 2× faster |
| `jt9-33` | WSJT-X 3.3.0-beta4 (`release/3.3`, commit `0f7d592`) | `osd33-rs/` replaces `osd174_91var` (MTft8) | same decodes as 3.3 MTft8, about 1.6× faster |

Both are compiled with `-O3 -mcpu=cortex-a76 -funroll-loops` (Debian uses
`-O2`; that alone gives about 30 %).

## How the Rust OSD is bit-identical

- Codewords are bit-packed (`[u64; 3]` for 174 bits), so encoding a test
  pattern is a few XORs instead of loops over byte arrays.
- Every floating-point sum runs over the same terms in the same ascending
  order as the Fortran `sum(nxor*absrx)`; the terms it skips are exact zeros.
- Sorting (`indexx`), CRC (`get_crc14`, `chkcrc14a`) and the generator
  (`encode174_91_nocrc`) are WSJT-X's own Fortran routines, called via FFI —
  ties sort the same way.
- Quirks are kept: values that live across test patterns in the Fortran (an
  AP-masked pattern start reuses the previous `e2sub`/`d1`), and the order of
  the boxit/fetchit hash boxes. The MTft8 version stamps the boxes with an
  epoch instead of wiping 2 MB per call, and is thread-safe for OpenMP.
- The Fortran original stays in the binary as `osd174_91_f` /
  `osd174_91var_f`. Cases FT8/FT4 never use go there.

**Check mode:** with `OSD_CHECK=1` in the environment every OSD call runs the
Fortran original as well and compares codeword, hard errors and distance bit
for bit; the totals go to stderr at exit:

    OSD_CHECK=1 jt9-fast -8 -p 15 -d 2 -L 200 -H 3000 -a /tmp -t /tmp 261026_085200.wav
    OSD_CHECK: 456 calls, 0 mismatches

## Build

On Raspberry Pi OS (Debian 13), as root:

    apt install gfortran cmake rustc cargo curl git pkg-config \
        libboost-dev libboost-log-dev libfftw3-dev libhamlib-dev \
        libqcustomplot-dev libqt5opengl5-dev libqt5serialport5-dev libqwt-qt5-dev \
        libsamplerate0-dev libudev-dev portaudio19-dev qtbase5-dev qttools5-dev \
        qtmultimedia5-dev libqt5websockets5-dev libssl-dev
    ./build.sh            # both; or ./build.sh 2.7 / ./build.sh 3.3

`build.sh` downloads the WSJT-X sources (2.7: Debian tarballs, checked by
SHA-256, with Debian's patches; 3.3: the pinned commit from GitHub), applies
`patches/` (rename the Fortran OSD, link the Rust library), builds the Rust
crate and `jt9`, and installs `jt9-fast` / `jt9-33` to `/usr/local/bin`.
`WORK` (default `/opt/jt9-fast`), `PREFIX`, `CPU` and `JOBS` can be set in
the environment. On a Pi 5 it takes a few minutes.

For another CPU set `CPU` (e.g. `cortex-a72` on a Pi 4) — the speed-up then
needs measuring again.

## Tools

- `tools/bench.py <jt9> <label> <depth> [repeats] [-- extra jt9 args]` —
  decodes every `wav/*.wav` under `BENCH_DIR`, prints CPU and wall time and
  writes the decode lines to `out/` for an exact comparison between builds.
  Test periods: copy them from `/run/ft950/FT8/` (the service keeps the last 20).
- `tools/msgcmp.py a.txt b.txt` — compares two outputs by message (SNR/DT
  estimates differ between decoder versions).
- `tools/shadow.py` — shadow run: decodes every live period with the Debian
  `jt9` and the build under test, compares, and logs to `$SHADOW_OUT/log.csv`
  (`python3 tools/shadow.py report` prints the totals). Run it at low
  priority, e.g. `systemd-run --unit=jt9-shadow --property=Nice=19
  --property=CPUSchedulingPolicy=idle python3 tools/shadow.py`.

## Licence

WSJT-X is © Joe Taylor K1JT and the WSJT Development Team, GPL-3.0. The Rust
crates here are ports of its `osd174_91.f90` and `osd174_91var.f90` and are
GPL-3.0 as well, like the rest of this repository.
