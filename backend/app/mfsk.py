"""MFSK32: a skimmer for every MFSK32 signal in the passband, and the
waveform for transmitting - bit-exact with fldigi (the reference program for
MFSK; the coding below is ported from fldigi's src/mfsk, GPL-3, by Dave
Freese W1HKJ and the fldigi team, after IZ8BLY's and ZL1BPU's MFSK16).

MFSK32: 16 tones 31.25 Hz apart (469 Hz wide), 31.25 baud, 4 bits per
symbol (Gray-coded tone number), a K=7 rate-1/2 convolutional code (NASA
polynomials 0x6d / 0x4f), IZ8BLY's diagonal interleaver (4 x 4, depth 10)
and the IZ8BLY MFSK varicode. About 120 WPM; readable to about -11 dB.

Receive pipeline (fed with 20 ms chunks of 48 kHz mono, like rtty.py):

* low-pass + decimate to 8 kHz;
* every 0.5 s a power spectrum of the last 4 s (1 Hz bins): an MFSK32
  signal is a comb of 16 lines 31.25 Hz apart, with nothing just outside
  its two edges (that rules out the comb shifted by one tone); each comb is
  a channel (at most MAX_CH, matched by centre, nudged to follow drift);
* per channel: mixed to 0 Hz, 1 kHz complex samples, a sliding 32-sample
  DFT on the 16 tones; symbol timing from where the tones are cleanest
  (the window then covers exactly one symbol); soft bits as in fldigi,
  deinterleaved, Viterbi decoded, varicode to text. A channel whose
  decoder metric stays low is noise and is not shown.
"""
from __future__ import annotations

import threading
import time
from collections import deque

import numpy as np

FS_IN = 48_000
DEC = 6
FS = FS_IN // DEC                  # 8 kHz, as fldigi
SYMLEN = 256                       # samples of 8 kHz per symbol: 31.25 baud
NT = 16                            # tones
SYMBITS = 4
SPACING = FS / SYMLEN              # 31.25 Hz
BANDWIDTH = (NT - 1) * SPACING     # 468.75 Hz between the outer tones
DEPTH = 10                         # interleaver depth (MFSK32)
PREAMBLE = 107
K, POLY1, POLY2 = 7, 0x6D, 0x4F
TRACEBACK = 45                     # fldigi's tracepair.trace for MFSK

# receive
BB_DEC = 8                         # 8 kHz -> 1 kHz complex baseband
BB_FS = FS // BB_DEC               # 1000 Hz
SPS = SYMLEN // BB_DEC             # 32 baseband samples per symbol
NFFT = 8192                        # ~1 Hz bins for the comb search
HOP = 4000                         # a spectrum every 0.5 s
HIST = 8                           # spectra averaged (4 s)
F_LO, F_HI = 300.0, 2700.0         # centres searched
MAX_CH = 6
MATCH_HZ = 20.0
DROP_S = 45.0
TEXT_MAX = 3000
COMB_DB = 1.5                      # comb tones' mean above the floor (dB, after +-7 Hz smoothing)
EDGE_RATIO = 3.0                   # comb tones' excess vs. that of the two positions just outside
SNR_CAL = 0.53                     # share of a tone's power inside the +-7 Hz window (calibrated)
UNIFORM = 4.0                      # strongest tone at most this x the median one (not RTTY / a carrier)
GOOD_METRIC = 35.0                 # fldigi-scaled decoder metric (0-100): a real signal
SQUELCH = 25.0                     # characters shown only above this (noise sits at 8-16)
PENDING_S = 10.0                   # text from before a channel was recognised, kept that long
BACKLOG_S = 8.0                    # audio a new channel decodes first

