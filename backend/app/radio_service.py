"""Async service layer around the synchronous FT950 driver.

All radio access goes through ``asyncio.to_thread`` so the event loop is never
blocked; the driver's lock serialises REST handlers and the poller.

One background task polls in two tiers (measured costs: ~25 ms / ~140 ms):

* fast (``settings.fast_interval``): IF, S-meter, busy, TX (+ PO/ALC/SWR while
  transmitting) — what must follow the dial;
* slow (``settings.slow_interval``): the receive/transmit chain, which only
  changes when a control is touched. A REST write forces the next pass to be
  a slow one, so the panel reflects the change at once.

Every fast pass broadcasts the merged state on ``/ws``.
"""
from __future__ import annotations

import asyncio
import logging
import time

from .config import settings
from .ft950 import FT950, FT950Error, MODEL_ID
from .state import ConnectionManager

log = logging.getLogger("ft950")

# FT8/FT4 rig profile: (read command, set command, expected read-back).
# Order matters — the mode picks the SH column, so MD and NA go first.
# Operator choices (AGC, IPO/ATT, power) are deliberately not touched.
FT8_PROFILE: list[tuple[str, str, str]] = [
    ("MD0", "MD0C", "MD0C"),            # DATA-U
    ("NA0", "NA00", "NA00"),            # narrow off (else the N column applies)
    ("SH0", "SH013", "SH013"),          # 2400 Hz, max in DATA-U (measured)
    ("IS0", "IS0+0000", "IS0+0000"),    # IF shift centred
    ("RF0", "RF03", "RF03"),            # roofing 3 kHz
    ("BC0", "BC00", "BC00"),            # DNF off
    ("BP00", "BP00000", "BP00000"),     # manual notch off
    ("NR0", "NR00", "NR00"),            # noise reduction off
    ("NB0", "NB00", "NB00"),            # noise blanker off
    ("CO00", "CO0000", "CO0000"),       # contour off (reads back 2 digits)
    ("SQ0", "SQ0000", "SQ0000"),        # squelch open
]


def voice_profile(freq_hz: int | None) -> list[tuple[str, str, str]]:
    """Remote voice: the browser mic reaches the radio through the SCU-17 on
    the DATA jack, which the FT-950 only takes in the DATA (PKT) modes — so
    voice stays in DATA, on the band's usual sideband: DATA-L below 10 MHz,
    DATA-U above (and on 60 m, which is USB by convention)."""
    f = freq_hz or 14_000_000
    lsb = f < 10_000_000 and not 5_250_000 <= f <= 5_450_000
    md = "MD08" if lsb else "MD0C"
    return [
        ("MD0", md, md),                    # DATA-L / DATA-U
        ("NA0", "NA00", "NA00"),            # narrow off
        ("SH0", "SH013", "SH013"),          # 2400 Hz, the widest in DATA
        ("IS0", "IS0+0000", "IS0+0000"),    # IF shift centred
        ("RF0", "RF03", "RF03"),            # roofing 3 kHz (AUTO reads back as AUTO-x)
        ("GT0", "GT03", "GT03"),            # AGC SLOW for speech
        ("BC0", "BC00", "BC00"),            # DNF off
        ("BP00", "BP00000", "BP00000"),     # manual notch off
        ("NB0", "NB00", "NB00"),            # noise blanker off
        ("CO00", "CO0000", "CO0000"),       # contour off
        ("SQ0", "SQ0000", "SQ0000"),        # squelch open
    ]


