"""FT8/FT4 decoding: UTC-aligned slices from AudioCapture, decoded by jt9.

jt9 (WSJT-X 2.7) is the decoder — its multi-pass subtraction and a-priori
decoding are what "very good decoding" means. Notes from jt9 --help and
first runs on the radio:

* default depth is 1 — always pass ``-d 3``;
* ``-c``/``-G`` (own call/grid) enable AP decoding of replies to us;
* jt9 reads 12 kHz mono 16-bit WAV named ``YYMMDD_HHMMSS.wav`` (the time
  column of its output comes from the name);
* first call per data dir pays ~1.5 s FFTW planning, later ~0.75 s (FT4).

Each cycle: wait until the period is fully captured, cut it with a little
context, resample to 12 kHz, write the WAV, decode in a subprocess while the
next period is already being captured, broadcast the results.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import math
import os
import re
import subprocess
import time
import wave
from collections import deque
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .audio import RATE, AudioCapture, apply_gain, to_12k
from .dxcc import Dxcc
from .state import ConnectionManager

log = logging.getLogger("ft950.digi")

MODES = {
    "FT8": {"period": 15.0, "flag": "-8", "tr": "15"},
    "FT4": {"period": 7.5, "flag": "-5", "tr": "7"},
}
# Dial frequencies (Hz) that select FT4 in AUTO mode; everything else is FT8.
FT4_DIALS = [3_575_000, 7_047_500, 10_140_000, 14_080_000, 18_104_000,
             21_140_000, 24_919_000, 28_180_000, 50_318_000]
# Measured on the Pi 4 (FT8, ~34 signals): d1 3.8 s, d2 8.1 s, d3 14.9 s →
# going one level deeper costs about this factor.
DEPTH_COST = {1: 3.5, 2: 1.8}
# Decodes should be on screen a few seconds after the period, not 12 s later:
# the adaptive depth keeps a run under this (the hard limit stays one period).
LATENCY_BUDGET = {"FT8": 4.0, "FT4": 2.5}
# jt9 is single-threaded: the passband is split into overlapping parts that
# decode in parallel (one core stays free for audio and the web server).
PARTS = max(1, min(3, (os.cpu_count() or 1) - 1))
PART_OVERLAP_HZ = 60           # a 50 Hz FT8 signal on a seam is seen by both parts


def split_band(lo: int, hi: int, parts: int = PARTS,
               last_df: Optional[list[int]] = None) -> list[tuple[int, int]]:
    """``parts`` sub-ranges of lo..hi that overlap by PART_OVERLAP_HZ.

    jt9's run time follows the number of signals, not the bandwidth (measured:
    a 100 Hz strip 0.4 s, the busy middle third 3.5 s of a 6.6 s full pass),
    so the seams are put at the quantiles of the previous period's decodes:
    each part gets about the same number of stations. Without enough
    history the band is cut into equal widths."""
    if parts <= 1 or hi - lo < 600:
        return [(lo, hi)]
    dfs = sorted(f for f in (last_df or []) if lo <= f <= hi)
    if len(dfs) >= 3 * parts:
        cuts = [dfs[len(dfs) * i // parts] for i in range(1, parts)]
    else:
        cuts = [round(lo + i * (hi - lo) / parts) for i in range(1, parts)]
    edges = [lo, *cuts, hi]
    return [(max(lo, edges[i] - PART_OVERLAP_HZ // 2), min(hi, edges[i + 1] + PART_OVERLAP_HZ // 2))
            for i in range(parts) if edges[i + 1] > edges[i]]


def dedupe(decodes: list[dict]) -> list[dict]:
    """One row per message: a signal decoded by two overlapping parts (same
    text, within 20 Hz) is kept once, with the better SNR."""
    out: list[dict] = []
    for d in sorted(decodes, key=lambda d: -d.get("snr", -99)):
        if not any(o["msg"] == d["msg"] and abs(o["df"] - d["df"]) <= 20 for o in out):
            out.append(d)
    return sorted(out, key=lambda d: d["df"])
CONTEXT = 0.25          # seconds of audio on each side, cropped after resampling
SLICE_WAIT = 1.5        # wait this long for a slice the capture has not finished yet
# Early pass while a QSO runs: depth 1 on the audio up to just after the
# nominal end of a signal (start 0.5 s + 12.64 s / 5.04 s). Measured on the
# Pi 4, busy 20 m band: full band 2.0-3.1 s (≈80 % of the decodes), ±150 Hz
# around the partner 0.4-0.5 s — in time to answer in the next period.
EARLY_AT = {"FT8": 13.4, "FT4": 5.74}
DIGI_MODES = ("DATA-U", "USB")

# "140030   5  0.1  722 +  9A2VX EW2MS KO34"  (FT8 uses "~", FT4 "+")
_LINE = re.compile(r"^(\d{6})\s+(-?\d+)\s+(-?\d+\.\d)\s+(\d+)\s+([~+])\s+(.*?)\s*$")
_AP = re.compile(r"\s+(a\d|\?)$")


def parse_jt9(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        msg, marks = m.group(6), []
        # jt9 can append two markers to an a-priori decode: "? a2"
        while (a := _AP.search(msg)):
            marks.insert(0, a.group(1))
            msg = msg[:a.start()].rstrip()
        ap = " ".join(marks) or None
        out.append({"utc": m.group(1), "snr": int(m.group(2)),
                    "dt": float(m.group(3)), "df": int(m.group(4)),
                    "msg": msg, "ap": ap})
    return out


_CALL = re.compile(r"^<?(?=[A-Z0-9/]*\d)(?=[A-Z0-9/]*[A-Z])[A-Z0-9/]{3,}>?$")
_GRID = re.compile(r"^[A-R]{2}\d{2}$")
_REPORT = re.compile(r"^R?[+-]\d{2}$")


def analyse(msg: str) -> dict:
    """Who calls whom, and which QSO step this is.

    ``CQ [DX|POTA|…] CALL GRID`` → CQ; ``TO FROM X`` with X a grid, report,
    R-report, RRR, RR73 or 73; anything else is free text.
    """
    tok = msg.upper().split()
    if not tok:
        return {"kind": "text", "from": None, "to": None}
    if tok[0] == "CQ":
        rest = tok[1:]
        if len(rest) >= 2 and not _CALL.match(rest[0]):
            rest = rest[1:]                      # directed CQ: DX, POTA, EU …
        frm = rest[0].strip("<>") if rest and _CALL.match(rest[0]) else None
        return {"kind": "CQ", "from": frm, "to": None,
                "grid": rest[1] if len(rest) > 1 and _GRID.match(rest[1]) else None}
    if len(tok) >= 2 and _CALL.match(tok[0]) and _CALL.match(tok[1]):
        last = tok[2] if len(tok) > 2 else ""
        # RR73 is also a valid grid square (Arctic Ocean) — WSJT-X gives the
        # QSO meaning precedence, so test the sign-offs first.
        kind = (last if last in ("RRR", "RR73", "73") else
                "grid" if _GRID.match(last) else
                "report" if _REPORT.match(last) and not last.startswith("R") else
                "rreport" if _REPORT.match(last) else
                "call" if not last else "text")
        return {"kind": kind, "to": tok[0].strip("<>"), "from": tok[1].strip("<>"),
                "grid": last if kind == "grid" else None}
    return {"kind": "text", "from": None, "to": None}


_sync_cache = (0.0, False)


def clock_synced() -> bool:
    """Kernel NTP sync state (timedatectl reads it for timesyncd and chrony).

    The Pi has no RTC: after boot the clock runs from a saved timestamp until
    NTP steps it (measured: 30 s off for the first ~45 s). Slices cut before
    that are misaligned, so decoding waits. Cached for 5 s.
    """
    global _sync_cache
    now = time.monotonic()
    if now - _sync_cache[0] < 5:
        return _sync_cache[1]
    try:
        out = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized",
                              "--value"], capture_output=True, text=True,
                             timeout=2).stdout.strip()
        ok = out == "yes"
    except (OSError, subprocess.SubprocessError):
        ok = True                       # cannot tell — do not block decoding
    _sync_cache = (now, ok)
    return ok


def auto_mode(dial_hz: Optional[int]) -> str:
    if dial_hz and any(abs(dial_hz - f) <= 3_000 for f in FT4_DIALS):
        return "FT4"
    return "FT8"


# Decoders to choose from in Settings ▸ FT8. A fixed list - the browser picks an
# id, never a path. "jt9" is WSJT-X's own (Debian package, from PATH); the others
# are local builds and are offered only where they are installed.
DECODERS = [
    {"id": "jt9", "path": "jt9", "tag": "2.7",
     "label": "jt9 · WSJT-X 2.7 (Debian package)"},
    {"id": "jt9-fast", "path": "/usr/local/bin/jt9-fast", "tag": "2.7 fast",
     "label": "jt9-fast · WSJT-X 2.7 built for the Pi 5, Rust OSD — same decodes, about twice as fast"},
    # MTft8 ("mt"): FT8's full pass is one process with threads over the whole
    # band instead of the band split over PARTS processes; it ignores the depth.
    {"id": "jt9-33mt", "path": "/usr/local/bin/jt9-33", "mt": True, "tag": "3.3 MT",
     "label": "jt9-33 · WSJT-X 3.3 beta, multithreaded MTft8 + Rust OSD — about 6 % more decodes, ~2.4 s"},
]


def decoder_available(d: dict) -> bool:
    p = d["path"]
    return "/" not in p or (os.path.isfile(p) and os.access(p, os.X_OK))


def decoder_config(decoder_id: str) -> dict:
    """jt9 path and threading for a decoder id, as DigiService.config keys;
    plain "jt9" if the id is unknown or its build is missing (a missing local
    build must not stop decoding)."""
    d = next((x for x in DECODERS if x["id"] == decoder_id), None)
    if d and decoder_available(d):
        return {"jt9": d["path"], "jt9_mt": bool(d.get("mt"))}
    if decoder_id and decoder_id != "jt9":
        log.warning("Decoder %s not available, using jt9", decoder_id)
    return {"jt9": "jt9", "jt9_mt": False}


def decoder_tag(config: dict) -> str:
    """Short name of the decoder that actually runs (after any fallback),
    for the DIGI panel's WSJT-X plate."""
    d = next((x for x in DECODERS if x["path"] == config.get("jt9")
              and bool(x.get("mt")) == bool(config.get("jt9_mt"))), DECODERS[0])
    return d["tag"]


