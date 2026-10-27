"""RTTY: a skimmer that decodes every RTTY signal in the passband, and the AFSK
waveform for transmitting.

Standard amateur RTTY: 45.45 baud, 170 Hz shift, 5-bit Baudot (ITA2, US
figures), one start bit, 1.5 stop bits. On the air the mark is the higher
frequency. In audio that depends on the sideband: in LSB / DATA-L the mark is
the lower tone, in USB / DATA-U the higher one (``upper_mark``).

Receive pipeline (fed by AudioCapture with 20 ms chunks of 48 kHz mono):

* low-pass + decimate to 8 kHz;
* every 0.5 s: a power spectrum of the last 2 s; an RTTY signal is a pair of
  peaks 170 Hz apart with a dip between them — each pair becomes a channel
  (at most MAX_CH, matched by centre ±MATCH_HZ, nudged towards the measured
  centre so slow drift is followed);
* per channel: mark and space are mixed to 0 Hz and averaged over most of a bit
  (a matched filter), their envelopes are normalised by their own slowly
  decaying peaks (automatic threshold, so selective fading of one tone does not
  flip the bits), sampled 8 times per bit, and a UART finds start bit, five data
  bits and the stop bit. Framing errors are counted; a channel whose frames
  mostly fail is noise and is not shown.
"""
from __future__ import annotations

import threading
import time
from collections import deque

import numpy as np

FS_IN = 48_000
DEC = 6
FS = FS_IN // DEC                  # 8 kHz
BAUD = 45.45
SHIFT = 170.0
SPB = 8                            # decision samples per bit
SUB = 22                           # 8 kHz / 22 = 363.6 Hz = 8 × 45.45
BIT_N = SPB * SUB                  # 176 samples of 8 kHz per bit
AVG_N = 132                        # matched filter: 3/4 of a bit
F_LO, F_HI = 300.0, 2900.0
NFFT, HOP = 512, 256               # 15.6 Hz bins, 32 ms hop
HIST = 62                          # ≈ 2 s of spectra
SCAN_EVERY = 16                    # spectra (≈ 0.5 s)
PEAK_DB = 8.0
MAX_CH = 8
CLEAR_RATIO = 2.0                  # one tone ≥ 6 dB above the other at every sample point
MATCH_HZ = 25.0
DROP_S = 60.0
TEXT_MAX = 3000

# Baudot / ITA2 with US figures, indexed by the 5-bit code (first bit = LSB)
LTRS = "\0E\nA SIU\rDRJNFCKTZLWHYPQOBG\x0eMXV\x0f"
FIGS = "\x003\n- \x0787\r$4',!:(5\")2#6019?&\x0e./;\x0f"
SO_FIGS, SI_LTRS = 27, 31
_ENC: dict[str, tuple[int, bool]] = {}
for _i, _c in enumerate(LTRS):
    if _c not in "\0\x0e\x0f":
        _ENC.setdefault(_c, (_i, False))
for _i, _c in enumerate(FIGS):
    if _c not in "\0\x0e\x0f\x07" and _c not in _ENC:
        _ENC[_c] = (_i, True)

_N = 63
_t = np.arange(_N) - (_N - 1) / 2
_LP = np.sinc(2 * 3200 / FS_IN * _t) * np.hamming(_N)
_LP /= _LP.sum()
_WIN = np.hanning(NFFT)


