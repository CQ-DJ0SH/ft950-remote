"""JTTY: the keyboard mode of WSJT-X 3.2+ (K1JT, K9AN, G4KLA and the WSJT-X
team) - typed like RTTY, any time, no T/R periods, but 4-GFSK at 31.25 baud
(127 Hz) with FEC: readable down to about -15 dB.

Receive: jttyd (decoder/jttyd, built against WSJT-X 3.3's libwsjt_fort)
runs while DECODE is on and gets the live receive audio, 48 kHz cut down to
12 kHz, on its stdin; it prints every receive update as a JSON line. Its
first step sets up the decoder's tables (20-35 s on a Pi 5), then a step
of 0.47 s of audio takes ~30 ms over the whole band. WSJT-X's decoder
searches the receive frequency +- tolerance plus 1200-1800 Hz; with the
tolerance over the whole passband it skims everything.

Transmit: ``jttyd --tx TEXT`` renders the tones (7 ms) at 48 kHz.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from collections import deque
from typing import Optional

import numpy as np

log = logging.getLogger("ft950.jtty")

BIN = os.environ.get("FT950_JTTYD", "/usr/local/bin/jttyd")
IN_RATE, RATE, DEC = 48_000, 12_000, 4
LOW_HZ, HIGH_HZ = 200, 2800
MAX_MSGS = 80                       # messages kept for the panel

# windowed-sinc low-pass before taking every 4th sample (cut-off 5 kHz at 48 kHz)
_N = 63
_k = np.arange(_N) - (_N - 1) / 2
_LP = np.sinc(2 * 5000 / IN_RATE * _k) * np.hamming(_N)
_LP /= _LP.sum()

# dial frequencies (USB / DATA-U) from WSJT-X 3.3's default list - preliminary
BANDS = [("160", 1_839_000), ("80", 3_572_000), ("40", 7_078_000), ("30", 10_140_000),
         ("20", 14_090_000), ("17", 18_104_000), ("15", 21_090_000), ("12", 24_920_000),
         ("10", 28_090_000), ("6", 50_160_000)]


def available() -> bool:
    return os.path.isfile(BIN) and os.access(BIN, os.X_OK)


class JttyReceiver:
    def __init__(self) -> None:
        self.enabled = False
        self.state = "off"                   # off | starting | ready | error
        self.error = ""
        self.version = 0
        self.msgs: dict[int, dict] = {}      # message id -> the latest update
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._q: deque[bytes] = deque(maxlen=1500)      # 30 s of 20 ms chunks
        self._have = threading.Event()
        self._hist = np.zeros(_N - 1)
        self._phase = 0
        self._fed = 0                        # 12 kHz samples handed to jttyd
        self.dropped = 0
        self._started = 0.0
        self._warm_ids: set[int] = set()

    # -- audio in ---------------------------------------------------------------
    def feed(self, mono: np.ndarray, t: float = 0.0) -> None:
        """48 kHz int16 chunk from the capture (its thread)."""
        if not self.enabled or self._proc is None:
            return
        x = np.concatenate([self._hist, mono.astype(np.float64)])
        y = np.convolve(x, _LP, mode="valid")
        self._hist = x[-(_N - 1):]
        y = y[self._phase::DEC]
        self._phase = (self._phase - len(mono)) % DEC
        if self.state == "starting":
            return              # warming up on its own test signal (see set_enabled)
        if len(self._q) == self._q.maxlen:
            self.dropped += 1
        self._q.append(np.clip(y, -32767, 32767).astype("<i2").tobytes())
        self._have.set()

    def _writer(self, proc: subprocess.Popen) -> None:
        while proc.poll() is None and self._proc is proc:
            if not self._have.wait(0.5):
                continue
            self._have.clear()
            while self._q:
                b = self._q.popleft()
                try:
                    proc.stdin.write(b)
                    self._fed += len(b) // 2
                except (BrokenPipeError, ValueError, OSError):
                    return
            try:
                proc.stdin.flush()
            except (BrokenPipeError, ValueError, OSError):
                return

    def _reader(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            with self._lock:
                if d.get("ready"):
                    pass                         # first step done - the set-up comes with the first signal
                elif "id" in d and (self.state == "starting" or d["id"] in self._warm_ids):
                    # the warm-up signal decoded: tables are set up, live audio from now on
                    self._warm_ids.add(d["id"])  # its later updates stay out of the list too
                    if self.state == "starting" and (d.get("term") == 1 or "CQ" in (d.get("text") or "")):
                        self.state = "ready"
                        self._q.clear()
                        log.info("JTTY decoder ready after %.0f s", time.time() - self._started)
                elif "id" in d:
                    # time of the message: when its first part arrived (the
                    # stream has a gap after the set-up, so sample counts
                    # do not give the clock)
                    now = time.time()
                    m = self.msgs.get(d["id"]) or {"id": d["id"], "utc": now - max(0.0, d["latest"] - d["start"])}
                    m.update(hz=round(d["f"], 1), text=d["text"], snr=d["snr"], term=d["term"], t=now)
                    self.msgs[d["id"]] = m
                    if len(self.msgs) > MAX_MSGS:
                        for k in sorted(self.msgs, key=lambda k: self.msgs[k]["t"])[:len(self.msgs) - MAX_MSGS]:
                            del self.msgs[k]
                self.version += 1
        if self._proc is proc and self.enabled:
            code = proc.wait()
            with self._lock:
                self.state, self.error = "error", f"jttyd ended (exit {code})"
                self.version += 1
            log.warning("JTTY: jttyd ended with exit code %s", code)

    # -- control ------------------------------------------------------------------
    def set_enabled(self, on: bool) -> None:
        if on == self.enabled:
            return
        if on:
            if not available():
                self.state, self.error = "error", f"{BIN} not installed (decoder/jttyd)"
                self.version += 1
                return
            self._q.clear()
            self._hist = np.zeros(_N - 1)
            self._phase, self._fed = 0, 0
            self._warm_ids = set()
            # warm-up: the decoder sets up its tables (20-35 s) on the first
            # signal it finds - on a station's call that blocked it, and live
            # audio was lost. So it gets its own test signal first; when that
            # is decoded it is ready, and the live audio follows.
            try:
                w, _ = waveform("CQ CQ TEST", 1500.0, RATE)
                pcm = np.concatenate([np.zeros(RATE), 0.3 * w, np.zeros(3 * RATE)])
                b = (pcm * 32767).astype("<i2").tobytes()
                for i in range(0, len(b), 9600):
                    self._q.append(b[i:i + 9600])
                self._have.set()
            except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
                log.warning("JTTY warm-up signal: %s", exc)
            self._started = time.time()
            # whole passband: centre in the middle, tolerance to both edges
            centre = (LOW_HZ + HIGH_HZ) / 2
            proc = subprocess.Popen([BIN, "--low", str(LOW_HZ), "--high", str(HIGH_HZ),
                                     "--center", str(centre), "--tol", str((HIGH_HZ - LOW_HZ) / 2), "-"],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, bufsize=0)
            self._proc = proc
            self.enabled, self.state, self.error = True, "starting", ""
            threading.Thread(target=self._writer, args=(proc,), name="jtty-in", daemon=True).start()
            threading.Thread(target=self._reader, args=(proc,), name="jtty-out", daemon=True).start()
            log.info("JTTY decoder started")
        else:
            self.enabled, self.state = False, "off"
            proc, self._proc = self._proc, None
            if proc:
                proc.terminate()
                try:
                    proc.wait(3)
                except subprocess.TimeoutExpired:
                    proc.kill()
        self.version += 1

    def clear(self) -> None:
        with self._lock:
            self.msgs.clear()
            self.version += 1

    def snapshot(self) -> dict:
        with self._lock:
            msgs = sorted(self.msgs.values(), key=lambda m: m["utc"])
            return {"enabled": self.enabled, "state": self.state, "error": self.error,
                    "available": available(), "msgs": [dict(m) for m in msgs],
                    "dropped": self.dropped, "bands": BANDS}


def waveform(text: str, hz: float, rate: int = 48_000, my_call: str = "") -> tuple[np.ndarray, dict]:
    """The text as JTTY audio (float, +-1) and jttyd's summary (segments,
    frames, seconds, the canonical text that goes out)."""
    cmd = [BIN, "--tx", text, "--f0", f"{hz:.1f}", "--rate", str(rate)]
    if my_call:
        cmd += ["--my-call", my_call]
    r = subprocess.run(cmd, capture_output=True, timeout=10)
    info = {}
    for line in r.stderr.decode(errors="replace").splitlines():
        try:
            info = json.loads(line)
        except ValueError:
            pass
    if r.returncode != 0 or not info.get("ok"):
        raise RuntimeError(f"JTTY encoder: {info.get('error') or r.returncode}")
    return np.frombuffer(r.stdout, dtype="<i2").astype(np.float64) / 32767.0, info