def digi_jt9_path(decoder_id: str) -> str:
    return decoder_config(decoder_id)["jt9"]


class DigiService:
    def __init__(self, capture: AudioCapture, manager: ConnectionManager,
                 radio_status: Callable[[], dict], work_dir: str,
                 keep_slices: int = 20, data_dir: Optional[str] = None) -> None:
        self.capture = capture
        self.manager = manager
        self.radio_status = radio_status
        self.work = Path(work_dir)
        self.data = Path(data_dir) if data_dir else self.work
        self.keep_slices = keep_slices
        self._last_df: dict[str, list[int]] = {}   # previous period's offsets: where to split
        self.dxcc = Dxcc()
        # (call, dial_hz, entity) -> worked-before flags; set by main.py once
        # the logbook exists
        self.worked_lookup: Optional[Callable[[str, Optional[int], Optional[str]], dict]] = None
        # Sequencer hooks (set by main.py): who gets decodes, whether an early
        # pass is wanted (None = no, else (low_hz, high_hz)), own TX periods.
        self.on_decodes = None          # async (decodes, t0, mode, early, partner=False) -> None
        self.early_range: Callable[[], Optional[tuple[int, int]]] = lambda: None
        self.tx_periods: set[float] = set()
        # Depth adapts per mode (see _adapt). Start values are measured on the
        # Pi 4 with a crowded 20 m band: FT8 depth 3 takes 13-15 s (34
        # decodes) and misses the period, depth 2 6-8 s (33-34); FT4 depth 3
        # takes ~1 s. Starting FT8 at 3 cost the first 30 s of decodes.
        self.depth = {"FT8": 2, "FT4": 3}
        self._fits = {m: 0 for m in MODES}     # consecutive "deeper would fit"
        # Fixed depth 2 by default (switchable in the FT8 panel: 1/2/3/AUTO).
        self.config = {"enabled": True, "mode": "AUTO", "depth": 2,
                       "low_hz": 200, "high_hz": 3000,
                       "my_call": "", "my_grid": "", "jt9": "jt9", "jt9_mt": False}
        self.decodes: deque[dict] = deque(maxlen=500)
        self.cycles: deque[dict] = deque(maxlen=40)    # per-cycle stats
        self._busy = False
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        for m in MODES:
            (self.work / m).mkdir(parents=True, exist_ok=True)
            (self.data / m).mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await asyncio.gather(self._task, return_exceptions=True)

    def current_mode(self) -> str:
        if self.config["mode"] in MODES:
            return self.config["mode"]
        vfo = (self.radio_status() or {}).get("vfo") or {}
        return auto_mode(vfo.get("freq_hz"))

    def status(self) -> dict:
        last = self.cycles[-1] if self.cycles else None
        return {"config": self.config, "mode": self.current_mode(),
                "decoder": decoder_tag(self.config),       # the WSJT-X plate: "2.7 fast" …
                "depth": self.depth, "dxcc_entities": len(self.dxcc),
                "clock_synced": clock_synced(),
                "capture": {"running": self.capture.running,
                            "error": self.capture.error,
                            **self.capture.digi_level(), "gain_db": self.capture.gain_db},
                "last_cycle": last, "cycles": list(self.cycles)}

    # -- main loop ---------------------------------------------------------
    async def _loop(self) -> None:
        while not self._stop.is_set():
            mode = self.current_mode()
            period = MODES[mode]["period"]
            now = time.time()
            t0 = math.floor(now / period) * period      # current period start
            due = t0 + period + CONTEXT + 0.1           # fully captured
            early_at = t0 + EARLY_AT[mode]
            if now < early_at and self.early_range() is not None:
                try:
                    await asyncio.wait_for(self._stop.wait(), early_at - now)
                    return
                except asyncio.TimeoutError:
                    pass
                rng = self.early_range()
                if rng is not None and self.config["enabled"] and t0 not in self.tx_periods:
                    asyncio.create_task(self._early(mode, t0, rng))
                now = time.time()
            try:
                await asyncio.wait_for(self._stop.wait(), max(0.0, due - now))
                return
            except asyncio.TimeoutError:
                pass
            if not self.config["enabled"]:
                continue
            # The mode may have changed meanwhile (band change): the slice
            # belongs to the mode its period was defined by.
            asyncio.create_task(self._cycle(mode, t0))

    async def _cycle(self, mode: str, t0: float) -> None:
        period = MODES[mode]["period"]
        radio = self.radio_status() or {}
        vfo = radio.get("vfo") or {}
        stat = {"mode": mode,
                "utc": dt.datetime.fromtimestamp(t0, dt.timezone.utc)
                         .strftime("%H:%M:%S.%f")[:10],
                "dial_hz": vfo.get("freq_hz"), "radio_mode": vfo.get("mode")}
        if t0 in self.tx_periods:
            stat["skipped"] = "own transmission"
        elif not await asyncio.to_thread(clock_synced):
            stat["skipped"] = "clock not synchronized (NTP)"
        elif not radio.get("connected"):
            # Also covers the first seconds after start, before the first
            # poll: AUTO cannot know the band yet.
            stat["skipped"] = "radio not connected"
        elif radio.get("tx"):
            stat["skipped"] = "transmitting"
        elif vfo.get("mode") not in DIGI_MODES:
            stat["skipped"] = f"radio in {vfo.get('mode')}"
        elif self._busy:
            stat["skipped"] = "previous decode still running"
        if "skipped" in stat:
            self._record(stat, [])
            return
        # The capture thread can run a little behind the clock (measured while
        # a browser had audio open: the Opus path adds work, and the slice was
        # not complete 0.1 s after the period - whole periods were dropped as
        # "no audio"). Wait up to SLICE_WAIT for the last samples instead.
        x = await self._slice(mode, t0, period)
        if x is not None and self.capture.gain_db:
            x = apply_gain(x, self.capture.gain_db)
        if x is None:
            stat["skipped"] = "no audio for this period"
            self._record(stat, [])
            return
        self._busy = True
        try:
            wav, rms, peak = await asyncio.to_thread(self._write_slice, mode, t0, x)
            stat.update(rms_dbfs=rms, peak_dbfs=peak)
            # Partner pass: while a QSO runs, decode the partner's narrow
            # range on the complete slice first (depth 2, ~0.4 s) so the
            # sequencer has the reply long before our next start — the
            # 13.4 s early pass misses a partner that transmits late.
            rng = self.early_range()
            if rng is not None and rng[1] - rng[0] <= 1200 and self.on_decodes:
                try:
                    t = time.monotonic()
                    ptext = await self._jt9(mode, wav, 2, rng, sub="partner")
                    pdec = self._enrich(dedupe(parse_jt9(ptext)), mode, t0, vfo.get("freq_hz"))
                    stat["partner_ms"] = round((time.monotonic() - t) * 1000)
                    # partner=True: the complete slice, the partner's range searched
                    await self.on_decodes(pdec, t0, mode, True, partner=True)
                except Exception as exc:  # noqa: BLE001
                    log.warning("partner pass %s failed: %s", mode, exc)
            depth = self._depth(mode)
            stat["depth"] = depth
            t = time.monotonic()
            try:
                text = await self._jt9_parallel(mode, wav, depth)
            finally:
                stat["decode_ms"] = round((time.monotonic() - t) * 1000)
                self._adapt(mode, stat["decode_ms"] / 1000)
            decodes = self._enrich(dedupe(parse_jt9(text)), mode, t0, vfo.get("freq_hz"))
            self._last_df[mode] = [d["df"] for d in decodes]
            self._record(stat, decodes)
            self._prune(mode)
            if self.on_decodes:
                await self.on_decodes(decodes, t0, mode, False)
        except Exception as exc:  # noqa: BLE001
            stat["error"] = str(exc)
            log.warning("decode %s %s failed: %s", mode, stat["utc"], exc)
            self._record(stat, [])
        finally:
            self._busy = False

    def _enrich(self, decodes: list[dict], mode: str, t0: float, dial: Optional[int]) -> list[dict]:
        utc = dt.datetime.fromtimestamp(t0, dt.timezone.utc).strftime("%H:%M:%S.%f")[:10]
        for d in decodes:
            info = analyse(d["msg"])
            d.update(info, mode=mode, period_utc=utc, t0=t0,
                     even=int(t0 // MODES[mode]["period"]) % 2 == 0,
                     rf_hz=dial + d["df"] if dial else None,
                     dxcc=self.dxcc.lookup(info["from"]) if info.get("from") else None)
            if self.worked_lookup and d.get("from"):
                d["worked"] = self.worked_lookup(d["from"], dial, d["dxcc"])
        return decodes

    async def _early(self, mode: str, t0: float, rng: tuple[int, int]) -> None:
        """Depth-1 decode of the period so far (rest zero-padded), only for
        the sequencer — the full pass after the period end feeds the UI."""
        period = MODES[mode]["period"]
        x = self.capture.extract(t0 - CONTEXT, EARLY_AT[mode] + CONTEXT)
        if x is None:
            return
        if self.capture.gain_db:
            x = apply_gain(x, self.capture.gain_db)
        x = np.concatenate([x, np.zeros(int((period + 2 * CONTEXT) * RATE) - len(x))])
        vfo = (self.radio_status() or {}).get("vfo") or {}
        try:
            wav, _, _ = await asyncio.to_thread(self._write_slice, mode, t0, x, "early")
            t = time.monotonic()
            text = await self._jt9(mode, wav, 1, rng, sub="early")
            wav.unlink(missing_ok=True)            # one-shot: never piles up in /run
            decodes = self._enrich(parse_jt9(text), mode, t0, vfo.get("freq_hz"))
            log.debug("early %s: %d decodes in %.1f s", mode, len(decodes), time.monotonic() - t)
            if self.on_decodes:
                await self.on_decodes(decodes, t0, mode, True)
        except Exception as exc:  # noqa: BLE001
            log.warning("early decode %s failed: %s", mode, exc)

    async def _slice(self, mode: str, t0: float, period: float) -> Optional[np.ndarray]:
        """The period's audio with CONTEXT on each side; waits up to SLICE_WAIT
        while the capture has not got the last samples yet."""
        x = self.capture.extract(t0 - CONTEXT, period + 2 * CONTEXT)
        waited = 0.0
        while x is None and waited < SLICE_WAIT and self.capture.running:
            await asyncio.sleep(0.05)
            waited += 0.05
            x = self.capture.extract(t0 - CONTEXT, period + 2 * CONTEXT)
        if x is not None and waited:
            log.info("%s slice complete %.2f s late (capture behind the clock)", mode, waited)
        return x

    def _write_slice(self, mode: str, t0: float, x48: np.ndarray, sub: str = ""):
        period = MODES[mode]["period"]
        y = to_12k(x48)
        c = int(CONTEXT * 12_000)
        y = y[c:c + int(period * 12_000)]
        core = x48[int(CONTEXT * RATE):int((CONTEXT + period) * RATE)] / 32768
        rms = round(20 * np.log10(np.sqrt(np.mean(core * core)) or 1e-9), 1)
        peak = round(20 * np.log10(np.max(np.abs(core)) or 1e-9), 1)
        name = dt.datetime.fromtimestamp(t0, dt.timezone.utc).strftime("%y%m%d_%H%M%S")
        folder = self.work / mode / sub if sub else self.work / mode
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{name}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(12_000)
            w.writeframes(np.clip(y, -32768, 32767).astype("<i2").tobytes())
        return path, rms, peak

    def _depth(self, mode: str) -> int:
        d = self.config["depth"]
        return int(d) if d != "AUTO" else self.depth[mode]

    def _adapt(self, mode: str, seconds: float) -> None:
        """Keep decoding inside the period, without oscillating.

        Down only when the period is really at risk (> 80 %, a timeout lands
        here too). Up only when the next depth is *predicted* to fit, using
        the measured cost ratios — fixed thresholds made a 9 s depth-2 run
        flip to depth 1 and straight back, losing ~30 % of the decodes on
        every other cycle.
        """
        budget = LATENCY_BUDGET[mode]
        d = self.depth[mode]
        if seconds > budget and d > 1:
            self.depth[mode] = d - 1
            self._fits[mode] = 0
        elif d < 3 and seconds * DEPTH_COST[d] < budget:
            # Two fitting cycles in a row: a single quiet or broken slice
            # (2 decodes right after boot, 5.6 s) must not push FT8 to depth
            # 3, which then needs 14.6 s on a busy band.
            self._fits[mode] += 1
            if self._fits[mode] >= 2:
                self.depth[mode] = d + 1
                self._fits[mode] = 0
        else:
            self._fits[mode] = 0
        if self.depth[mode] != d:
            log.info("%s decode took %.1f s → depth %d", mode, seconds, self.depth[mode])

    async def _jt9_parallel(self, mode: str, wav: Path, depth: int) -> str:
        """The full pass, split over PARTS cores. Each part gets its own
        working dir (jt9 writes scratch files next to the WAV and in -a)."""
        if self.config.get("jt9_mt") and mode == "FT8":
            # MTft8: one process, PARTS threads, the whole band
            return await self._jt9(mode, wav, depth, threads=PARTS)
        rngs = split_band(self.config["low_hz"], self.config["high_hz"],
                          last_df=self._last_df.get(mode))
        if len(rngs) == 1:
            return await self._jt9(mode, wav, depth)
        jobs = []
        for i, rng in enumerate(rngs):
            part = wav.parent / f"p{i}"
            part.mkdir(exist_ok=True)
            link = part / wav.name
            link.unlink(missing_ok=True)
            os.link(wav, link)
            jobs.append(self._jt9(mode, link, depth, rng, sub=f"p{i}"))
        try:
            outs = await asyncio.gather(*jobs)
        finally:
            for i in range(len(rngs)):
                (wav.parent / f"p{i}" / wav.name).unlink(missing_ok=True)
        return "".join(outs)

    async def _jt9(self, mode: str, wav: Path, depth: int,
                   rng: Optional[tuple[int, int]] = None, sub: str = "",
                   threads: int = 0) -> str:
        c = self.config
        lo, hi = rng or (c["low_hz"], c["high_hz"])
        data = self.data / mode / sub if sub else self.data / mode
        data.mkdir(parents=True, exist_ok=True)   # own dir: passes may overlap
        # Lower priority than the web server: the full pass of the partner's
        # period runs on three cores exactly while our own transmission has
        # to be keyed on time (at nice -5 it starved the TX timing).
        cmd = ["nice", "-n", "5", c.get("jt9") or "jt9", MODES[mode]["flag"],
               "-p", MODES[mode]["tr"], "-d", str(depth),
               "-L", str(lo), "-H", str(hi),
               "-a", str(data), "-t", str(wav.parent)]
        if c["my_call"]:
            cmd += ["-c", c["my_call"]]
        if c["my_grid"]:
            cmd += ["-G", c["my_grid"][:4]]     # messages carry 4-char grids
        if threads:
            cmd += ["-M", "-N", str(threads)]    # WSJT-X 3.3 MTft8
        proc = await asyncio.create_subprocess_exec(
            *cmd, str(wav), cwd=str(wav.parent),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(),
                                              MODES[mode]["period"])
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("jt9 took longer than one period")
        if proc.returncode != 0:
            raise RuntimeError(f"jt9 exit {proc.returncode}: {err.decode()[-200:]}")
        return out.decode(errors="replace")

    def _record(self, stat: dict, decodes: list[dict]) -> None:
        stat["count"] = len(decodes)
        if decodes:
            stat["mean_abs_dt"] = round(sum(abs(d["dt"]) for d in decodes)
                                        / len(decodes), 2)
        self.cycles.append(stat)
        self.decodes.extend(decodes)
        asyncio.get_running_loop().create_task(
            self.manager.broadcast({"type": "cycle", "cycle": stat,
                                    "decodes": decodes}))

    def _prune(self, mode: str) -> None:
        # Keep the latest slices for A/B comparison against WSJT-X.
        wavs = sorted((self.work / mode).glob("*.wav"))
        for p in wavs[:-self.keep_slices]:
            p.unlink(missing_ok=True)
