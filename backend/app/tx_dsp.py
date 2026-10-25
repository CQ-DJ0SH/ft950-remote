"""Voice TX processing: compressor and pre-emphasis (switchable in Settings ▸
Audio), plus the low-pass and limiter they need — taken over from TM-V71
Remote (same author), where they were measured and tuned.

Chain per 20 ms mic block, in this order: compressor (level) → pre-emphasis
(tilt) → voice low-pass (removes the lifted hiss above the SSB band) →
limiter (last: nothing clips). For SSB the low-pass sits at 3 kHz — the
FT-950's TX passband (menu 064) ends at 2.9 kHz anyway.
"""
from __future__ import annotations

import numpy as np

# SSB voice: +6 dB/octave above 700 Hz, unity at 1 kHz → about −4 dB at 300 Hz
# and +7 dB at 2.7 kHz. (The FM value of TM-V71 Remote, 75 µs = 2.1 kHz, lifted
# only ~+4 dB below the 3 kHz low-pass — inaudible on SSB.)
PREEMPH_US = 227.0         # 1 / (2π · 700 Hz)
PREEMPH_REF_HZ = 1000.0
LOWPASS_HZ = 3000.0




class FIRLowpass:
    """Windowed-sinc FIR low-pass, stateful across blocks via overlap-save.

    Vectorised (np.convolve), so it adds no per-sample Python loop to the audio
    path. ``process`` filters an int16 mono block and returns int16; the last
    ntaps-1 samples are carried over so block boundaries stay continuous."""

    def __init__(self, fc: float, fs: float, ntaps: int = 63):
        n = np.arange(ntaps)
        m = (ntaps - 1) / 2.0
        fcn = fc / (fs / 2.0)                       # cutoff as a fraction of Nyquist
        h = fcn * np.sinc(fcn * (n - m)) * np.hamming(ntaps)
        self.h = (h / h.sum()).astype(np.float64)   # unity DC gain
        self._tail = np.zeros(ntaps - 1, dtype=np.float64)

    def process(self, pcm: np.ndarray) -> np.ndarray:
        x = np.concatenate([self._tail, pcm.astype(np.float64)])
        y = np.convolve(x, self.h, mode="valid")    # len == len(pcm)
        self._tail = x[-(self.h.size - 1):]
        return np.clip(y, -32768, 32767).astype(np.int16)

    def reset(self) -> None:
        self._tail[:] = 0.0


class PreEmphasis:
    """1st-order FM pre-emphasis for the TX path — the exact inverse of
    _DeEmphasis, same time constant, so what one lifts the other lowers.

    Only meaningful into a FLAT input (the 9600-baud data port). The radio's own
    mic input and its 1200-baud input apply pre-emphasis themselves; doing it
    here as well would lift the treble twice and sound shrill.

    y[n] = (x[n] − a·x[n−1]) / (1 − a): unity gain at DC, +6 dB/octave above the
    corner (1/2πτ). A one-zero FIR keeps it loop-free. The lift keeps rising to
    Nyquist (+17 dB at 75 µs), so it is always followed by the voice low-pass —
    otherwise hiss above the voice band would go out lifted as well."""

    def __init__(self, tau_us: float, fs: float, ref_hz: float | None = None):
        self.a = float(np.exp(-1.0 / (tau_us * 1e-6 * fs)))
        # gain: unity at DC (FM use), or unity at ref_hz so the overall
        # loudness stays put and only the tilt changes (SSB voice)
        if ref_hz:
            w = 2 * np.pi * ref_hz / fs
            self.k = 1.0 / float(np.abs(1 - self.a * np.exp(-1j * w)))
        else:
            self.k = 1.0 / (1.0 - self.a)
        self._x1 = 0.0

    def gain_at(self, hz: float, fs: float) -> float:
        """Linear gain of the filter at ``hz`` (for tests and documentation)."""
        return self.k * float(np.abs(1 - self.a * np.exp(-2j * np.pi * hz / fs)))

    def process(self, pcm: np.ndarray) -> np.ndarray:
        x = pcm.astype(np.float64)
        prev = np.concatenate(([self._x1], x[:-1]))
        self._x1 = float(x[-1]) if x.size else self._x1
        y = (x - self.a * prev) * self.k
        return np.clip(y, -32768, 32767).astype(np.int16)

    def reset(self) -> None:
        self._x1 = 0.0