# -- coding tables (fldigi) -----------------------------------------------------
VARICODE = [   # IZ8BLY MFSK varicode, from fldigi src/mfsk/mfskvaricode.cxx
    "11101011100", "11101100000", "11101101000", "11101101100", "11101110000", "11101110100",
    "11101111000", "11101111100", "10101000", "11110000000", "11110100000", "11110101000",
    "11110101100", "10101100", "11110110000", "11110110100", "11110111000", "11110111100",
    "11111000000", "11111010000", "11111010100", "11111011000", "11111011100", "11111100000",
    "11111101000", "11111101100", "11111110000", "11111110100", "11111111000", "11111111100",
    "100000000000", "101000000000", "100", "111000000", "111111100", "1011011000",
    "1010101000", "1010100000", "1000000000", "110111100", "111110100", "111110000",
    "1010110100", "111100000", "10100000", "111011000", "111010100", "111101000",
    "11100000", "11110000", "101000000", "101010100", "101110100", "101100000",
    "101101100", "110100000", "110000000", "110101100", "111101100", "111111000",
    "1011000000", "111011100", "1010111100", "111010000", "1010000000", "10111100",
    "100000000", "11010100", "11011100", "10111000", "11111000", "101010000",
    "101011000", "11000000", "110110100", "101111100", "11110100", "11101000",
    "11111100", "11010000", "11101100", "110110000", "11011000", "10110100",
    "10110000", "101011100", "110101000", "101101000", "101110000", "101111000",
    "110111000", "1011101000", "1011010000", "1011101100", "1011010100", "1010110000",
    "1010101100", "10100", "1100000", "111000", "110100", "1000",
    "1010000", "1011000", "110000", "11000", "10000000", "1110000",
    "101100", "1000000", "11100", "10000", "1010100", "1111000",
    "100000", "101000", "1100", "111100", "1101100", "1101000",
    "1110100", "1011100", "1111100", "1011011100", "1010111000", "1011100000",
    "1011110000", "101010000000", "101010100000", "101010101000", "101010101100", "101010110000",
    "101010110100", "101010111000", "101010111100", "101011000000", "101011010000", "101011010100",
    "101011011000", "101011011100", "101011100000", "101011101000", "101011101100", "101011110000",
    "101011110100", "101011111000", "101011111100", "101100000000", "101101000000", "101101010000",
    "101101010100", "101101011000", "101101011100", "101101100000", "101101101000", "101101101100",
    "101101110000", "101101110100", "101101111000", "101101111100", "1011110100", "1011111000",
    "1011111100", "1100000000", "1101000000", "1101010000", "1101010100", "1101011000",
    "1101011100", "1101100000", "1101101000", "1101101100", "1101110000", "1101110100",
    "1101111000", "1101111100", "1110000000", "1110100000", "1110101000", "1110101100",
    "1110110000", "1110110100", "1110111000", "1110111100", "1111000000", "1111010000",
    "1111010100", "1111011000", "1111011100", "1111100000", "1111101000", "1111101100",
    "1111110000", "1111110100", "1111111000", "1111111100", "10000000000", "10100000000",
    "10101000000", "10101010000", "10101010100", "10101011000", "10101011100", "10101100000",
    "10101101000", "10101101100", "10101110000", "10101110100", "10101111000", "10101111100",
    "10110000000", "10110100000", "10110101000", "10110101100", "10110110000", "10110110100",
    "10110111000", "10110111100", "10111000000", "10111010000", "10111010100", "10111011000",
    "10111011100", "10111100000", "10111101000", "10111101100", "10111110000", "10111110100",
    "10111111000", "10111111100", "11000000000", "11010000000", "11010100000", "11010101000",
    "11010101100", "11010110000", "11010110100", "11010111000", "11010111100", "11011000000",
    "11011010000", "11011010100", "11011011000", "11011011100", "11011100000", "11011101000",
    "11011101100", "11011110000", "11011110100", "11011111000", "11011111100", "11100000000",
    "11101000000", "11101010000", "11101010100", "11101011000",
]
_VARIDEC = {int(c, 2): i for i, c in enumerate(VARICODE)}


def _parity(w: int) -> int:
    return bin(w).count("1") & 1


_OUT = np.array([_parity(POLY1 & i) | (_parity(POLY2 & i) << 1) for i in range(1 << K)])


def gray_encode(x: int) -> int:
    """fldigi's grayencode() (its names are swapped: this is Gray -> binary)."""
    b = x
    for s in range(1, 8):
        b ^= x >> s
    return b & 0xFF


def gray_decode(x: int) -> int:
    """fldigi's graydecode(): binary -> Gray."""
    return x ^ (x >> 1)


