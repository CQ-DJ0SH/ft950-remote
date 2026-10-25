"""Audio-passband spectrum frames (0–3000 Hz) for the browser waterfall.

FT8 tones are 6.25 Hz apart, so the FFT must resolve a few Hz: 16384 points
at 48 kHz = 2.93 Hz per bin (a 341 ms Hann window), one frame every 100 ms.
Frames are only computed while someone is watching. The FFT runs in the
capture thread (~1 ms on the Pi 4) and is handed to the event loop.
"""
from __future__ import annotations

import asyncio

import numpy as np

from .audio import RATE, AudioCapture
from .state import ConnectionManager

NFFT = 16384
F_MAX = 3000
EVERY = 5                                   # chunks (20 ms) per frame
_WIN = np.hanning(NFFT)
_BINS = int(F_MAX * NFFT / RATE)            # 1024 bins up to 3000 Hz


class Waterfall:
    def __init__(self, capture: AudioCapture, manager: ConnectionManager,
                 loop: asyncio.AbstractEventLoop) -> None:
        self.manager = manager
        self.capture = capture                  # its gain_db (DIGI RX level) shifts the spectrum
        self.loop = loop
        self._buf = np.zeros(NFFT, dtype=np.float64)
        self._n = 0
        capture.subscribe(self._on_chunk)

    def _on_chunk(self, mono: np.ndarray, utc: float) -> None:
        k = len(mono)
        self._buf[:-k] = self._buf[k:]
        self._buf[-k:] = mono
        self._n += 1
        if self._n % EVERY or not self.manager.count:
            return
        X = np.fft.rfft(self._buf * _WIN)[:_BINS]
        db = 20 * np.log10(np.abs(X) / (NFFT * 16384) + 1e-12) + self.capture.gain_db
        frame = {"t": round(utc, 2), "hz_per_bin": RATE / NFFT,
                 "db": np.round(db).astype(int).tolist()}
        asyncio.run_coroutine_threadsafe(self.manager.broadcast(frame), self.loop)
