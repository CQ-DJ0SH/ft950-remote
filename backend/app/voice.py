"""Browser audio over WebRTC/Opus — TM-V71 Remote's bridge, FT-950 edition.

RX: AudioCapture already owns the codec and delivers 20 ms chunks of 48 kHz
mono — exactly one Opus frame (960 samples). They fan out to one bounded queue
per listening browser; a clock-paced track sends them (see RadioRxTrack).

TX: decoded mic frames from the browser are metered always (VU, graph) but only
queued while the transmitter is open. A writer thread drains the queue into
``aplay`` on the codec, padding with silence, so the radio sees a continuous
stream for as long as PTT is keyed. The queue is capped at the TX buffer: a
backlog never turns into seconds of delay on the air.

Kept from TM-V71 Remote on purpose: the wall-clock paced RX track with a refill
threshold (clicks under load otherwise), Opus forced to mono in the SDP, and
the ctypes libopus encoder (opus_mono.py) that avoids PyAV's measured leak.
"""
from __future__ import annotations

import asyncio
import fractions
import logging
import re
import subprocess
import threading
from collections import deque
from typing import Optional

import numpy as np
from aiortc import MediaStreamTrack
from av import AudioFrame
from av.audio.resampler import AudioResampler

from .audio import CHUNK, RATE, AudioCapture
from .tx_dsp import LOWPASS_HZ, PREEMPH_REF_HZ, PREEMPH_US, Compressor, FIRLowpass, Limiter, PreEmphasis

log = logging.getLogger("ft950.voice")

BLOCK = CHUNK                     # 960 samples = 20 ms = one Opus frame
DEF_RX_BUFFER_MS = 60
DEF_TX_BUFFER_MS = 150
DEF_PTT_TAIL_MS = 250
TONE_1750_HZ = 1750.0
MIC_TEST_MAX_S = 30               # echo test recording cap (TM-V71 Remote)

# Roger beep as TM-V71 Remote plays it: d6 · a5 · e5, 60 ms each, 30 ms apart.
ROGER_NOTES = (1174.7, 880.0, 659.3)
ROGER_TONE_S, ROGER_GAP_S, ROGER_AMP = 0.06, 0.03, 0.28


ECHO_TARGET_DBFS = -16.0          # echo test replay: speech loudness to listen to
ECHO_MAX_GAIN = 60.0              # +35.6 dB at most (a silent recording stays quiet)


def echo_playback(x: np.ndarray) -> tuple[np.ndarray, dict]:
    """The recorded mic test, brought to a comfortable listening level.

    The recording is what would go out — at transmit level, which for the DATA
    jack is often around −30 dBFS and far too quiet to judge by ear. Only the
    volume changes: the speech (20 ms frames above a gate relative to the
    loudest) is raised to ECHO_TARGET_DBFS RMS; peaks above −1 dBFS are caught
    by a limiter instead of capping the gain, so one click or plosive does not
    keep the whole replay quiet. The level meters still show the real TX level.
    Returns the replay and its numbers (logged)."""
    f = x.astype(np.float64)
    if not f.size:
        return x, {}
    n = len(f) // BLOCK * BLOCK
    frames = f[:n].reshape(-1, BLOCK) if n else f.reshape(1, -1)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    speech = rms[rms > max(3.0, 0.1 * rms.max())]            # ignore pauses / room noise
    level = float(np.sqrt(np.mean(speech ** 2))) if speech.size else float(rms.max())
    target = 32768.0 * 10 ** (ECHO_TARGET_DBFS / 20)
    g = min(ECHO_MAX_GAIN, target / max(level, 1e-9))
    lim = Limiter(RATE, ceiling_db=-1.0, release_ms=60.0)
    y = np.concatenate([lim.process(f[i:i + BLOCK] * g) for i in range(0, len(f), BLOCK)])
    db = lambda v: round(20 * np.log10(max(v, 1e-9) / 32768.0), 1)
    return y, {"in_dbfs": db(level), "gain_db": round(20 * np.log10(g), 1),
               "out_dbfs": db(level * g), "limited": lim.hits, "seconds": round(len(f) / RATE, 1)}