class Interleaver:
    """IZ8BLY's diagonal interleaver (fldigi interleave.cxx)."""

    def __init__(self, size: int, depth: int, forward: bool):
        self.size, self.depth, self.fwd = size, depth, forward
        self.tab = np.full((depth, size, size), 0 if forward else 128, dtype=np.int64)

    def symbols(self, syms: list[int]) -> list[int]:
        s = self.size
        for k in range(self.depth):
            t = self.tab[k]
            t[:, :-1] = t[:, 1:].copy()
            t[:, -1] = syms
            syms = [int(t[i, s - i - 1]) if self.fwd else int(t[i, i]) for i in range(s)]
        return syms

    def bits(self, word: int) -> int:
        s = self.size
        syms = self.symbols([(word >> (s - i - 1)) & 1 for i in range(s)])
        out = 0
        for b in syms:
            out = (out << 1) | b
        return out


class Viterbi:
    """K=7 rate-1/2 soft-decision Viterbi decoder, chunk size 1 (fldigi viterbi.cxx)."""
    PATHMEM = 256

    def __init__(self, traceback: int = TRACEBACK):
        self.ns = 1 << (K - 1)
        self.tb = traceback
        self.metrics = np.zeros((self.PATHMEM, self.ns), dtype=np.int64)
        self.history = np.zeros((self.PATHMEM, self.ns), dtype=np.int64)
        self.ptr = 0
        n = np.arange(self.ns)
        self.p0, self.p1 = n >> 1, (n + self.ns) >> 1
        self.o0, self.o1 = _OUT[n], _OUT[n + self.ns]

    def decode(self, s0: int, s1: int) -> tuple[int, int]:
        """Soft symbols (0..255, 255 = surely 1) of one bit pair -> (bit, metric)."""
        m0, m1 = 128 - s0, s0 - 128            # mettab[bit][sym[0]]
        n0, n1 = 128 - s1, s1 - 128
        met = np.array([m0 + n0, m1 + n0, m0 + n1, m1 + n1])
        cur, prev = self.ptr, (self.ptr - 1) % self.PATHMEM
        a = self.metrics[prev][self.p0] + met[self.o0]
        b = self.metrics[prev][self.p1] + met[self.o1]
        take_a = a > b
        self.metrics[cur] = np.where(take_a, a, b)
        self.history[cur] = np.where(take_a, self.p0, self.p1)
        top = self.metrics[cur].max()
        if abs(top) > 1 << 40:                 # keep the numbers small
            self.metrics -= top
        self.ptr = (self.ptr + 1) % self.PATHMEM
        # traceback from the best state
        p = cur
        state = int(np.argmax(self.metrics[p]))
        later_p, later_state = p, state
        for _ in range(self.tb):
            later_p, later_state = p, state
            state = int(self.history[p][state])
            p = (p - 1) % self.PATHMEM
        # p: tb steps back, state: the best path there; the bit is its low bit,
        # the metric the path's gain over the next step (fldigi: chunk size 1)
        bit = state & 1
        return bit, int(self.metrics[later_p][later_state] - self.metrics[p][state])


class Encoder:
    def __init__(self) -> None:
        self.sh = 0

    def encode(self, bit: int) -> int:
        self.sh = ((self.sh << 1) | (1 if bit else 0)) & ((1 << K) - 1)
        return int(_OUT[self.sh])


