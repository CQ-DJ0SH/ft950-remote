#!/bin/sh
# Build the faster FT8 decoders for the Raspberry Pi 5 and install them:
#
#   jt9-fast  WSJT-X 2.7.0 (Debian source 2.7.0+repack-1) with the OSD stage
#             (osd174_91) in Rust - bit-identical decodes, about twice as fast
#   jt9-33    WSJT-X 3.3.0-beta4 (release/3.3, pinned commit) with its
#             multithreaded MTft8 decoder and the MTft8 OSD (osd174_91var) in Rust
#
# Usage:  sudo ./build.sh [2.7|3.3|all]        (default: all)
#
# Environment:
#   WORK    where sources and builds go       (default /opt/jt9-fast)
#   PREFIX  where the binaries are installed  (default /usr/local/bin)
#   CPU     -mcpu / target-cpu                (default cortex-a76 = Pi 5)
#   JOBS    parallel make jobs                (default 3)
#
# Needs: gfortran, cmake, rustc + cargo, curl, git, and WSJT-X's build
# dependencies - see README.md. WSJT-X is GPL-3.0; so are the Rust ports here.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
WORK=${WORK:-/opt/jt9-fast}
PREFIX=${PREFIX:-/usr/local/bin}
CPU=${CPU:-cortex-a76}
JOBS=${JOBS:-3}
WHAT=${1:-all}
FLAGS="-O3 -mcpu=$CPU -funroll-loops"

DEB=http://deb.debian.org/debian/pool/main/w/wsjtx
ORIG=wsjtx_2.7.0+repack.orig.tar.xz
ORIG_SHA=c4687b322ed36d3c526ed32e99ea48023eb76542c004a0d4658e58d8ddbf3538
DEBT=wsjtx_2.7.0+repack-1.debian.tar.xz
DEBT_SHA=1e6ef3b540502e3aaa56601337f0c5d76b9584fdd0fb344deca4e005a8e50779
V33_REPO=https://github.com/WSJTX/wsjtx.git
V33_COMMIT=0f7d5928449ec747c3d1f6da1306f8c94404c3a9   # "Cut 3.3.0-beta4 from develop", 2026-10-21

say() { printf '\n== %s\n' "$*"; }

crate() {   # crate <dir>: build a Rust OSD as a static library
    say "Rust: $1"
    (cd "$HERE/$1" && RUSTFLAGS="-C target-cpu=$CPU" CARGO_TARGET_DIR="$WORK/target-$1" \
        cargo build --release --locked)
}

cmake_jt9() {   # cmake_jt9 <source> <build> <rust lib>
    say "jt9: $1 -> $2"
    cmake -S "$1" -B "$2" -DCMAKE_BUILD_TYPE=None \
        -DCMAKE_Fortran_FLAGS="$FLAGS" -DCMAKE_C_FLAGS="$FLAGS" -DCMAKE_CXX_FLAGS="$FLAGS" \
        -DWSJT_GENERATE_DOCS=OFF -DWSJT_SKIP_MANPAGES=ON -DOSD_RS_LIB="$3" >"$2.cmake.log" 2>&1 \
        || { tail -20 "$2.cmake.log"; exit 1; }
    make -C "$2" -j"$JOBS" jt9 >"$2.make.log" 2>&1 || { tail -20 "$2.make.log"; exit 1; }
}

build_27() {
    src=$WORK/src/wsjtx-2.7-rs
    mkdir -p "$WORK/src"
    cd "$WORK/src"
    [ -f "$ORIG" ] || curl -fsSLO "$DEB/$ORIG"
    [ -f "$DEBT" ] || curl -fsSLO "$DEB/$DEBT"
    echo "$ORIG_SHA  $ORIG" | sha256sum -c -
    echo "$DEBT_SHA  $DEBT" | sha256sum -c -
    rm -rf "$src" && mkdir -p "$src"
    tar -xJf "$ORIG" -C "$src" --strip-components=1
    tar -xJf "$DEBT" -C "$src"
    say "Debian patches"
    for p in $(grep -v '^#' "$src/debian/patches/series"); do
        patch -d "$src" -p1 -s <"$src/debian/patches/$p"
    done
    patch -d "$src" -p1 <"$HERE/patches/wsjtx-2.7-rust-osd.patch"
    crate osd-rs
    cmake_jt9 "$src" "$WORK/build-2.7-rs" "$WORK/target-osd-rs/release/libosd174.a"
    install -m 755 "$WORK/build-2.7-rs/jt9" "$PREFIX/jt9-fast"
    say "installed $PREFIX/jt9-fast"
}

build_33() {
    src=$WORK/src/wsjtx-3.3-rs
    mkdir -p "$WORK/src"
    rm -rf "$src" && mkdir -p "$src"
    git -C "$src" init -q
    git -C "$src" fetch -q --depth 1 "$V33_REPO" "$V33_COMMIT"
    git -C "$src" checkout -q FETCH_HEAD
    patch -d "$src" -p1 <"$HERE/patches/wsjtx-3.3-rust-osd.patch"
    crate osd33-rs
    cmake_jt9 "$src" "$WORK/build-3.3-rs" "$WORK/target-osd33-rs/release/libosd174var.a"
    install -m 755 "$WORK/build-3.3-rs/jt9" "$PREFIX/jt9-33"
    say "installed $PREFIX/jt9-33"
}

case "$WHAT" in
    2.7) build_27 ;;
    3.3) build_33 ;;
    all) build_27; build_33 ;;
    *) echo "usage: $0 [2.7|3.3|all]" >&2; exit 2 ;;
esac
