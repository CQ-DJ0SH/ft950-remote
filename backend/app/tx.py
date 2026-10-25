"""Transmit path: tone generator into the SCU-17 codec and CAT PTT with a watchdog.

Two users so far: TUNE — a steady tone at the TX offset, for the antenna tuner
and for setting drive/ALC (as iDigi's TUNE) — and voice PTT from the browser
microphone (voice.VoiceHub). The FT8/FT4 sender will reuse the same handling.

Safety rules (TM-V71 Remote's PTT lessons, plus TX-specific ones):
* PTT only with the radio connected and in a mode that takes audio from the
  DATA jack (DATA-U/DATA-L, or USB);
* order on: PTT → 60 ms → tone fades in; order off: tone fades out → PTT off,
  so no key click and no unmodulated carrier spike;
* a backend watchdog ends every transmission after ``max_s`` even if the
  browser is gone; the radio's own TX time-out (menu 031) is the last net;
* any error on the way switches PTT off;
* voice PTT needs a connected browser microphone (no unmodulated carrier) and
  a lease the browser renews every second — 2.5 s without renewal (Wi-Fi gone,
  tab closed) releases it; 3 minutes is the hard limit per over.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import threading
import time
from typing import Optional

import numpy as np

from . import ftgen
from .audio import RATE

log = logging.getLogger("ft950.tx")

TX_MODES = ("DATA-U", "DATA-L", "USB")            # FT8/FT4 and TUNE
# voice PTT: every phone mode — FM for the repeaters (M13/M14, 1750 Hz call) too
VOICE_TX_MODES = ("LSB", "USB", "AM", "FM", "FM-N", "PKT-FM", "DATA-U", "DATA-L")
TONE_MODES = ("FM", "FM-N", "PKT-FM")      # 1750 Hz tone call: FM repeaters only
LEASE_S = 2.5                # voice PTT: browser must renew within this
RELEASE_MAX_S = 0.8          # key-up: wait at most this long for queued speech + roger
# FT8/FT4 audio frequency range the radio actually transmits: menu 064 SSB TX BPF
# is 100–2900 Hz here, and a signal at 2900 Hz (AF adopting a caller up there)
# went out with no power at all. f0 + bandwidth (50 / 83 Hz) stays ~100 Hz
# inside the filter edges.
TX_AUDIO_MIN_HZ, TX_AUDIO_MAX_HZ = 200.0, 2700.0


def clamp_tx_hz(hz: float) -> float:
    return min(TX_AUDIO_MAX_HZ, max(TX_AUDIO_MIN_HZ, float(hz)))
# FT8/FT4: the nominal start is 0.5 s into the period (decoder DT = 0). A late
# start still fits the period (FT8 12.64 s, FT4 5.04 s) and decodes with a
# positive DT, up to this much after the period began; later → skip the period.
FT_LATEST_START = {"FT8": 2.0, "FT4": 1.5}
PTT_LEAD_S = 0.15            # key before the audio starts (T/R relay settles)
VOICE_MAX_S = 180.0          # longest over
FADE_S = 0.010
BLOCK = RATE // 50                                   # 20 ms


class ToneOut:
    """Continuous sine on the codec via aplay; phase-continuous, faded."""

    def __init__(self, device: str, hz: float, level: float) -> None:
        self.device, self.hz, self.level = device, hz, max(0.0, min(1.0, level))
        self._stop = threading.Event()
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self.error: Optional[str] = None

    def start(self) -> None:
        self._proc = subprocess.Popen(
            ["aplay", "-q", "-D", self.device, "-f", "S16_LE", "-r", str(RATE),
             "-c", "2", "-t", "raw", "--buffer-time=60000"],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        self._thread = threading.Thread(target=self._run, name="tone-out", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        assert self._proc and self._proc.stdin
        phase, n_fade = 0.0, int(FADE_S * RATE)
        step = 2 * np.pi * self.hz / RATE
        amp = 0.98 * self.level * 32767                 # ≈ 0 dBFS at 100 %: a single steady tone cannot clip
        k = np.arange(BLOCK)
        gain_in = np.ones(BLOCK)
        gain_in[:n_fade] = np.linspace(0, 1, n_fade)
        first = True
        try:
            while True:
                stopping = self._stop.is_set()
                x = np.sin(phase + step * k) * amp
                phase = (phase + step * BLOCK) % (2 * np.pi)
                if first:
                    x *= gain_in
                    first = False
                if stopping:
                    x *= np.linspace(1, 0, BLOCK)       # fade out over one block
                s = np.repeat(x.astype("<i2")[:, None], 2, axis=1)
                self._proc.stdin.write(s.tobytes())
                if stopping:
                    break
            self._proc.stdin.close()
            self._proc.wait(timeout=2)
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            log.warning("tone output failed: %s", exc)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self._proc and self._proc.poll() is None:
            self._proc.kill()


class BufferOut:
    """Play one prepared buffer on the codec (aplay paces it) and signal the end."""

    def __init__(self, device: str, samples: np.ndarray) -> None:
        self.device, self.samples = device, samples
        self.done = threading.Event()
        self._proc: Optional[subprocess.Popen] = None

    def start(self) -> None:
        self._proc = subprocess.Popen(
            ["aplay", "-q", "-D", self.device, "-f", "S16_LE", "-r", str(RATE), "-c", "2",
             "-t", "raw", "--buffer-time=60000"], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        threading.Thread(target=self._run, name="ft-out", daemon=True).start()

    def _run(self) -> None:
        try:
            st = np.repeat(self.samples.astype("<i2")[:, None], 2, axis=1).tobytes()
            for i in range(0, len(st), BLOCK * 4):
                self._proc.stdin.write(st[i:i + BLOCK * 4])
            self._proc.stdin.close()
            self._proc.wait(timeout=5)
        except Exception as exc:  # noqa: BLE001
            log.warning("FT output: %s", exc)
        finally:
            self.done.set()

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.kill()
        self.done.set()


class Transmitter:
    def __init__(self, service, device_getter, level: float = 0.5,
                 max_s: float = 10.0, hub=None) -> None:
        self.hub = hub                      # voice.VoiceHub (browser mic)
        self.lease_until = 0.0
        self.service = service              # RadioService (status + driver)
        self.device_getter = device_getter
        self.level = level                  # 0 … 1, software drive into the codec
        self.max_s = max_s
        self.state: dict = {"active": None, "hz": None, "since": None, "until": None}
        self._tone: Optional[ToneOut] = None
        self._watchdog: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    def status(self) -> dict:
        s = dict(self.state)
        if s["until"]:
            s["remaining_s"] = max(0.0, round(s["until"] - time.time(), 1))
        return {**s, "level": round(self.level * 100), "max_s": self.max_s}

    async def ptt(self, on: bool, peer: Optional[str] = None) -> dict:
        """Voice PTT from browser ``peer``. ``on`` again while keyed renews
        the lease (keep-alive)."""
        if not on:
            if self.state["active"] == "voice" and self.hub:
                # frames still in flight over WebRTC arrive during the tail …
                await asyncio.sleep(self.hub.ptt_tail_ms / 1000)
                self.hub.mic_off()          # … then the over is complete:
                self.hub.trim_tail()        # no trailing silence on the air,
                if self.hub.roger:          # the roger beep behind the speech,
                    self.hub.play_roger()
                # and the key drops once the queue has played out (bounded:
                # a stalled codec must not hold the transmitter open)
                limit = time.monotonic() + RELEASE_MAX_S
                while self.hub.tx_backlog_ms() > 0 and time.monotonic() < limit:
                    await asyncio.sleep(0.02)
                await asyncio.sleep(0.06)   # aplay's own 60 ms buffer
            return await self.stop()
        async with self._lock:
            if self.state["active"] == "voice":
                self.lease_until = time.time() + LEASE_S
                return self.status()
            if self.state["active"]:
                await self._stop_locked()
            self._check(VOICE_TX_MODES)
            if not self.hub or self.hub.mic_tracks == 0:
                raise RuntimeError("Connect audio first — no PTT without a microphone")
            if self.hub.mic_test:
                raise RuntimeError("Mic test running — switch MIC TEST off first")
            self.hub.claim_mic(peer)        # only this browser's mic goes on air
            # codec output starts while the CAT command keys the radio (aplay
            # takes a while to spawn on the Pi); it stays silent until keyed
            self.hub.open_tx(hold=True)
            try:
                await asyncio.to_thread(self.service.radio.ptt, True)
            except Exception:
                self.hub.close_tx()
                raise
            self.hub.release_tx()
            now = time.time()
            self.lease_until = now + LEASE_S
            self.state = {"active": "voice", "hz": None, "since": now, "until": now + VOICE_MAX_S}
            self._watchdog = asyncio.create_task(self._voice_guard())
            log.info("PTT on (voice)")
            return self.status()

    async def send_ft(self, message: str, mode: str, f0: float, t0: float,
                      on_air=None) -> dict:
        """Transmit one FT8/FT4 message in the period starting at ``t0``.

        Returns when the transmission is over (or was skipped/halted). The
        tones are generated before keying, so a refused message never keys.
        ``on_air(start_offset)`` is called once the radio is keyed, so the
        message can be shown while it is being sent, not 13 s later."""
        latest = t0 + FT_LATEST_START[mode]
        if clamp_tx_hz(f0) != f0:           # last line of defence: never outside the TX filter
            log.warning("%s TX at %.0f Hz is outside the radio's TX filter → %.0f Hz",
                        mode, f0, clamp_tx_hz(f0))
            f0 = clamp_tx_hz(f0)
        symbols = await asyncio.to_thread(ftgen.tones, message, mode)
        wave = await asyncio.to_thread(ftgen.waveform, symbols, mode, f0)
        async with self._lock:
            if self.state["active"]:
                await self._stop_locked()
            self._check()
            now = time.time()
            start = max(t0 + ftgen.START_S, now + PTT_LEAD_S + 0.1)
            if start > latest:
                log.warning("%s TX %r skipped: too late (%.2f s into the period, latest %.1f s)",
                            mode, message, now - t0, FT_LATEST_START[mode])
                return {"sent": False, "why": "too late for this cycle"}
            await asyncio.sleep(max(0.0, start - PTT_LEAD_S - time.time()))
            await asyncio.to_thread(self.service.radio.ptt, True)
            # aplay starts playing ~immediately; pad with silence up to the start
            pad = max(0.0, start - time.time() - 0.03)
            amp = 0.98 * self.level * 32767           # same drive as TUNE: TUNE calibrates FT
            samples = np.concatenate([np.zeros(int(pad * RATE)), wave * amp]).astype(np.int16)
            out = BufferOut(self.device_getter(), samples)
            out.start()
            self._tone = out                          # stop() kills it like a tone
            dur = ftgen.duration(mode)
            self.state = {"active": "ft", "hz": f0, "since": start, "until": start + dur,
                          "message": message, "mode": mode}
            self._watchdog = asyncio.create_task(self._expire(dur + pad + 2.0))
            log.info("%s TX %r at %.0f Hz, start %+.2f s", mode, message, f0, start - t0)
        if on_air:
            try:
                await on_air(round(start - t0, 2))
            except Exception as exc:  # noqa: BLE001 — display only, never the TX
                log.warning("on_air callback failed: %s", exc)
        await asyncio.to_thread(out.done.wait, dur + pad + 3)
        await self.stop()
        return {"sent": True, "start_offset": round(start - t0, 2)}

    async def _voice_guard(self) -> None:
        while True:
            await asyncio.sleep(0.25)
            now = time.time()
            why = ("lease expired (browser silent)" if now > self.lease_until else
                   f"{VOICE_MAX_S:.0f} s limit" if now > self.state["until"] else
                   "microphone gone" if self.hub and self.hub.mic_tracks == 0 else None)
            if why:
                log.warning("PTT released by watchdog: %s", why)
                await self.stop()
                return

    def _check(self, modes: tuple = TX_MODES) -> None:
        st = self.service.status
        if not st.get("connected"):
            raise RuntimeError("Radio not connected")
        mode = (st.get("vfo") or {}).get("mode")
        if mode not in modes:
            raise RuntimeError(f"Transmit only in {', '.join(modes)} — radio is in {mode}")

    async def tune(self, hz: float, seconds: Optional[float] = None) -> dict:
        async with self._lock:
            if self.state["active"]:
                await self._stop_locked()
            self._check()
            dur = min(self.max_s, seconds or self.max_s)
            hz = max(200.0, min(2900.0, float(hz)))
            tone = ToneOut(self.device_getter(), hz, self.level)
            try:
                await asyncio.to_thread(self.service.radio.ptt, True)
                await asyncio.sleep(0.06)
                tone.start()
            except Exception:
                tone.stop()
                await asyncio.to_thread(self._ptt_off_safely)
                raise
            self._tone = tone
            now = time.time()
            self.state = {"active": "tune", "hz": hz, "since": now, "until": now + dur}
            self._watchdog = asyncio.create_task(self._expire(dur))
            log.info("TUNE on at %.0f Hz, level %d %%, max %.0f s", hz, self.level * 100, dur)
            return self.status()

    async def _expire(self, dur: float) -> None:
        await asyncio.sleep(dur)
        log.info("TUNE watchdog: %.0f s reached", dur)
        await self.stop()

    async def stop(self) -> dict:
        async with self._lock:
            await self._stop_locked()
            return self.status()

    async def _stop_locked(self) -> None:
        if self._watchdog and self._watchdog is not asyncio.current_task():
            self._watchdog.cancel()
        self._watchdog = None
        if self._tone:
            await asyncio.to_thread(self._tone.stop)      # fade out first …
            self._tone = None
        if self.state["active"] == "voice" and self.hub:
            await asyncio.to_thread(self.hub.close_tx)    # … voice too …
        await asyncio.to_thread(self._ptt_off_safely)     # … then unkey
        if self.state["active"]:
            log.info("TX off")
        self.state = {"active": None, "hz": None, "since": None, "until": None}

    def _ptt_off_safely(self) -> None:
        for _ in range(3):                  # the one call that must not be lost
            try:
                self.service.radio.ptt(False)
                return
            except Exception as exc:  # noqa: BLE001
                log.error("PTT off failed, retrying: %s", exc)
                time.sleep(0.2)
