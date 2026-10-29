"""Raw capture from the SCU-17 codec with a UTC time anchor and a ring buffer.

This is the single owner of the ALSA capture device. Everything that needs RX
audio reads from here — the FT8/FT4 decoder takes exact UTC-aligned slices,
and live consumers (waterfall, later the Opus/WebRTC bridge) subscribe to
chunks. The decoder must never see Opus-compressed audio: the split happens
here, before any encoder.

Timing: ``arecord`` delivers 20 ms chunks. For every chunk, ``arrival -
samples_so_far / rate`` estimates the wall-clock time of sample 0; arrival is
never early, only late, so the *minimum* over a sliding window is the anchor.
The window (30 s) lets the anchor follow the codec crystal's drift against the
system clock (~100 ppm ≈ 0.36 s per hour). Measured result: jt9 reports
DT within ±0.1 s.
"""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections import deque
from typing import Callable, Optional

import numpy as np

log = logging.getLogger("ft950.audio")

RATE = 48_000
CHANNELS = 2            # PCM2903B delivers stereo; both channels are identical
CHUNK = RATE // 50      # 20 ms


def to_12k(x48: np.ndarray) -> np.ndarray:
    """Exact 48 kHz → 12 kHz resampling in the frequency domain.

    48000/12000 is an integer, so keeping the lowest quarter of the spectrum
    and inverting at a quarter of the length is a perfect decimator. A soft
    taper removes hum below ~150 Hz (measured −17 dB under 100 Hz) and rolls
    off above 5.5 kHz. Callers pass a little context on both ends and crop,
    which keeps the circular edge effects out of the slice.
    """
    n = len(x48) - len(x48) % 4
    X = np.fft.rfft(x48[:n])
    f = np.fft.rfftfreq(n, 1 / RATE)
    keep = n // 8 + 1
    taper = (np.clip((f[:keep] - 100) / 100, 0, 1)
             * np.clip((5800 - f[:keep]) / 300, 0, 1))
    return np.fft.irfft(X[:keep] * taper, n=n // 4) / 4


def apply_gain(x: np.ndarray, db: float) -> np.ndarray:
    """int16 samples × 10^(db/20), clipped to int16."""
    return np.clip(np.round(x.astype(np.float32) * 10 ** (db / 20)), -32768, 32767).astype(np.int16)


class AudioCapture:
    """Background ``arecord`` reader with UTC-anchored ring buffer."""

    def __init__(self, device: str, ring_seconds: float = 40.0) -> None:
        self.device = device
        self._ring = np.zeros(int(ring_seconds * RATE), dtype=np.int16)
        self._total = 0                    # samples written since start
        self._est: deque[float] = deque(maxlen=int(30 * 50))  # 30 s of chunks
        self._anchor: Optional[float] = None
        self._lock = threading.Lock()
        self._subs: list[Callable[[np.ndarray, float], None]] = []
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.error: Optional[str] = None
        self.level = {"rms_dbfs": None, "peak_dbfs": None}
        # RX dropout diagnostics (Settings ▸ Pi Hardware), since the service
        # started: arecord's overruns (the codec's buffer overflowed: samples
        # lost), chunks whose subscribers took more than 15 ms of their 20 ms,
        # and the longest such pass
        self.overruns = 0
        self.slow = 0
        self.max_ms = 0.0
        self.restarts = 0
        self._stderr: deque[str] = deque(maxlen=20)
        # DIGI RX level (software, dB): applied by the FT8/FT4 decoder, the
        # waterfall and the DIGI meter only (digi_level). Browser audio and the
        # recorder get the codec signal as is; the radio is never touched.
        self.gain_db = 0.0

    def digi_level(self) -> dict:
        """Codec level shifted by the DIGI gain (what jt9 gets; peak caps at 0)."""
        r, p = self.level.get("rms_dbfs"), self.level.get("peak_dbfs")
        g = self.gain_db
        return {"rms_dbfs": None if r is None else round(r + g, 1),
                "peak_dbfs": None if p is None else round(min(0.0, p + g), 1)}

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="audio-capture",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._proc:
            self._proc.terminate()
        if self._thread:
            self._thread.join(timeout=2)

    def set_device(self, device: str) -> None:
        """Switch the ALSA device: ending arecord makes the capture thread
        restart with the new device (its normal recovery path)."""
        self.device = device
        if self._proc:
            self._proc.terminate()

    @property
    def running(self) -> bool:
        return self._anchor is not None and self.error is None

    def subscribe(self, fn: Callable[[np.ndarray, float], None]) -> None:
        """``fn(mono_int16_chunk, utc_of_first_sample)`` per 20 ms chunk."""
        self._subs.append(fn)

    # -- capture thread ----------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._capture()
            except Exception as exc:  # noqa: BLE001
                self.error = str(exc)
                log.warning("audio capture stopped: %s", exc)
            if not self._stop.is_set():
                time.sleep(2)              # device unplugged / busy: retry

    def stats(self) -> dict:
        return {"overruns": self.overruns, "slow": self.slow,
                "max_ms": round(self.max_ms, 1), "restarts": self.restarts}

    def _read_stderr(self, proc: subprocess.Popen) -> None:
        """arecord reports every overrun on stderr ("overrun!!! (at least … ms
        long)"); count them, keep the last lines for an error message."""
        assert proc.stderr is not None
        for raw in proc.stderr:
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            self._stderr.append(line)
            if "overrun" in line.lower():
                self.overruns += 1
                if self.overruns == 1 or self.overruns % 50 == 0:
                    log.warning("RX audio: %s (%d so far)", line, self.overruns)

    def _capture(self) -> None:
        # no -q: arecord then reports overruns, counted by _read_stderr
        self._proc = subprocess.Popen(
            ["arecord", "-D", self.device, "-f", "S16_LE",
             "-r", str(RATE), "-c", str(CHANNELS), "-t", "raw",
             "--buffer-time=40000"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self._stderr.clear()
        err_reader = threading.Thread(target=self._read_stderr, args=(self._proc,),
                                      name="arecord-stderr", daemon=True)
        err_reader.start()
        with self._lock:                   # a restart is a new timeline
            self._total, self._anchor = 0, None
            self._est.clear()
        nbytes = CHUNK * CHANNELS * 2
        assert self._proc.stdout is not None
        while not self._stop.is_set():
            raw = self._proc.stdout.read(nbytes)
            now = time.time()
            if len(raw) < nbytes:
                if self._stop.is_set():
                    return
                err_reader.join(timeout=1)
                self.restarts += 1
                err = " · ".join(l for l in self._stderr if not l.startswith("Recording"))
                raise RuntimeError(f"arecord ended: {err.strip() or 'EOF'}")
            mono = np.frombuffer(raw, "<i2").reshape(-1, CHANNELS)[:, 0]
            with self._lock:
                i = self._total % len(self._ring)
                self._ring[i:i + CHUNK] = mono          # ring size is a CHUNK multiple
                self._total += CHUNK
                self._update_anchor(now)
                t_chunk = self._anchor + (self._total - CHUNK) / RATE
            self.error = None
            t0 = time.perf_counter()
            self._update_level(mono)
            for fn in self._subs:
                try:
                    fn(mono, t_chunk)
                except Exception as exc:  # noqa: BLE001
                    log.warning("audio subscriber failed: %s", exc)
            dt = (time.perf_counter() - t0) * 1000.0
            if dt > self.max_ms:
                self.max_ms = dt
            if dt > 15.0:                  # of the 20 ms a chunk lasts
                self.slow += 1

    def _update_anchor(self, now: float) -> None:
        """Sliding-minimum anchor that also survives clock steps.

        A backward step lowers the estimate and the minimum takes it at once.
        A forward step (NTP syncing a Pi without RTC after boot: measured
        +30 s at 16:42:02) would leave the minimum stuck on the old timeline
        for the whole window, so: if even the *earliest* arrival of the last
        second sits more than 0.5 s above the anchor — far beyond arrival
        jitter — the clock moved, and the window restarts from there.
        """
        self._est.append(now - self._total / RATE)
        recent = list(self._est)[-50:]                 # last second of chunks
        if self._anchor is not None and min(recent) - self._anchor > 0.5:
            log.info("clock stepped by %+.2f s — re-anchoring",
                     min(recent) - self._anchor)
            self._est = deque(recent, maxlen=self._est.maxlen)
        self._anchor = min(self._est)

    def _update_level(self, mono: np.ndarray) -> None:
        x = mono.astype(np.float64) / 32768
        rms = float(np.sqrt(np.mean(x * x))) or 1e-9
        peak = float(np.max(np.abs(x))) or 1e-9
        self.level = {"rms_dbfs": round(20 * np.log10(rms), 1),
                      "peak_dbfs": round(20 * np.log10(peak), 1)}

    # -- slices ------------------------------------------------------------
    def extract(self, t_start: float, seconds: float) -> Optional[np.ndarray]:
        """Samples (float64, 48 kHz) covering [t_start, t_start + seconds).

        None if not yet captured, already overwritten, or across a restart.
        """
        with self._lock:
            if self._anchor is None:
                return None
            i0 = int(round((t_start - self._anchor) * RATE))
            n = int(round(seconds * RATE))
            if i0 < max(0, self._total - len(self._ring)) or i0 + n > self._total:
                return None
            idx = np.arange(i0, i0 + n) % len(self._ring)
            return self._ring[idx].astype(np.float64)