class Channel:
    _ids = 0

    def __init__(self, hz: float):
        Channel._ids += 1
        self.id = Channel._ids
        self.hz = hz                               # centre between mark and space
        self.text = ""
        self.figs = False
        self._tail_m = np.zeros(AVG_N - 1, complex)
        self._tail_s = np.zeros(AVG_N - 1, complex)
        self._sub = 0                              # phase of the 1-in-22 decision sampler
        self.mpk = self.spk = 1e-6                 # envelope peaks (automatic threshold)
        self.prev = True                           # last decision (mark)
        self.cnt = -1                              # -1: waiting for a start bit
        self.bits = 0
        self.frames: deque[bool] = deque(maxlen=40)   # True: valid frame
        self.last_ok = time.monotonic()
        self.snr_db = 0.0

    def demod(self, y: np.ndarray, t0: int, upper_mark: bool) -> str:
        n = len(y)
        t = (t0 + np.arange(n)) / FS
        fm = self.hz + (SHIFT / 2 if upper_mark else -SHIFT / 2)
        fs = self.hz - (SHIFT / 2 if upper_mark else -SHIFT / 2)
        zm = np.concatenate([self._tail_m, y * np.exp(-2j * np.pi * fm * t)])
        zs = np.concatenate([self._tail_s, y * np.exp(-2j * np.pi * fs * t)])
        self._tail_m, self._tail_s = zm[-(AVG_N - 1):], zs[-(AVG_N - 1):]
        cm = np.cumsum(np.concatenate([[0], zm]))
        cs = np.cumsum(np.concatenate([[0], zs]))
        em = np.abs(cm[AVG_N:] - cm[:-AVG_N])
        es = np.abs(cs[AVG_N:] - cs[:-AVG_N])
        idx = np.arange((-self._sub) % SUB, n, SUB)
        self._sub = (self._sub + n) % SUB
        out = []
        for m, s in zip(em[idx], es[idx]):
            self.mpk = max(m, self.mpk * 0.996)
            self.spk = max(s, self.spk * 0.996)
            mark = m / self.mpk > s / self.spk
            # squelch: with RTTY one tone is always well above the other; in
            # noise both are alike. A frame only counts where this holds.
            clear = max(m, s) >= CLEAR_RATIO * min(m, s)
            ch = self._uart(mark, clear)
            if ch:
                out.append(ch)
            self.prev = mark
        return "".join(out)

    def _uart(self, mark: bool, clear: bool = True) -> str:
        if self.cnt < 0:
            if self.prev and not mark:             # mark → space: a start bit begins
                self.cnt, self.bits = 0, 0
            return ""
        self.cnt += 1
        c = self.cnt
        if c in (SPB // 2, 12, 20, 28, 36, 44, 52) and not clear:
            self.cnt = -1                          # a sample point in noise: no character
            return ""
        if c == SPB // 2 and mark:                 # middle of the start bit must be space
            self.cnt = -1
            return ""
        if c in (12, 20, 28, 36, 44):              # middles of the five data bits
            if mark:
                self.bits |= 1 << ((c - 12) // SPB)
            return ""
        if c == 52:                                # middle of the stop bit
            self.cnt = -1
            self.frames.append(mark)
            if not mark:
                return ""                          # framing error: search the next edge
            self.last_ok = time.monotonic()
            return self._char(self.bits)
        return ""

    def _char(self, code: int) -> str:
        if code == SO_FIGS:
            self.figs = True
            return ""
        if code == SI_LTRS:
            self.figs = False
            return ""
        ch = (FIGS if self.figs else LTRS)[code]
        if ch == " ":
            self.figs = False                      # unshift on space (as most programs send)
        if ch in "\0\r\x07":
            return ""
        self.text = (self.text + ch)[-TEXT_MAX:]
        return ch

    @property
    def quality(self) -> float:
        return sum(self.frames) / len(self.frames) if self.frames else 0.0

    @property
    def plausible(self) -> bool:
        return len(self.frames) >= 12 and self.quality >= 0.7

    def view(self) -> dict:
        return {"id": self.id, "hz": round(self.hz, 1), "snr": round(self.snr_db),
                "q": round(self.quality * 100), "text": self.text[-1500:]}


class RTTYSkimmer:
    def __init__(self) -> None:
        self.enabled = False
        self.upper_mark = False                    # set from the radio's sideband (+ REV)
        self.rev = False
        self._lock = threading.Lock()
        self._hist_in = np.zeros(_N - 1)
        self._phase = 0
        self._t = 0                                # 8 kHz sample counter (oscillator phase)
        self._fbuf = np.zeros(0)
        self._spec: deque[np.ndarray] = deque(maxlen=HIST)
        self._nspec = 0
        self.channels: list[Channel] = []
        self.version = 0

    def feed(self, mono: np.ndarray, _t: float = 0.0) -> None:
        if not self.enabled:
            return
        x = np.concatenate([self._hist_in, mono.astype(np.float64) / 32768.0])
        y = np.convolve(x, _LP, mode="valid")
        self._hist_in = x[-(_N - 1):]
        y = y[self._phase::DEC]
        self._phase = (self._phase - len(mono)) % DEC
        with self._lock:
            upper = self.upper_mark != self.rev
            changed = False
            for c in self.channels:
                if c.demod(y, self._t, upper):
                    changed = True
            self._t += len(y)
            self._fbuf = np.concatenate([self._fbuf, y])
            while len(self._fbuf) >= NFFT:
                self._spec.append(np.abs(np.fft.rfft(self._fbuf[:NFFT] * _WIN)) ** 2)
                self._fbuf = self._fbuf[HOP:]
                self._nspec += 1
                if self._nspec % SCAN_EVERY == 0:
                    self._scan()
                    changed = True
            if changed:
                self.version += 1

    def _scan(self) -> None:
        p = np.mean(self._spec, axis=0)
        bin_hz = FS / NFFT
        lo, hi = int(F_LO / bin_hz), int(F_HI / bin_hz)
        floor = float(np.median(p[lo:hi])) + 1e-15
        thr = floor * 10 ** (PEAK_DB / 10)
        peaks = [i for i in range(lo + 1, hi - 1) if p[i] >= thr and p[i] >= p[i - 1] and p[i] >= p[i + 1]]
        sh = SHIFT / bin_hz
        found: list[tuple[float, float]] = []
        for i in peaks:
            j = min(peaks, key=lambda k: abs(k - (i + sh)), default=None)
            if j is None or abs(j - (i + sh)) > 1.2:
                continue
            mid = int(round((i + j) / 2))
            if p[mid] > 0.6 * min(p[i], p[j]):     # RTTY has a dip between mark and space
                continue
            # centre: power-weighted between the two peaks (±1 bin each)
            def cen(k):
                w = p[k - 1:k + 2]
                return (k + (w @ np.array([-1.0, 0.0, 1.0])) / w.sum()) * bin_hz
            centre = (cen(i) + cen(j)) / 2
            snr = 10 * np.log10(min(p[i], p[j]) / floor)
            found.append((centre, snr))
        for hz, snr in found:
            near = [c for c in self.channels if abs(c.hz - hz) <= MATCH_HZ]
            if near:
                c = near[0]
                c.hz = 0.8 * c.hz + 0.2 * hz       # follow slow drift
                c.snr_db = snr
            elif len(self.channels) < MAX_CH:
                c = Channel(hz)
                c.snr_db = snr
                self.channels.append(c)
        now = time.monotonic()
        self.channels = [c for c in self.channels if now - c.last_ok < DROP_S
                         or (now - c.last_ok < 8 and not c.frames)]
        self.channels.sort(key=lambda c: c.hz)

    def set_enabled(self, on: bool) -> None:
        with self._lock:
            self.enabled = on
            if not on:
                self.channels, self._spec = [], deque(maxlen=HIST)
                self._fbuf = np.zeros(0)
            self.version += 1

    def clear(self) -> None:
        with self._lock:
            for c in self.channels:
                c.text = ""
            self.version += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {"enabled": self.enabled, "rev": self.rev, "upper_mark": self.upper_mark != self.rev,
                    "channels": [c.view() for c in self.channels if c.plausible]}


# -- transmit -----------------------------------------------------------------
def encode(text: str) -> list[int]:
    """Text → Baudot codes with LTRS/FIGS shifts (unknown characters dropped).
    A space switches back to letters on the receiving side (unshift on space),
    so after a space in figures the FIGS shift is sent again."""
    codes: list[int] = [SI_LTRS, SI_LTRS]
    figs = False
    for ch in text.upper().replace("\r\n", "\n"):
        if ch == "\n":
            codes += [8, 2]                        # CR LF
            continue
        e = _ENC.get(ch)
        if not e:
            continue
        code, need_figs = e
        if ch == " ":
            codes.append(4)
            figs = False                           # the receiver unshifts on space
            continue
        if need_figs != figs:
            codes.append(SO_FIGS if need_figs else SI_LTRS)
            figs = need_figs
        codes.append(code)
    return codes


def waveform(text: str, centre: float, upper_mark: bool, rate: int = FS_IN,
             lead_s: float = 0.4, tail_s: float = 0.25) -> np.ndarray:
    """Continuous-phase AFSK for ``text`` (with CR LF before and after), mark
    idle before and after, ±1.0 amplitude."""
    codes = encode("\n" + text.strip() + "\n")
    bits: list[tuple[bool, float]] = [(True, lead_s * BAUD)]
    for c in codes:
        bits.append((False, 1.0))                                  # start
        bits += [(bool(c >> k & 1), 1.0) for k in range(5)]        # data, LSB first
        bits.append((True, 1.5))                                   # stop
    bits.append((True, tail_s * BAUD))
    # sample-exact bit boundaries from the running time
    marks = np.zeros(int(sum(b[1] for b in bits) / BAUD * rate) + 1, dtype=bool)
    pos = 0.0
    for mark, nb in bits:
        a, b = int(round(pos / BAUD * rate)), int(round((pos + nb) / BAUD * rate))
        marks[a:b] = mark
        pos += nb
    fm = centre + (SHIFT / 2 if upper_mark else -SHIFT / 2)
    fsp = centre - (SHIFT / 2 if upper_mark else -SHIFT / 2)
    f = np.where(marks, fm, fsp).astype(np.float64)
    k = max(1, int(rate * 0.002))                                  # 2 ms smoothing: no clicks
    f = np.convolve(f, np.ones(k) / k, mode="same")
    phase = 2 * np.pi * np.cumsum(f) / rate
    x = np.sin(phase)
    ramp = int(rate * 0.005)
    x[:ramp] *= np.linspace(0, 1, ramp)
    x[-ramp:] *= np.linspace(1, 0, ramp)
    return x


def duration_s(text: str) -> float:
    return (len(encode("\n" + text.strip() + "\n")) * 7.5 + (0.4 + 0.25) * BAUD) / BAUD
