#!/bin/sh
# Build jttyd against a WSJT-X 3.3 build tree (libwsjt_fort.a holds JTTY's
# receiver and transmit encoder; the link line is that of WSJT-X's rjtty).
#   WSJTX_BUILD=/opt/jt9-fast/build-33 ./build.sh && sudo install -m 755 jttyd /usr/local/bin/
set -e
B=${WSJTX_BUILD:-/opt/jt9-fast/build-33}
cd "$(dirname "$0")"
c++ -O2 -mcpu=cortex-a76 -Wall -Wextra -o jttyd jttyd.cpp \
  "$B/libwsjt_fort.a" "$B/libwsjt_cxx.a" \
  /usr/lib/aarch64-linux-gnu/libfftw3f_threads.so /usr/lib/aarch64-linux-gnu/libfftw3f.so \
  -lgfortran -lm -fopenmp -pthread
echo "built $(pwd)/jttyd"