# -- transmit -------------------------------------------------------------------
def _symbols(text: str) -> list[int]:
    """The tone numbers (0-15, before the sideband reversal) of a whole
    transmission, as fldigi's tx_process sends it."""
    enc, inlv = Encoder(), Interleaver(SYMBITS, DEPTH, True)
    out: list[int] = []
    st = {"sh": 0, "n": 0}

    def push(bits2: int, send: bool) -> None:
        for i in range(2):
            st["sh"] = (st["sh"] << 1) | ((bits2 >> i) & 1)
            st["n"] += 1
            if st["n"] == SYMBITS:
                word = inlv.bits(st["sh"])
                if send:
                    out.append(word)
                st["sh"], st["n"] = 0, 0

    def sendbit(b: int) -> None:
        push(enc.encode(b), True)

    def sendchar(c: int) -> None:
        for ch in VARICODE[c & 0xFF]:
            sendbit(1 if ch == "1" else 0)

    # preamble: the interleaver filled with the coded zeros, nothing sent (clearbits)
    d = enc.encode(0)
    for _ in range(PREAMBLE):
        push(d, False)
    for _ in range(PREAMBLE // 3):
        sendbit(0)
    for c in (13, 2, 13):                      # CR STX CR
        sendchar(c)
    for ch in text.replace("\r\n", "\n"):
        c = ord(ch)
        if c == 10:                            # a new line goes out as CR LF
            sendchar(13)
            sendchar(10)
            continue
        if c < 256:
            sendchar(c)
    for c in (13, 4, 13):                      # CR EOT CR
        sendchar(c)
    sendbit(1)                                 # flushtx(preamble)
    for _ in range(PREAMBLE):
        sendbit(0)
    return out


def waveform(text: str, centre: float, reverse: bool = False, rate: int = FS_IN) -> np.ndarray:
    """Continuous-phase MFSK32 tones for ``text``, centred on ``centre`` Hz,
    +-1.0 amplitude. reverse: the tone order mirrored (LSB / DATA-L)."""
    syms = _symbols(text)
    n = int(round(rate / 31.25))               # samples per symbol at ``rate``
    f0 = centre - BANDWIDTH / 2
    tones = np.array([gray_encode(s & 15) for s in syms])
    if reverse:
        tones = (NT - 1) - tones
    f = np.repeat(f0 + tones * SPACING, n).astype(np.float64)
    phase = 2 * np.pi * np.cumsum(f) / rate
    x = np.cos(phase)
    ramp = int(rate * 0.005)
    x[:ramp] *= np.linspace(0, 1, ramp)
    x[-ramp:] *= np.linspace(1, 0, ramp)
    return x


def duration_s(text: str) -> float:
    return len(_symbols(text)) / 31.25


# -- receive --------------------------------------------------------------------
_N = 63
_t = np.arange(_N) - (_N - 1) / 2
_LP = np.sinc(2 * 3200 / FS_IN * _t) * np.hamming(_N)
_LP /= _LP.sum()
_NB = 63                                       # 8 kHz -> 1 kHz baseband low-pass (+-320 Hz)
_tb = np.arange(_NB) - (_NB - 1) / 2
_LPB = np.sinc(2 * 320 / FS * _tb) * np.hamming(_NB)
_LPB /= _LPB.sum()
_WIN = np.hanning(NFFT)
_TONE_HZ = (np.arange(NT) - (NT - 1) / 2) * SPACING     # baseband tone offsets
_GRAYDEC = [gray_decode(i) for i in range(NT)]
SYNC_N = 24                                    # symbols in the timing estimate


class Channel:
    _ids = 0

    def __init__(self, hz: float):
        Channel._ids += 1
        self.id = Channel._ids
        self.hz = hz
        self.text = ""
        self.nchars = 0
        self.tx: deque[dict] = deque(maxlen=12)
        self.snr_db = 0.0
        self.last_seen = time.monotonic()
        self.metric = 0.0                      # 0..100, fldigi's scale
        self.best_metric = 0.0
        self.good_since: float | None = None
        self._bt = 0                           # 8 kHz sample counter for the mixer
        self._tail8 = np.zeros(_NB - 1, complex)
        self._dph = 0                          # decimation phase 8 kHz -> 1 kHz
        self._n = 0                            # 1 kHz sample counter (absolute)
        self._ztail = np.zeros(SPS - 1, complex)
        self._mag = np.zeros((0, NT))          # |DFT| per 1 kHz sample, window ending there
        self._mag0 = 0                         # absolute index of _mag[0]
        self._next = None                      # absolute index of the next symbol's window end
        self._peaky: deque[np.ndarray] = deque(maxlen=SYNC_N)
        self._rxinlv = Interleaver(SYMBITS, DEPTH, False)
        self._vit = Viterbi()
        self._pair = [128, 128]
        self._symcounter = 0
        self._met2 = 0.0
        self._shreg = 1
        self._last_cr = False
        self._pending: deque[tuple[float, str]] = deque(maxlen=60)
        self._mute_until = 0.0

    # baseband ----------------------------------------------------------------
    def _baseband(self, y: np.ndarray) -> np.ndarray:
        t = (self._bt + np.arange(len(y))) / FS
        self._bt += len(y)
        z = np.concatenate([self._tail8, y * np.exp(-2j * np.pi * self.hz * t)])
        self._tail8 = z[-(_NB - 1):]
        f = np.convolve(z, _LPB, mode="valid")
        out = f[self._dph::BB_DEC]
        self._dph = (self._dph - len(f)) % BB_DEC
        return out

    def demod(self, y: np.ndarray) -> str:
        z = self._baseband(y)
        if not len(z):
            return ""
        zz = np.concatenate([self._ztail, z])
        idx = self._n - (SPS - 1) + np.arange(len(zz))
        self._ztail = zz[-(SPS - 1):]
        ph = np.exp(-2j * np.pi * np.outer(idx, _TONE_HZ) / BB_FS)
        cs = np.cumsum(np.vstack([np.zeros((1, NT)), zz[:, None] * ph]), axis=0)
        mag = np.abs(cs[SPS:] - cs[:-SPS])     # window ending at sample self._n + i
        if self._mag.shape[0] == 0:
            self._mag0 = self._n
        self._mag = np.vstack([self._mag, mag])
        self._n += len(z)
        out = []
        if self._next is None:
            self._next = self._mag0 + SPS
        while self._next + SPS // 2 < self._mag0 + self._mag.shape[0]:
            i = self._next - self._mag0
            if i < SPS // 2:
                self._next += SPS
                continue
            # timing: how clean the strongest tone is at each offset in this symbol
            seg = self._mag[i - SPS // 2:i + SPS // 2]
            self._peaky.append(seg.max(axis=1) / (seg.sum(axis=1) + 1e-12))
            bins = self._mag[i]
            out.append(self._symbol(bins))
            self._next += SPS
            if len(self._peaky) == SYNC_N:
                score = np.mean(self._peaky, axis=0)      # offsets -16 .. +15
                d = int(np.argmax(score)) - SPS // 2
                if d:
                    # big error: jump (acquisition), small: one sample at a time
                    self._next += d if abs(d) > 4 else int(np.sign(d))
                    self._peaky.clear()
        # keep what the next symbol may still need
        keep = self._next - SPS - self._mag0
        if keep > 0:
            self._mag = self._mag[keep:]
            self._mag0 += keep
        return "".join(out)

    # symbol -> soft bits -> deinterleave -> Viterbi -> varicode -----------------
    def _symbol(self, bins: np.ndarray) -> str:
        if self.reverse:
            bins = bins[::-1]
        s = float(bins.sum()) or 1e-10
        hard = int(np.argmax(bins))
        b = np.zeros(SYMBITS)
        for i in range(NT):
            j = _GRAYDEC[i]
            m = 2 * bins[i] if i == hard else bins[i]
            for k in range(SYMBITS):
                b[k] += m if j & (1 << (SYMBITS - k - 1)) else -m
        soft = [int(min(255, max(0, 128 + v / s * 256))) for v in b]
        soft = self._rxinlv.symbols(soft)
        out = []
        for v in soft:
            self._pair = [self._pair[1], v]
            self._symcounter ^= 1
            if self._symcounter:
                continue
            bit, met = self._vit.decode(self._pair[0], self._pair[1])
            self._met2 += (met - self._met2) / 50
            # fldigi's display metric: (met - 60) / 2, 0..100
            self.metric = max(0.0, min(100.0, (self._met2 - 60) * 0.5))
            self.best_metric = max(self.best_metric, self.metric)
            now = time.monotonic()
            if self.metric >= GOOD_METRIC:
                self.last_seen = now
                if self.good_since is None:
                    self.good_since = now
                    # the start of the transmission, decoded while the metric rose
                    out.extend(c for t, c in self._pending if now - t <= PENDING_S)
                    self._pending.clear()
            ch = self._bit(bit)
            if not ch:
                continue
            if now < self._mute_until:
                continue                       # just after EOT: the flush, no text
            if self.good_since is None:
                if self.metric >= 20:
                    self._pending.append((now, ch))
            elif self.metric >= SQUELCH:
                out.append(ch)
        return "".join(out)

    reverse = False

    def _bit(self, bit: int) -> str:
        self._shreg = (self._shreg << 1) | bit
        if (self._shreg & 7) == 1:
            c = _VARIDEC.get(self._shreg >> 1, -1)
            self._shreg = 1
            return self._char(c)
        if self._shreg > 1 << 16:              # no character boundary: noise
            self._shreg = 1
        return ""

    def _char(self, c: int) -> str:
        if c < 0:
            return ""
        if c == 4:                             # EOT: the end of a transmission
            self._mute_until = time.monotonic() + 1.5
            return ""
        if c == 13:
            self._last_cr = True
            return "\n"
        if c == 10:
            if self._last_cr:
                self._last_cr = False
                return ""
            return "\n"
        self._last_cr = False
        if c < 32 or c == 127:
            return ""                          # STX, EOT, NUL (idle) …
        return chr(c) if c < 128 else bytes([c]).decode("latin-1")

    @property
    def plausible(self) -> bool:
        return self.good_since is not None

    def view(self) -> dict:
        return {"id": self.id, "hz": round(self.hz, 1), "snr": round(self.snr_db),
                "q": round(self.metric), "text": self.text[-1500:],
                "n": self.nchars, "tx": [dict(x) for x in self.tx]}


def _refine(p: np.ndarray, bin_hz: float, c: float) -> float:
    """The comb's centre to a fraction of a hertz: its 16 lines (+-2 bins
    each) in the unsmoothed spectrum, the best of c +- 8 Hz, parabola."""
    n = len(p)
    offs = np.arange(-8.0, 8.5, 0.5)
    score = []
    for o in offs:
        tot = 0.0
        for off in _TONE_HZ:
            k = int(round((c + o + off) / bin_hz))
            tot += float(p[max(0, k - 2):min(n, k + 3)].sum())
        score.append(tot)
    score = np.convolve(score, np.ones(5) / 5, mode="same")      # the 1 Hz bins: smooth
    m = int(np.argmax(score))
    if 0 < m < len(score) - 1:
        a, b, d = score[m - 1], score[m], score[m + 1]
        den = a - 2 * b + d
        frac = 0.5 * (a - d) / den if den else 0.0
        return float(c + offs[m] + 0.5 * max(-1.0, min(1.0, frac)))
    return float(c + offs[m])


def comb_scan(p: np.ndarray, bin_hz: float, lo: float = F_LO, hi: float = F_HI) -> list[tuple[float, float]]:
    """(centre Hz, SNR dB) of MFSK32 combs in an averaged power spectrum.

    Each tone's power is summed over +-7 Hz (its main lobe; the noise
    averages out), relative to the same sum in the noise floor. A comb
    counts when its 16 tones are well above the floor, used evenly (RTTY's
    two lines or a carrier are not), and the two positions just outside
    its edges are quiet (that rules out the comb shifted by a tone)."""
    w = max(1, int(round(7 / bin_hz)))
    sm = np.convolve(p, np.ones(2 * w + 1), mode="same")
    floor = float(np.median(sm[int(200 / bin_hz):int(2800 / bin_hz)])) + 1e-20
    r = sm / floor                                       # 1.0 = noise
    n = len(r)
    cs = np.arange(lo, hi, 1.0)

    def at(off):
        return r[np.clip(np.round((cs + off) / bin_hz).astype(int), 0, n - 1)]

    tone = np.stack([at(off) for off in _TONE_HZ], axis=1)
    edge = np.maximum(at(-(NT + 1) / 2 * SPACING), at((NT + 1) / 2 * SPACING))
    mean = tone.mean(axis=1)
    med = np.median(tone, axis=1)
    active = (tone > 1.25).sum(axis=1)
    ok = ((mean >= 10 ** (COMB_DB / 10)) & (active >= 13)
          & (tone.max(axis=1) <= UNIFORM * med)
          & (mean - 1 >= EDGE_RATIO * np.maximum(edge - 1, 0.05)))
    found: list[tuple[float, float]] = []
    i = 0
    while i < len(cs):
        if not ok[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(cs) and ok[j + 1]:
            j += 1
        k = i + int(np.argmax(mean[i:j + 1]))
        found.append((_refine(p, bin_hz, float(cs[k])), float(mean[k])))
        i = j + 1
    # two MFSK32 signals cannot overlap: a weaker comb within one signal width
    # of a stronger one is its side lobe (or the comb shifted by some tones)
    keep: list[tuple[float, float]] = []
    for hz, sc in sorted(found, key=lambda f: -f[1]):
        if all(abs(hz - k[0]) > BANDWIDTH + 2 * SPACING for k in keep):
            keep.append((hz, sc))
    # SNR in 2500 Hz (as WSJT-X): 16 tones of (mean - 1) x the noise in the
    # (2w+1)-bin window, of which the window holds about SNR_CAL of a tone
    return [(hz, 10 * np.log10(max(sc - 1, 1e-3) * NT * (2 * w + 1) * bin_hz / 2500 / SNR_CAL))
            for hz, sc in sorted(keep)]


class MFSKSkimmer:
    def __init__(self) -> None:
        self.enabled = False
        self.reverse = False                   # LSB / DATA-L: tone order mirrored
        self._lock = threading.Lock()
        self._hist_in = np.zeros(_N - 1)
        self._phase = 0
        self._fbuf = np.zeros(0)
        self._spec: deque[np.ndarray] = deque(maxlen=HIST)
        self.channels: list[Channel] = []
        self.version = 0
        # the last BACKLOG_S of audio: a new channel decodes it first, so the
        # start of a transmission (before the comb was found) is not lost
        self._backlog: deque[np.ndarray] = deque()
        self._backlog_n = 0

    def feed(self, mono: np.ndarray, _t: float = 0.0) -> None:
        if not self.enabled:
            return
        x = np.concatenate([self._hist_in, mono.astype(np.float64) / 32768.0])
        y = np.convolve(x, _LP, mode="valid")
        self._hist_in = x[-(_N - 1):]
        y = y[self._phase::DEC]
        self._phase = (self._phase - len(mono)) % DEC
        self.feed8k(y)

    def feed8k(self, y: np.ndarray) -> None:
        with self._lock:
            changed = False
            for c in self.channels:
                c.reverse = self.reverse
                txt = c.demod(y)
                if txt:
                    c.text = (c.text + txt)[-TEXT_MAX:]
                    c.nchars += len(txt)
                    changed = True
            self._backlog.append(y)
            self._backlog_n += len(y)
            while self._backlog_n - len(self._backlog[0]) >= BACKLOG_S * FS:
                self._backlog_n -= len(self._backlog.popleft())
            self._fbuf = np.concatenate([self._fbuf, y])
            while len(self._fbuf) >= NFFT:
                self._spec.append(np.abs(np.fft.rfft(self._fbuf[:NFFT] * _WIN)) ** 2)
                self._fbuf = self._fbuf[HOP:]
                self._scan()
                changed = True
            if changed:
                self.version += 1

    def _scan(self) -> None:
        if len(self._spec) < 2:
            return
        p = np.mean(self._spec, axis=0)
        for hz, snr in comb_scan(p, FS / NFFT):
            near = [c for c in self.channels if abs(c.hz - hz) <= MATCH_HZ]
            if near:
                c = near[0]
                c.hz = 0.8 * c.hz + 0.2 * hz
                c.snr_db = snr
                c.last_seen = max(c.last_seen, time.monotonic() - DROP_S / 2)
            elif len(self.channels) < MAX_CH:
                c = Channel(hz)
                c.snr_db = snr
                c.reverse = self.reverse
                if self._backlog:
                    txt = c.demod(np.concatenate(list(self._backlog)))
                    if txt:
                        c.text, c.nchars = txt[-TEXT_MAX:], len(txt)
                self.channels.append(c)
        now = time.monotonic()
        self.channels = [c for c in self.channels if now - c.last_seen < DROP_S]
        # overlapping channels: only the better one stays (a side lobe, or one
        # created from a shifted comb before the real centre was found)
        best: list[Channel] = []
        for c in sorted(self.channels, key=lambda c: (not c.plausible, -c.best_metric, -c.snr_db)):
            if all(abs(c.hz - b.hz) > BANDWIDTH + 2 * SPACING for b in best):
                best.append(c)
        self.channels = sorted(best, key=lambda c: c.hz)

    def set_enabled(self, on: bool) -> None:
        with self._lock:
            self.enabled = on
            if not on:
                self.channels, self._spec = [], deque(maxlen=HIST)
                self._fbuf = np.zeros(0)
                self._backlog.clear()
                self._backlog_n = 0
            self.version += 1

    def clear(self) -> None:
        with self._lock:
            for c in self.channels:
                c.text = ""
                c.tx.clear()
            self.version += 1

    def mark_tx(self, hz: float, text: str, tol: float = 60.0) -> bool:
        with self._lock:
            near = [c for c in self.channels if abs(c.hz - hz) <= tol]
            if not near:
                return False
            c = min(near, key=lambda c: abs(c.hz - hz))
            c.tx.append({"at": c.nchars, "text": text, "t": time.time()})
            self.version += 1
            return True

    def snapshot(self) -> dict:
        with self._lock:
            return {"enabled": self.enabled, "reverse": self.reverse,
                    "channels": [c.view() for c in self.channels if c.plausible]}