def render_roger() -> np.ndarray:
    """The beep as int16 at 48 kHz; 5 ms raised-cosine edges so no note clicks."""
    n, gap, edge = int(ROGER_TONE_S * RATE), int(ROGER_GAP_S * RATE), int(0.005 * RATE)
    env = np.ones(n)
    ramp = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, edge))
    env[:edge], env[-edge:] = ramp, ramp[::-1]
    parts = []
    for i, f in enumerate(ROGER_NOTES):
        parts.append(np.sin(2 * np.pi * f * np.arange(n) / RATE) * env * ROGER_AMP * 32767)
        if i < len(ROGER_NOTES) - 1:
            parts.append(np.zeros(gap))
    return np.concatenate(parts).astype(np.int16)


def force_opus_mono(sdp: str) -> str:
    """stereo=0;sprop-stereo=0 on every Opus fmtp line (TM-V71 Remote): the
    radio audio is mono, and stereo Opus left the right ear with static."""
    sep = "\r\n" if "\r\n" in sdp else "\n"
    lines = sdp.split(sep)
    for pt in re.findall(r"a=rtpmap:(\d+) opus/48000", sdp):
        prefix = f"a=fmtp:{pt} "
        idx = next((i for i, ln in enumerate(lines) if ln.startswith(prefix)), None)
        if idx is None:
            rt = next((i for i, ln in enumerate(lines) if ln.startswith(f"a=rtpmap:{pt} opus")), None)
            if rt is not None:
                lines.insert(rt + 1, prefix + "minptime=10;useinbandfec=1;stereo=0;sprop-stereo=0")
            continue
        params = [p for p in lines[idx][len(prefix):].split(";") if p.strip()]
        kept = [p for p in params if p.split("=", 1)[0].strip() not in ("stereo", "sprop-stereo")]
        lines[idx] = prefix + ";".join(kept + ["stereo=0", "sprop-stereo=0"])
    return sep.join(lines)


def _dbfs(x: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean((x.astype(np.float64) / 32768) ** 2))) if x.size else 0.0
    return round(20 * np.log10(rms), 1) if rms > 1e-6 else -120.0


PREROLL_S = 0.6
SPEECH_RMS = 150.0          # ≈ −47 dBFS: below this a block counts as silence


def _rms(b: np.ndarray) -> float:
    return float(np.sqrt(np.mean(b.astype(np.float32) ** 2))) if b.size else 0.0


def speech_start(blocks: list) -> list:
    """The pre-roll from its first non-silent block on (the browser mic is
    muted until the key press, so everything before that is digital silence)."""
    for i, b in enumerate(blocks):
        if _rms(b) > SPEECH_RMS:
            return blocks[i:]
    return []