class RadioService:
    def __init__(self, manager: ConnectionManager) -> None:
        self.radio = FT950(settings.serial_port, settings.serial_baud,
                           settings.serial_timeout)
        self.manager = manager
        self.model: str | None = None
        self._state: dict = {"connected": False, "error": None}
        self._slow: dict = {}
        self._last_slow = 0.0
        self._force_slow = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._fails = 0

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await asyncio.gather(self._task, return_exceptions=True)
        await asyncio.to_thread(self.radio.close)

    @property
    def status(self) -> dict:
        return self._state

    # -- polling -----------------------------------------------------------
    def _connect(self) -> None:
        self.radio.close()
        self.radio.open()
        model = self.radio.identify()
        if model != MODEL_ID:
            raise FT950Error(f"unexpected radio ID {model!r} (FT-950 is {MODEL_ID})")
        self.model = model

    async def _poll_loop(self) -> None:
        while not self._stop.is_set():
            if not self._state["connected"]:
                try:
                    await asyncio.to_thread(self._connect)
                    log.info("Connected to FT-950 on %s", settings.serial_port)
                    self._last_slow = 0.0            # full picture first
                except Exception as exc:  # noqa: BLE001
                    await self._offline(exc)
                    await self._sleep(settings.reconnect_interval)
                    continue
            try:
                fast = await asyncio.to_thread(self.radio.read_fast)
                now = time.monotonic()
                if (self._force_slow.is_set()
                        or now - self._last_slow >= settings.slow_interval):
                    self._force_slow.clear()
                    # frequency/mode first: the slow chain read (~25 queries)
                    # must not hold back what a band or mode click changed
                    await self.manager.broadcast({"connected": True, "error": None,
                                                  "model": self.model, **self._slow, **fast})
                    self._slow = await asyncio.to_thread(
                        self.radio.read_slow, fast["vfo"]["mode"])
                    self._last_slow = now
                self._state = {"connected": True, "error": None,
                               "model": self.model, **self._slow, **fast}
                if not fast["tx"]:
                    self._state.pop("meters", None)
                await self.manager.broadcast(self._state)
                self._fails = 0
            except Exception as exc:  # noqa: BLE001
                # Measured: right after an FA write the radio can miss one
                # query while it retunes. One miss is not a dead link.
                self._fails += 1
                if self._fails < 3:
                    log.info("missed reply (%d/3): %s", self._fails, exc)
                    await self._sleep(settings.fast_interval)
                    continue
                log.warning("Radio link lost: %s", exc)
                self._fails = 0
                await self._offline(exc)
                continue
            await self._sleep(settings.fast_interval)

    async def _offline(self, exc: Exception) -> None:
        was = self._state.get("connected")
        self._state = {"connected": False, "error": str(exc)}
        if was or not self._slow:
            await self.manager.broadcast(self._state)

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), seconds)
        except asyncio.TimeoutError:
            pass

    def set_port(self, port: str, baud: int | None = None) -> None:
        """Move to another serial port/baud rate; the poll loop reconnects and
        checks the model ID again."""
        self.radio.close()
        self.radio.port = port
        if baud:
            self.radio.baudrate = baud
        self._state = {"connected": False, "error": f"switching to {port}"}

    # -- commands ----------------------------------------------------------
    async def run(self, fn, *args):
        """Run a driver call off the event loop, then refresh the chain."""
        result = await asyncio.to_thread(fn, *args)
        self._force_slow.set()
        return result

    def _apply_profile(self, profile: list[tuple[str, str, str]]) -> list[dict]:
        report = []
        for read_cmd, set_cmd, expect in profile:
            entry = {"set": set_cmd}
            try:
                before = self.radio.query(read_cmd)
                if before.upper() == expect.upper():
                    entry["result"] = "unchanged"
                else:
                    self.radio.set_verified(set_cmd, read_cmd, expect)
                    entry.update(result="changed", before=before)
            except FT950Error as exc:
                entry.update(result="failed", error=str(exc))
            report.append(entry)
        return report

    async def apply_voice_profile(self) -> list[dict]:
        """Voice profile for the current band, diff-applied like FT8's."""
        freq = (self._state.get("vfo") or {}).get("freq_hz")
        return await self.run(self._apply_profile, voice_profile(freq))

    async def apply_ft8_profile(self) -> list[dict]:
        """Diff-apply: read each value, write only what differs, verify it."""
        return await self.run(self._apply_profile, FT8_PROFILE)
