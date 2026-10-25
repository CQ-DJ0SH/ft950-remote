"""Raw RX audio recorder (as in TM-V71 Remote): REC / play / WAV download.

Fed by the capture thread with 20 ms chunks of 48 kHz mono. To keep the Pi's
RAM in check the take is low-passed and decimated to 16 kHz on the fly (SSB
and digi audio stay below ~3 kHz, the filter passes up to ~7 kHz) and capped
at 30 minutes (~58 MB); beyond that the oldest audio is dropped.
"""
from __future__ import annotations

import io
import threading
import wave

import numpy as np

IN_RATE = 48_000
FACTOR = 3
RATE = IN_RATE // FACTOR
CAP_S = 30 * 60

# windowed-sinc low-pass, cut-off 7 kHz at 48 kHz, 63 taps
_N = 63
_t = np.arange(_N) - (_N - 1) / 2
_TAPS = np.sinc(2 * 7000 / IN_RATE * _t) * np.hamming(_N)
_TAPS /= _TAPS.sum()


class Recorder:
    def __init__(self) -> None:
        self.on = False
        self._lock = threading.Lock()
        self._chunks: list[np.ndarray] = []
        self._n = 0
        self._hist = np.zeros(_N - 1)          # filter state across chunks
        self._phase = 0                        # decimation phase across chunks

    # capture-thread callback
    def feed(self, mono: np.ndarray, _t: float = 0.0) -> None:
        if not self.on:
            return
        x = np.concatenate([self._hist, mono.astype(np.float64)])
        y = np.convolve(x, _TAPS, mode="valid")          # len == len(mono)
        self._hist = x[-(_N - 1):]
        out = y[self._phase::FACTOR]
        self._phase = (self._phase - len(mono)) % FACTOR
        pcm = np.clip(np.round(out), -32768, 32767).astype(np.int16)
        with self._lock:
            self._chunks.append(pcm)
            self._n += len(pcm)
            while self._n > CAP_S * RATE and len(self._chunks) > 1:
                self._n -= len(self._chunks.pop(0))

    def set(self, on: bool) -> None:
        if on and not self.on:
            self.clear()                       # fresh take
        self.on = on

    def clear(self) -> None:
        with self._lock:
            self._chunks, self._n = [], 0
            self._hist[:] = 0
            self._phase = 0

    def status(self) -> dict:
        with self._lock:
            n = self._n
        return {"recording": self.on, "rec_seconds": round(n / RATE, 1), "rec_bytes": n * 2}

    def wav_bytes(self) -> bytes:
        with self._lock:
            data = np.concatenate(self._chunks) if self._chunks else np.zeros(0, np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(data.tobytes())
        return buf.getvalue()
