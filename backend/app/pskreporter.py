"""Spots of decoded FT8/FT4 stations to PSK Reporter (pskreporter.info).

Protocol (pskreporter.info/pskdev.html): IPFIX datagrams by UDP to
report.pskreporter.info:4739. Each datagram holds one receiver record (my
call, locator, software, antenna) and a block of sender records (call,
frequency, SNR, mode, information source, locator, time). The layout of both
is declared by template sets, sent in the first three datagrams and then once
an hour.

Rules kept here:
  - at most one datagram every 5 minutes (more only when one is full), on a
    timer started with the service, not on the clock
  - a station once per hour on the same band and mode (sooner only when its
    locator becomes known) - the page asks for once per 5 minutes at most
  - informationSource 1 (automatically extracted), or the spot is not counted
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import socket
import struct
import time
from typing import Callable, Optional

from .logbook import band_for_hz

log = logging.getLogger("ft950.psk")

HOST, PORT = "report.pskreporter.info", 4739
INTERVAL_S = 300
REPEAT_S = 3600
TEMPLATE_EVERY_S = 3600
MAX_DATAGRAM = 1400          # stays below a 1500-byte MTU
SOFTWARE = "FT950 Remote"
_GRID = re.compile(r"^[A-R]{2}\d{2}([A-X]{2})?$", re.I)

_ENT = b"\x00\x00\x76\x8f"   # enterprise number 30351
RX_TEMPLATE_ID, TX_TEMPLATE_ID = 0x9992, 0x9993


def _field(fid: int, size: int = 0xFFFF) -> bytes:
    return struct.pack(">HH", 0x8000 | fid, size) + _ENT


def _pad4(b: bytes) -> bytes:
    return b + b"\x00" * (-len(b) % 4)


# receiver (options template, set 3): receiverCallsign, receiverLocator,
# decodingSoftware, antennaInformation
RX_TEMPLATE = _pad4(struct.pack(">HHHHH", 3, 0, RX_TEMPLATE_ID, 4, 1)
                    + _field(2) + _field(4) + _field(8) + _field(9))
RX_TEMPLATE = RX_TEMPLATE[:2] + struct.pack(">H", len(RX_TEMPLATE)) + RX_TEMPLATE[4:]
# sender (template set 2): senderCallsign, frequency (4), sNR (1), mode,
# informationSource (1), senderLocator, flowStartSeconds (IANA 150, 4)
_TX_FIELDS = (_field(1) + _field(5, 4) + _field(6, 1) + _field(10) + _field(11, 1)
              + _field(3) + struct.pack(">HH", 150, 4))
TX_TEMPLATE = struct.pack(">HHHH", 2, 8 + len(_TX_FIELDS), TX_TEMPLATE_ID, 7) + _TX_FIELDS


def _str(s: str) -> bytes:
    b = (s or "").encode("ascii", "replace")[:254]
    return bytes([len(b)]) + b


def rx_record(call: str, grid: str, antenna: str) -> bytes:
    body = _str(call) + _str(grid) + _str(SOFTWARE) + _str(antenna)
    return _pad4(struct.pack(">HH", RX_TEMPLATE_ID, 0) + body)


def tx_record(s: dict) -> bytes:
    return (_str(s["call"]) + struct.pack(">I", int(s["freq_hz"]))
            + struct.pack(">b", max(-128, min(127, int(s["snr"])))) + _str(s["mode"])
            + b"\x01" + _str(s.get("grid") or "") + struct.pack(">I", int(s["t"])))


def _set(template_id: int, body: bytes) -> bytes:
    b = _pad4(struct.pack(">HH", template_id, 0) + body)
    return b[:2] + struct.pack(">H", len(b)) + b[4:]


def datagram(seq: int, domain: int, rx: bytes, records: list[bytes], templates: bool,
             now: Optional[float] = None) -> bytes:
    rx = rx[:2] + struct.pack(">H", len(rx)) + rx[4:]
    body = (RX_TEMPLATE + TX_TEMPLATE if templates else b"") + rx
    if records:
        body += _set(TX_TEMPLATE_ID, b"".join(records))
    head = struct.pack(">HHIII", 10, 16 + len(body), int(now or time.time()), seq, domain)
    return head + body


class PskReporter:
    def __init__(self, me: Callable[[], tuple[str, str]], enabled: bool = False,
                 antenna: str = "", send: Optional[Callable[[bytes], None]] = None) -> None:
        self.me = me                              # -> (callsign, locator)
        self.enabled = enabled
        self.antenna = antenna
        self._send = send or self._udp
        self.pending: dict[tuple, dict] = {}      # (call, band, mode) -> spot
        self.reported: dict[tuple, dict] = {}     # (call, band, mode) -> {"t", "grid"}
        self.grids: dict[str, str] = {}           # call -> locator, from CQ / grid messages
        self.seq = 0
        self.domain = random.getrandbits(32)
        self.datagrams = 0
        self._templates_at = 0.0
        self.last: dict = {}                      # last upload: t, spots, error
        self.total = 0
        # statistics since the service started (Settings ▸ FT8 ▸ PSK Reporter)
        self.since = time.time()
        self.uploads = 0
        self.errors = 0
        self.calls: set[str] = set()              # different stations reported
        self.bands: dict[str, int] = {}           # spots per band
        self._task: Optional[asyncio.Task] = None

    # -- collecting -----------------------------------------------------------
    def add(self, decodes: list[dict], mode: str, me: str = "") -> int:
        """Spots from one full decode pass; returns how many were queued."""
        if not self.enabled:
            return 0
        n, now = 0, time.time()
        for d in decodes:
            call = (d.get("from") or "").upper()
            if (not call or call.startswith("<") or call == me.upper() or "?" in (d.get("ap") or "")
                    or not d.get("rf_hz")):
                continue
            if d.get("grid") and _GRID.match(d["grid"]) and d["grid"].upper() != "RR73":
                self.grids[call] = d["grid"].upper()
            band = band_for_hz(d["rf_hz"])
            key = (call, band, mode)
            grid = self.grids.get(call, "")
            done = self.reported.get(key)
            if done and now - done["t"] < REPEAT_S and (done["grid"] or not grid):
                continue
            if key not in self.pending:
                n += 1
            self.pending[key] = {"call": call, "freq_hz": d["rf_hz"], "snr": d.get("snr", 0),
                                 "mode": mode, "grid": grid, "t": d.get("t0") or now}
        if len(self.grids) > 5000:
            self.grids.clear()
        return n

    # -- sending --------------------------------------------------------------
    def build(self, now: Optional[float] = None) -> list[bytes]:
        """Datagrams for everything pending (several only if one is full)."""
        now = now or time.time()
        call, grid = self.me()
        if not call or not grid:
            return []
        rx = rx_record(call.upper(), grid, self.antenna)
        out, batch, size = [], [], 0
        spots = list(self.pending.values())
        templates = self.datagrams < 3 or now - self._templates_at >= TEMPLATE_EVERY_S
        overhead = 16 + len(rx) + 4 + 3 + (len(RX_TEMPLATE) + len(TX_TEMPLATE) if templates else 0)
        for s in spots:
            r = tx_record(s)
            if batch and overhead + size + len(r) > MAX_DATAGRAM:
                out.append(self._finish(rx, batch, templates, now))
                templates = self.datagrams < 3
                overhead = 16 + len(rx) + 4 + 3 + (len(RX_TEMPLATE) + len(TX_TEMPLATE) if templates else 0)
                batch, size = [], 0
            batch.append(r)
            size += len(r)
        if batch:
            out.append(self._finish(rx, batch, templates, now))
        for s in spots:
            band = band_for_hz(s["freq_hz"])
            self.reported[(s["call"], band, s["mode"])] = {"t": now, "grid": s["grid"]}
            self.calls.add(s["call"])
            self.bands[band or "?"] = self.bands.get(band or "?", 0) + 1
        self.pending.clear()
        for k in [k for k, v in self.reported.items() if now - v["t"] > REPEAT_S]:
            del self.reported[k]
        return out

    def _finish(self, rx: bytes, batch: list[bytes], templates: bool, now: float) -> bytes:
        dg = datagram(self.seq, self.domain, rx, batch, templates, now)
        self.seq += len(batch)
        self.datagrams += 1
        if templates:
            self._templates_at = now
        return dg

    def _udp(self, data: bytes) -> None:
        addr = socket.getaddrinfo(HOST, PORT, socket.AF_INET, socket.SOCK_DGRAM)[0][4]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(data, addr)

    async def flush(self) -> None:
        if not self.enabled or not self.pending:
            return
        n = len(self.pending)
        dgs = self.build()
        try:
            for dg in dgs:
                await asyncio.to_thread(self._send, dg)
            self.total += n
            self.uploads += 1
            self.last = {"t": time.time(), "spots": n, "error": None}
            log.info("PSK Reporter: %d spots in %d datagram(s)", n, len(dgs))
        except OSError as exc:
            self.errors += 1
            self.last = {"t": time.time(), "spots": 0, "error": str(exc)}
            log.warning("PSK Reporter upload failed: %s", exc)

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        # from the service start with a random offset, not on the clock
        await asyncio.sleep(random.uniform(30, 90))
        while True:
            try:
                await self.flush()
            except Exception as exc:  # noqa: BLE001
                log.warning("PSK Reporter: %s", exc)
            await asyncio.sleep(INTERVAL_S + random.uniform(-10, 10))

    @property
    def dest(self) -> str:
        return f"{HOST}:{PORT} (UDP)"

    def status(self) -> dict:
        return {"enabled": self.enabled, "pending": len(self.pending), "total": self.total,
                "last_t": self.last.get("t"), "last_spots": self.last.get("spots"),
                "error": self.last.get("error"), "since": self.since, "uploads": self.uploads,
                "datagrams": self.datagrams, "errors": self.errors, "stations": len(self.calls),
                "bands": dict(sorted(self.bands.items(), key=lambda kv: -kv[1]))}