class Limiter:
    """Peak ceiling for the end of the TX chain — ramped gain, never a hard cut.

    Pre-emphasis lifts the treble, and both the AGC and a loud syllable can push
    the lifted peaks past full scale. Clipping them in int16 is not a quiet
    failure: it turns every sibilant into a burst of broadband crackle, and the
    emphasis stage sits right before the band-limiting low-pass, so that crackle
    lands inside the voice band where it cannot be filtered out again. Gain is
    reduced over 2.5 ms slices and ramped between them, so nothing steps."""

    SLICE = 120

    def __init__(self, fs: float, ceiling_db: float = -1.0,
                 release_ms: float = 120.0):
        self.ceiling = 32768.0 * 10 ** (ceiling_db / 20.0)
        self.rel = 1.0 - np.exp(-(self.SLICE / fs) / (release_ms / 1000.0))
        self.reset()

    def reset(self) -> None:
        self._gain = 1.0
        self.hits = 0                     # slices that needed limiting

    def process(self, pcm: np.ndarray) -> np.ndarray:
        x = pcm.astype(np.float64)
        n = x.size
        if not n:
            return pcm
        k = self.SLICE
        m = max(1, n // k)
        out = np.empty(n, dtype=np.float64)
        pos = 0
        for i in range(m):
            end = n if i == m - 1 else pos + k
            seg = x[pos:end]
            peak = float(np.max(np.abs(seg))) if seg.size else 0.0
            need = self.ceiling / peak if peak > self.ceiling else 1.0
            if need < self._gain:
                g = need                  # attack: immediate, or it clips
                self.hits += 1
            else:                         # release: slow, or it pumps
                g = self._gain + (min(1.0, need) - self._gain) * self.rel
            ramp = np.linspace(self._gain, g, seg.size, endpoint=False)
            ramp = np.minimum(ramp, need) if need < 1.0 else ramp
            out[pos:end] = seg * ramp
            self._gain = g
            pos = end
        return np.clip(out, -32768, 32767).astype(np.int16)


class Compressor:
    """Feed-forward dynamics compressor with makeup gain and a peak ceiling.

    Raises the AVERAGE modulation: quiet syllables and trailing words come up,
    loud bursts do not overdeviate. That is different from the AGC, which rides
    the overall level slowly (tens of seconds); this acts within a syllable.

    Levels are measured in 2.5 ms slices rather than per 20 ms block — at block
    resolution the attack would let the first 20 ms of a loud word through
    uncompressed, and the gain would step audibly between blocks. The gain is
    ramped linearly across each slice for the same reason. Below the gate
    threshold the makeup gain is withheld, so the pauses between words (room
    noise, fan, breath) are not pumped up to speech level."""

    SLICE = 120                          # 2.5 ms @ 48 kHz

    def __init__(self, fs: float, threshold_db: float = -26.0, ratio: float = 3.0,
                 knee_db: float = 6.0, attack_ms: float = 5.0,
                 release_ms: float = 180.0, makeup_db: float = 9.0,
                 gate_db: float = -52.0, ceiling_db: float = -3.0):
        self.thr, self.ratio, self.knee = threshold_db, ratio, knee_db
        self.makeup, self.gate = makeup_db, gate_db
        self.ceiling = 32768.0 * 10 ** (ceiling_db / 20.0)
        dt = self.SLICE / fs
        self.att = 1.0 - np.exp(-dt / (attack_ms / 1000.0))
        self.rel = 1.0 - np.exp(-dt / (release_ms / 1000.0))
        self.reset()

    def reset(self) -> None:
        self._env_db = -90.0
        self._gain = 1.0                 # last applied linear gain (ramp start)
        self.gain_db = 0.0               # exposed for the status readout

    def _curve(self, lvl_db: float) -> float:
        """Static gain (dB) for an input level: soft-knee downward compression."""
        over = lvl_db - self.thr
        if 2 * over <= -self.knee:
            g = 0.0
        elif 2 * abs(over) <= self.knee:
            g = (1.0 / self.ratio - 1.0) * (over + self.knee / 2) ** 2 / (2 * self.knee)
        else:
            g = (1.0 / self.ratio - 1.0) * over
        return g

    def process(self, pcm: np.ndarray) -> np.ndarray:
        x = pcm.astype(np.float64)
        n = x.size
        if not n:
            return pcm
        k = self.SLICE
        m = max(1, n // k)
        out = np.empty(n, dtype=np.float64)
        pos = 0
        for i in range(m):                        # 8 slices per block: cheap
            end = n if i == m - 1 else pos + k
            seg = x[pos:end]
            rms = float(np.sqrt(np.mean(seg * seg)))
            lvl = 20.0 * np.log10(rms / 32768.0) if rms >= 1.0 else -90.0
            c = self.att if lvl > self._env_db else self.rel
            self._env_db += (lvl - self._env_db) * c
            gdb = self._curve(self._env_db)
            if self._env_db > self.gate:          # makeup only on speech
                gdb += self.makeup
            g = 10 ** (gdb / 20.0)
            peak = float(np.max(np.abs(seg))) if seg.size else 0.0
            start = self._gain
            if peak > 0:
                lim = self.ceiling / peak
                g = min(g, lim)                   # never past the ceiling...
                # ...not even at the START of the ramp. The ramp begins at the
                # previous slice's gain, and after a pause that is the full
                # makeup gain: the first samples of a loud onset went out at
                # -1 dBFS against a -3 dBFS ceiling. Peaks are limited at once.
                start = min(start, lim)
            ramp = np.linspace(start, g, seg.size, endpoint=False)
            out[pos:end] = seg * ramp
            self._gain = g
            pos = end
        self.gain_db = round(20.0 * np.log10(max(self._gain, 1e-6)), 1)
        return np.clip(out, -32768, 32767).astype(np.int16)