class PcmOut:
    """aplay on the codec, fed block by block from a pull function."""

    def __init__(self, device: str, pull) -> None:
        self.device, self.pull = device, pull
        self._stop = threading.Event()
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._proc = subprocess.Popen(
            ["aplay", "-q", "-D", self.device, "-f", "S16_LE", "-r", str(RATE),
             "-c", "2", "-t", "raw", "--buffer-time=60000"],
            stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self._thread = threading.Thread(target=self._run, name="voice-out", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        assert self._proc and self._proc.stdin
        try:
            while not self._stop.is_set():
                x = self.pull()
                self._proc.stdin.write(np.repeat(x[:, None], 2, axis=1).astype("<i2").tobytes())
            fade = (self.pull() * np.linspace(1, 0, BLOCK)).astype(np.int16)
            self._proc.stdin.write(np.repeat(fade[:, None], 2, axis=1).astype("<i2").tobytes())
            self._proc.stdin.close()
            self._proc.wait(timeout=2)
        except Exception as exc:  # noqa: BLE001
            log.warning("voice output failed: %s", exc)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self._proc and self._proc.poll() is None:
            self._proc.kill()


class VoiceHub:
    """RX fan-out to browsers, TX queue from the browser mic, levels."""

    def __init__(self, capture: AudioCapture, device_getter, *, rx_gain: float = 1.0,
                 tx_gain: float = 1.0, rx_buffer_ms: int = DEF_RX_BUFFER_MS,
                 tx_buffer_ms: int = DEF_TX_BUFFER_MS, ptt_tail_ms: int = DEF_PTT_TAIL_MS,
                 roger: bool = False, tx_auto_gain: bool = False,
                 tx_comp: bool = False, tx_preemph: bool = False) -> None:
        self.roger = roger                   # roger beep on PTT release
        # 1750 Hz tone call (FM repeaters), as TM-V71 Remote: armed with the
        # button, it replaces the mic while PTT is held; disarms on release
        self.tone_1750 = False
        self.tone_level = 0.5                # set from the TX drive when armed
        self._t1750_phase = 0.0
        # TX auto gain (as TM-V71 Remote): rides the mic level toward a target and
        # overrides tx_gain while on; agc_gain is the live factor for the UI.
        self.tx_auto_gain = tx_auto_gain
        self.agc_gain = 1.0
        # Voice TX processing (Settings ▸ Audio): compressor and pre-emphasis,
        # with the low-pass and limiter they need (tx_dsp.py).
        self.tx_comp, self.tx_preemph = tx_comp, tx_preemph
        self._comp = Compressor(RATE)
        self._pemph = PreEmphasis(PREEMPH_US, RATE, PREEMPH_REF_HZ)
        self._lp = FIRLowpass(LOWPASS_HZ, RATE)
        self._lim = Limiter(RATE)
        self.device_getter = device_getter
        self.rx_gain, self.tx_gain = rx_gain, tx_gain
        self.rx_buffer_ms, self.tx_buffer_ms, self.ptt_tail_ms = rx_buffer_ms, tx_buffer_ms, ptt_tail_ms
        self.peers = 0                       # connected browsers
        # RX to the browsers, since the service started: gaps (a browser's queue
        # ran dry: a short silence) and drops (it fell behind: oldest discarded)
        self.rx_gaps = self.rx_drops = 0
        # Every browser's mic is metered, but only ONE feeds the transmitter
        # and the echo test: the browser that keyed (``active``). Measured: two
        # connected browsers used to interleave their mics block by block.
        self.mic_levels: dict[str, float] = {}   # peer id -> mic dBFS
        self.active: Optional[str] = None
        self.levels = {"rx": -120.0, "tx": -120.0}
        self._subs: dict[str, deque] = {}   # peer id -> RX block queue
        self._lock = threading.Lock()
        self._tx: deque = deque()
        self._tx_open = False
        self._mic_on = False                 # mic frames accepted into the TX queue
        self._priming = True
        # Pre-roll: the last ~0.6 s of every browser's (gained) mic, so the
        # words spoken between the key press and the radio/codec being ready
        # are sent instead of lost. _grace lets that head start past the TX
        # buffer cap — but only until the next speech pauses: silent mic blocks
        # are dropped while the queue is above the cap, so the over catches up
        # to real time instead of running the whole over (and the release) late
        # by the pre-roll ("the queue never empties", as once in TM-V71 Remote).
        # _hold keeps the codec silent until the radio is keyed (aplay starts
        # in parallel with the CAT command).
        self._pre: dict[str, deque] = {}
        self._grace = 0
        self._hold = False
        self._out: Optional[PcmOut] = None
        self._silence = np.zeros(BLOCK, dtype=np.int16)
        # Mic echo test: while on, the mic is recorded here (RX muted); when it
        # is switched off the recording is played back over the RX track — the
        # whole path browser → Opus → Pi → browser, without keying the radio.
        self.mic_test = False
        self._echo_rec: list[np.ndarray] = []
        self._echo_buf: Optional[np.ndarray] = None
        self._echo_pos = 0
        capture.subscribe(self._on_rx)

    # -- settings -----------------------------------------------------------
    @property
    def mic_tracks(self) -> int:
        return len(self.mic_levels)

    def claim_mic(self, peer: Optional[str]) -> str:
        """Make ``peer``'s microphone the TX source. Without an id the only
        connected mic is taken; with several, the caller must say which."""
        if peer is None:
            if len(self.mic_levels) != 1:
                raise RuntimeError("Several browsers connected with a microphone — reload the page")
            peer = next(iter(self.mic_levels))
        if peer not in self.mic_levels:
            raise RuntimeError("This microphone is no longer connected — reconnect audio")
        self.active = peer
        return peer

    def config(self) -> dict:
        return {"rx_gain": self.rx_gain, "tx_gain": self.tx_gain, "rx_buffer_ms": self.rx_buffer_ms,
                "tx_buffer_ms": self.tx_buffer_ms, "ptt_tail_ms": self.ptt_tail_ms,
                "roger": self.roger, "tx_auto_gain": self.tx_auto_gain,
                "agc_gain": round(self.agc_gain, 2), "tx_comp": self.tx_comp,
                "tx_preemph": self.tx_preemph}

    def rx_depth(self) -> int:
        return max(1, round(self.rx_buffer_ms / 20))

    # -- RX -------------------------------------------------------------------
    def rx_subscribe(self, peer: str = "") -> deque:
        q: deque = deque(maxlen=25)          # 500 ms cap: latency can never grow past it
        with self._lock:
            self._subs[peer] = q
        return q

    def rx_unsubscribe(self, q: deque) -> None:
        with self._lock:
            for k, v in list(self._subs.items()):
                if v is q:
                    del self._subs[k]

    def set_mic_test(self, on: bool) -> None:
        with self._lock:
            if on:
                self._echo_rec, self._echo_buf = [], None
            elif self.mic_test and self._echo_rec:
                self._echo_buf, info = echo_playback(np.concatenate(self._echo_rec))
                self._echo_pos, self._echo_rec = 0, []
                log.info("mic test replay: speech %s dBFS, +%s dB → %s dBFS, %s s, limiter %s×",
                         info.get("in_dbfs"), info.get("gain_db"), info.get("out_dbfs"),
                         info.get("seconds"), info.get("limited"))
            self.mic_test = on

    @property
    def echo_busy(self) -> bool:
        return self._echo_buf is not None

    def _on_rx(self, mono: np.ndarray, utc: float) -> None:
        x = np.clip(mono.astype(np.int32) * self.rx_gain, -32768, 32767).astype(np.int16)
        self.levels["rx"] = _dbfs(x)
        own = None                  # what only the testing browser hears
        with self._lock:
            if self._echo_buf is not None:           # replaying the mic test
                e, p = self._echo_buf, self._echo_pos
                own = e[p:p + BLOCK]
                if len(own) < BLOCK:
                    own = np.concatenate([own, self._silence[:BLOCK - len(own)]])
                    self._echo_buf = None
                else:
                    self._echo_pos = p + BLOCK
            elif self.mic_test:
                own = self._silence                  # its RX muted while recording
            subs = dict(self._subs)
        data = x.tobytes()
        for peer, q in subs.items():
            if len(q) == q.maxlen:                   # full: the append drops the oldest
                self.rx_drops += 1
            # every other listener keeps hearing the radio
            q.append(own.tobytes() if own is not None and peer == self.active else data)

    # -- TX -------------------------------------------------------------------
    def _shape(self, x: np.ndarray) -> np.ndarray:
        """Compressor → pre-emphasis → low-pass → limiter, as switched on."""
        if self.tx_comp:
            x = self._comp.process(x)
        if self.tx_preemph:
            x = self._lp.process(self._pemph.process(x))
        if self.tx_comp or self.tx_preemph:
            x = self._lim.process(x)
        return x

    def _reset_shape(self) -> None:
        for f in (self._comp, self._pemph, self._lp, self._lim):
            f.reset()

    AGC_TARGET, AGC_NOISE, AGC_MAX, AGC_MIN = 5000.0, 180.0, 12.0, 0.3

    def _gain(self, pcm: np.ndarray, live: bool) -> float:
        """Manual tx_gain, or the auto gain. The AGC only learns from audio
        that is actually going out (keyed or mic test): it lowers the gain fast
        (no clipping on loud syllables), raises it slowly, and holds it in
        pauses — below the noise gate nothing is pumped up."""
        if not self.tx_auto_gain:
            return self.tx_gain
        if live and pcm.size:
            x = pcm.astype(np.float32)
            rms = float(np.sqrt(np.mean(x * x)))
            if rms > self.AGC_NOISE:
                want = min(self.AGC_MAX, max(self.AGC_MIN, self.AGC_TARGET / rms))
                a = 0.5 if want < self.agc_gain else 0.04       # fast down, slow up
                self.agc_gain += (want - self.agc_gain) * a
        return self.agc_gain

    def push_tx(self, pcm: np.ndarray, src: str = "") -> None:
        """Every decoded mic frame: metered always; only the active browser's
        frames are recorded (echo test) or queued (transmit)."""
        live = src == self.active and (self.mic_test or (self._tx_open and self._mic_on))
        x = np.clip(pcm.astype(np.float32) * self._gain(pcm, live), -32768, 32767).astype(np.int16)
        if live:
            x = self._shape(x)
        self.mic_levels[src] = _dbfs(x)
        pre = self._pre.setdefault(src, deque())
        pre.append(x)
        while len(pre) > 1 and sum(len(b) for b in pre) > PREROLL_S * RATE:
            pre.popleft()
        if src != self.active:
            return
        self.levels["tx"] = self.mic_levels[src]
        if self.mic_test:
            with self._lock:
                if sum(len(b) for b in self._echo_rec) < MIC_TEST_MAX_S * RATE:
                    self._echo_rec.append(x)
            return
        if not (self._tx_open and self._mic_on):
            return
        with self._lock:
            cap = max(1, round(self.tx_buffer_ms / 20)) * BLOCK
            if (self._grace and not self._hold and _rms(x) < SPEECH_RMS
                    and sum(len(b) for b in self._tx) + len(x) > cap):
                # catch up in a pause: skip this silent block, shrink the head start
                self._grace = max(0, self._grace - len(x))
                return
            self._tx.append(x)
            while sum(len(b) for b in self._tx) > cap + self._grace:
                self._tx.popleft()           # drop the oldest: never seconds of delay
            # the head start only ever shrinks: never more than the current excess
            self._grace = min(self._grace, max(0, sum(len(b) for b in self._tx) - cap))

    def _pull(self) -> np.ndarray:
        """One 20 ms block for the codec. Like the RX track it pre-fills: mic
        frames arrive with network jitter, so playback waits for half the TX
        buffer (≤ 60 ms) and re-fills after running dry, instead of chopping."""
        with self._lock:
            if self._hold:
                return self._silence
            if self.tone_1750 and self._mic_on:
                self._tx.clear()                     # the tone replaces the mic
                step = 2 * np.pi * TONE_1750_HZ / RATE
                ph = self._t1750_phase + step * np.arange(BLOCK)
                self._t1750_phase = float((ph[-1] + step) % (2 * np.pi))
                return (np.sin(ph) * 0.98 * self.tone_level * 32767).astype(np.int16)
            have = sum(len(b) for b in self._tx)
            prime = min(self.tx_buffer_ms / 2, 60) / 1000 * RATE
            if self._priming and have < prime and self._mic_on:
                return self._silence            # (after release: nothing more comes — play out)
            self._priming = have == 0
            buf = np.concatenate(list(self._tx)) if self._tx else np.zeros(0, np.int16)
            self._tx.clear()
            if len(buf) > BLOCK:
                self._tx.append(buf[BLOCK:])
        return buf[:BLOCK] if len(buf) >= BLOCK else np.concatenate([buf, self._silence[:BLOCK - len(buf)]])

    def trim_tail(self) -> int:
        """After release: drop the silence at the end of the queue (the breath
        before the button is let go), so it does not hold the key open.
        Returns the ms removed."""
        with self._lock:
            cut = 0
            while self._tx and _rms(self._tx[-1]) < SPEECH_RMS:
                cut += len(self._tx.pop())
            return round(cut / RATE * 1000)

    def tx_backlog_ms(self) -> int:
        with self._lock:
            return round(sum(len(b) for b in self._tx) / RATE * 1000)

    def open_tx(self, hold: bool = False) -> None:
        """Start the codec output, seeded with the active mic's speech since
        the key press. ``hold``: play silence until :meth:`release_tx`."""
        with self._lock:
            self._tx.clear()
            self._priming = True
            self._hold = hold
            seed = speech_start(list(self._pre.get(self.active or "", ())))
            self._tx.extend(seed)
            self._grace = sum(len(b) for b in seed)
            self._reset_shape()                     # a fresh over: no filter state from the last one
        self._tx_open = self._mic_on = True
        self._out = PcmOut(self.device_getter(), self._pull)
        self._out.start()

    def release_tx(self) -> None:
        """The radio is keyed: let the queued speech play."""
        self._hold = False

    def mic_off(self) -> None:
        """Release: no more mic into the queue (what is queued still plays).
        The 1750 Hz tone call is one-shot: it disarms here."""
        self._mic_on = False
        self.tone_1750 = False

    def play_roger(self) -> float:
        """Queue the roger beep behind the speech; returns its duration in s.
        Bypasses the TX buffer cap and the pre-fill: it must play whole."""
        beep = render_roger()
        with self._lock:
            self._tx.append(beep)
            self._priming = False
        return len(beep) / RATE

    def close_tx(self) -> None:
        self._tx_open = self._mic_on = False
        self._hold = False
        self._grace = 0
        if self._out:
            self._out.stop()
            self._out = None


class RadioRxTrack(MediaStreamTrack):
    """Radio RX → browser, paced to the wall clock (TM-V71 Remote): frame N is
    due at start + N·20 ms. A queue between the capture clock and this one
    absorbs scheduling jitter; when it runs dry it refills to the RX buffer
    depth first — a short gap instead of continuous crackle."""
    kind = "audio"

    def __init__(self, hub: VoiceHub, peer: str = "") -> None:
        super().__init__()
        self._hub, self._pts, self._start = hub, 0, None
        self._q = hub.rx_subscribe(peer)
        self._fill = True
        self._silence = np.zeros((1, BLOCK), dtype=np.int16)

    async def recv(self) -> AudioFrame:
        loop = asyncio.get_event_loop()
        if self._start is None:
            self._start = loop.time()
        else:
            await asyncio.sleep(max(0.0, self._start + (self._pts + BLOCK) / RATE - loop.time()))
        data = self._next()
        arr = self._silence if data is None else np.frombuffer(data, dtype=np.int16).reshape(1, -1)
        frame = AudioFrame.from_ndarray(arr, format="s16", layout="mono")
        frame.sample_rate, frame.pts = RATE, self._pts
        frame.time_base = fractions.Fraction(1, RATE)
        self._pts += BLOCK
        return frame

    def _next(self) -> Optional[bytes]:
        q = self._q
        if self._fill:
            if len(q) < self._hub.rx_depth():
                return None
            self._fill = False
        if not q:
            self._fill = True
            self._hub.rx_gaps += 1                   # ran dry: an audible gap follows
            return None
        return q.popleft()

    def stop(self) -> None:
        self._hub.rx_unsubscribe(self._q)
        super().stop()


def _mic_pcm(frame) -> Optional[np.ndarray]:
    """Decoded mic frame → mono int16 by a numpy downmix (PyAV's resampler
    leaks, measured in TM-V71 Remote); None if a real resample is needed."""
    if frame.format.name != "s16" or frame.sample_rate != RATE:
        return None
    a = frame.to_ndarray()
    ch = a.size // max(1, frame.samples)
    if ch <= 1:
        return a.reshape(-1).astype(np.int16, copy=False)
    x = a.reshape(-1, ch) if a.shape[0] == 1 else a.T
    return np.clip(x.mean(axis=1), -32768, 32767).astype(np.int16)


async def consume_mic(track: MediaStreamTrack, hub: VoiceHub, src: str) -> None:
    resampler = None
    hub.mic_levels[src] = -120.0
    try:
        while True:
            frame = await track.recv()
            pcm = _mic_pcm(frame)
            if pcm is not None:
                hub.push_tx(pcm, src)
                continue
            if resampler is None:
                resampler = AudioResampler(format="s16", layout="mono", rate=RATE)
            for f in resampler.resample(frame):
                hub.push_tx(f.to_ndarray().reshape(-1).astype(np.int16), src)
    except Exception:  # noqa: BLE001  (track ended)
        pass
    finally:
        hub.mic_levels.pop(src, None)
        hub._pre.pop(src, None)
        if hub.active == src:
            hub.active = None
            hub.levels["tx"] = -120.0
