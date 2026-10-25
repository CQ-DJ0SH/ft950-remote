"""CW skimmer: decode every CW signal in the receive passband at once.

Like the FT8 list, each carrier gets its own row (pitch, speed, running
text); the operator picks one. The timing logic (dot/dash/gap classification
with adaptive speed) is TM-V71 Remote's CWDecoder; what is new is the front
end. On HF several signals share the passband, so the V71 gate (tone energy
vs. total energy) does not work — here every channel has its own envelope
from a short-time spectrum and its own adaptive threshold.

Pipeline (fed by AudioCapture with 20 ms chunks of 48 kHz mono):

* low-pass + decimate to 8 kHz;
* STFT: 20 ms Hann window, 5 ms hop, 256-point FFT (31 Hz bins);
* every 0.5 s: peaks in 300–1500 Hz well above the median floor of the last
  2 s become channels (at most MAX_CH, matched by pitch ±40 Hz); a channel
  that stays silent for DROP_S is dropped;
* per channel and frame: magnitude at its bin → mark/space with hysteresis
  between a slow noise floor and a fast-attack peak → Morse timing.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Optional

import numpy as np

FS_IN = 48_000
DEC = 6
FS = FS_IN // DEC              # 8 kHz
WIN, HOP, NFFT = 160, 40, 256  # 20 ms window, 5 ms hop
BIN_HZ = FS / NFFT             # 31.25 Hz
F_LO, F_HI = 300.0, 1500.0
SCAN_EVERY = 100               # frames (0.5 s)
HIST = 400                     # frames of spectrum history for the floor (2 s)
PEAK_DB = 10.0                 # a carrier must stand this far above the floor
MAX_CH = 10
MATCH_HZ = 40.0
LEAK_HZ, LEAK_RATIO = 200.0, 4.0   # ±200 Hz, 12 dB
DROP_S = 45.0
TEXT_MAX = 400

MORSE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.", "G": "--.",
    "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..", "M": "--", "N": "-.",
    "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.", "S": "...", "T": "-", "U": "..-",
    "V": "...-", "W": ".--", "X": "-..-", "Y": "-.--", "Z": "--..",
    "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-", "5": ".....",
    "6": "-....", "7": "--...", "8": "---..", "9": "----.",
    ".": ".-.-.-", ",": "--..--", "?": "..--..", "/": "-..-.", "=": "-...-", "+": ".-.-.",
    "-": "-....-", "@": ".--.-.",
}
INV_MORSE = {v: k for k, v in MORSE.items()}

# windowed-sinc low-pass for the decimation (cut-off 3.2 kHz at 48 kHz)
_N = 63
_t = np.arange(_N) - (_N - 1) / 2
_LP = np.sinc(2 * 3200 / FS_IN * _t) * np.hamming(_N)
_LP /= _LP.sum()
_HANN = np.hanning(WIN)


class Timing:
    """Mark/space run lengths (in 5 ms frames) → characters. TM-V71 Remote's
    CWDecoder logic: adaptive dot length within ~5…40 WPM, re-seed at 20 WPM
    after repeated garbage, char gap at 2 dots, word gap at 6."""

    def __init__(self, wpm: float = 20.0):
        wdur = HOP / FS
        self.wdur = wdur
        self.dot_min = max(1, round((1.2 / 45.0) / wdur))
        self.dot_max = max(self.dot_min + 1, round((1.2 / 5.0) / wdur))
        self.dot_seed = max(1, round((1.2 / 20.0) / wdur))
        self.dot = self._clamp(int((1.2 / wpm) / wdur))
        self.state, self.run = False, 0
        self.symbol, self._space_pending, self._fail = "", False, 0
        self.wpm_ok = wpm                  # speed of the last correctly decoded characters

    def _clamp(self, d: int) -> int:
        return min(self.dot_max, max(self.dot_min, d))

    @property
    def wpm(self) -> float:
        return 1.2 / (self.dot * self.wdur)

    def _mark(self, length: int) -> None:
        if length < 2 * self.dot:
            self.symbol += "."
            self.dot = self._clamp(int(round(0.7 * self.dot + 0.3 * length)))
        else:
            self.symbol += "-"
            self.dot = self._clamp(int(round(0.7 * self.dot + 0.3 * max(1, length / 3))))

    def _flush(self) -> str:
        if not self.symbol:
            return ""
        ch = INV_MORSE.get(self.symbol, "")
        self.symbol = ""
        if ch:
            self._fail = 0
            self.wpm_ok = 0.7 * self.wpm_ok + 0.3 * self.wpm
        else:
            self._fail += 1
            if self._fail >= 4:
                self._fail = 0
                self.dot = self._clamp(self.dot_seed)
        return ch

    def step(self, tone: bool) -> str:
        out = ""
        if tone != self.state:
            if self.state:
                self._mark(self.run)
                self._space_pending = True
            elif self.run >= 2 * self.dot:
                out = self._flush()
            self.state, self.run = tone, 1
            return out
        self.run += 1
        if not self.state and self._space_pending:
            if self.symbol and self.run == 2 * self.dot:
                out = self._flush()
            if self.run == 6 * self.dot:
                out += " "
                self._space_pending = False
        return out


class Channel:
    _ids = 0

    def __init__(self, hz: float):
        Channel._ids += 1
        self.id = Channel._ids
        self.hz = hz
        self.timing = Timing()
        self.text = ""
        self.noise, self.peak = 1e-6, 1e-5
        self.on = False
        self.last_mark = time.monotonic()
        self.snr_db = 0.0

    def frame(self, mag: float) -> str:
        # slow noise floor (follows down fast, up slow), fast-attack peak
        self.noise = mag if mag < self.noise else 0.999 * self.noise + 0.001 * mag
        # the peak fades over ~10 s, so the threshold stays up between overs
        # and noise after the last character is not keyed as dots
        self.peak = mag if mag > self.peak else 0.9995 * self.peak + 0.0005 * self.noise
        span = self.peak - self.noise
        hi, lo = self.noise + 0.55 * span, self.noise + 0.35 * span   # hysteresis
        if self.peak > 4 * self.noise:                                 # ≥ 12 dB above noise
            self.on = mag > (lo if self.on else hi)
        else:
            self.on = False
        if self.on:
            self.last_mark = time.monotonic()
        self.snr_db = 20 * np.log10(max(self.peak, 1e-12) / max(self.noise, 1e-12))
        ch = self.timing.step(self.on)
        if ch:
            if ch == " " and (not self.text or self.text.endswith(" ")):
                return ""
            self.text = (self.text + ch)[-TEXT_MAX:]
        return ch

    @property
    def plausible(self) -> bool:
        """Flickering noise keys runs of dots: almost only E I S H 5. Real CW
        mixes in dashes. Judged on the last 30 decoded characters."""
        chars = [c for c in self.text[-60:] if c != " "][-30:]
        if len(chars) < 8:
            return True
        dotty = sum(c in "EISH5" for c in chars) / len(chars)
        return dotty < 0.7

    def view(self) -> dict:
        return {"id": self.id, "hz": round(self.hz, 1), "wpm": round(self.timing.wpm_ok),
                "snr": round(self.snr_db), "text": self.text[-160:], "on": self.on}


class CWSkimmer:
    def __init__(self) -> None:
        self.enabled = False
        self._lock = threading.Lock()
        self._hist_in = np.zeros(_N - 1)
        self._phase = 0
        self._buf = np.zeros(0)
        self._spec: deque[np.ndarray] = deque(maxlen=HIST)
        self._frames = 0
        self.channels: list[Channel] = []
        self.version = 0                   # bumps when text changes (for the broadcaster)

    # capture-thread callback
    def feed(self, mono: np.ndarray, _t: float = 0.0) -> None:
        if not self.enabled:
            return
        x = np.concatenate([self._hist_in, mono.astype(np.float64) / 32768.0])
        y = np.convolve(x, _LP, mode="valid")
        self._hist_in = x[-(_N - 1):]
        y = y[self._phase::DEC]
        self._phase = (self._phase - len(mono)) % DEC
        self._buf = np.concatenate([self._buf, y])
        n = (len(self._buf) - WIN) // HOP + 1
        if n <= 0:
            return
        idx = np.arange(WIN)[None, :] + HOP * np.arange(n)[:, None]
        frames = self._buf[idx] * _HANN
        self._buf = self._buf[n * HOP:]
        mags = np.abs(np.fft.rfft(frames, NFFT, axis=1))
        with self._lock:
            changed = False
            for m in mags:
                self._spec.append(m)
                self._frames += 1
                if self._frames % SCAN_EVERY == 0:
                    self._scan()
                for c in self.channels:
                    b = int(round(c.hz / BIN_HZ))
                    if c.frame(float(m[max(0, b - 1):b + 2].max())):
                        changed = True
            if changed:
                self.version += 1

    def _scan(self) -> None:
        spec = np.mean(self._spec, axis=0)
        lo, hi = int(F_LO / BIN_HZ), int(F_HI / BIN_HZ)
        band = spec[lo:hi]
        floor = float(np.median(band)) + 1e-12
        # strongest frames matter for keyed CW: use the 90th percentile per bin
        p90 = np.percentile(np.asarray(self._spec)[:, lo:hi], 90, axis=0)
        found = []
        for i in range(1, len(p90) - 1):
            if p90[i] >= p90[i - 1] and p90[i] >= p90[i + 1] and 20 * np.log10(p90[i] / floor) >= PEAK_DB:
                a, b, c = p90[i - 1], p90[i], p90[i + 1]
                off = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) else 0.0
                found.append(((lo + i + off) * BIN_HZ, float(p90[i])))
        # Keying sidebands of a strong carrier show up as weaker peaks next to
        # it; a peak within LEAK_HZ of one LEAK_RATIO stronger is not a signal.
        accepted: list[tuple[float, float]] = []
        for hz, lvl in sorted(found, key=lambda f: -f[1]):
            if any(abs(h - hz) <= LEAK_HZ and l >= LEAK_RATIO * lvl for h, l in accepted):
                continue
            accepted.append((hz, lvl))
        for hz, _ in accepted:
            if any(abs(c.hz - hz) <= MATCH_HZ for c in self.channels):
                continue
            if len(self.channels) < MAX_CH:
                self.channels.append(Channel(hz))
        now = time.monotonic()
        def leak(c):
            near = [l for h, l in accepted if abs(h - c.hz) <= MATCH_HZ]
            own = max(near) if near else 0.0
            return any(abs(h - c.hz) <= LEAK_HZ and abs(h - c.hz) > MATCH_HZ and l >= LEAK_RATIO * max(own, 1e-12)
                       for h, l in accepted)
        self.channels = [c for c in self.channels if now - c.last_mark < DROP_S and not leak(c)]
        self.channels.sort(key=lambda c: c.hz)

    def set_enabled(self, on: bool) -> None:
        with self._lock:
            self.enabled = on
            if not on:
                self.channels, self._spec = [], deque(maxlen=HIST)
                self._buf = np.zeros(0)

    def clear(self) -> None:
        with self._lock:
            for c in self.channels:
                c.text = ""
            self.version += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {"enabled": self.enabled,
                    "channels": [c.view() for c in self.channels if c.plausible]}


def cw_duration_s(text: str, wpm: float) -> float:
    """Length of ``text`` in CW at ``wpm`` (PARIS: 50 dot units per word)."""
    units = 0
    for ch in text.upper():
        if ch == " ":
            units += 4                          # 7 between words, 3 already counted
            continue
        code = MORSE.get(ch)
        if not code:
            continue
        units += sum(1 if s == "." else 3 for s in code) + len(code) - 1 + 3
    return units * 1.2 / max(4.0, wpm)
