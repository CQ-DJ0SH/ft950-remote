"""Station statistics for the DIGI panel: who hears us (PSK Reporter) and how
often our QRZ.com page was looked at.

PSK Reporter (https://pskreporter.info/pskdev.html): the public query
interface, used as its author asks - at most once every 5 minutes, only
after an own transmission and 5 minutes after it (so every receiver has
reported), compressed, and incremental (lastseqno: only what is new). No
transmission, no query. The reports are kept for 24 h; the panel shows those
of the band VFO A is on, from the last WINDOW_S.

QRZ.com: the XML lookup of the own call carries u_views, the page views of
that call - every 5 minutes, through the logbook's QRZ login (it needs a
QRZ XML subscription). The count at the first look of a UTC day is kept, so
the panel shows the views since then.
"""
from __future__ import annotations

import asyncio
import gzip
import logging
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Callable, Optional

from . import runtime
from .config import APP_VERSION
from .digi import distance_km, grid_ll
from .logbook import band_for_hz

log = logging.getLogger("ft950.stats")

PSK_URL = "https://retrieve.pskreporter.info/query"
PSK_MIN_GAP_S = 300          # the author's rule: not more often than every 5 minutes
PSK_AFTER_TX_S = 300         # and 5 minutes after the transmission
WINDOW_S = 1800              # the panel: receivers of the last 30 minutes
KEEP_S = 24 * 3600
QRZ_EVERY_S = 300           # QRZ page views: every 5 minutes (288 lookups a day)
AGENT = f"ft950-remote {APP_VERSION}"


def _band(hz) -> str:
    try:
        return band_for_hz(int(hz)) if hz else ""
    except (TypeError, ValueError):
        return ""


class StationStats:
    def __init__(self, my_call: Callable[[], str], my_grid: Callable[[], str],
                 vfo_hz: Callable[[], Optional[int]], transmitting: Callable[[], bool],
                 qrz_lookup: Callable[[str], dict], qrz_ok: Callable[[], bool]) -> None:
        self._call, self._grid, self._vfo = my_call, my_grid, vfo_hz
        self._tx, self._qrz_lookup, self._qrz_ok = transmitting, qrz_lookup, qrz_ok
        # PSK Reporter
        self.reports: dict[tuple[str, str], dict] = {}   # (receiver, band) -> latest report
        self._seq: Optional[int] = None
        self._last_query = 0.0
        self._first_tx: Optional[float] = None           # first own transmission since the last query
        self.psk_error = ""
        self.psk_updated = 0.0
        # QRZ
        self.qrz_views: Optional[int] = None
        self.qrz_updated = 0.0
        self.qrz_error = ""
        self._qrz_day = runtime.load().get("qrz_views_day") or {}
        # Settings ▸ FT8 ▸ Station statistics: off = no queries at all
        self.psk_on = True
        self.qrz_on = True

    # -- PSK Reporter ------------------------------------------------------------
    def _due(self, now: float) -> bool:
        if self._first_tx is None:
            return False
        return now - self._last_query >= PSK_MIN_GAP_S and now - self._first_tx >= PSK_AFTER_TX_S

    def _query(self, call: str) -> None:
        params = {"senderCallsign": call, "rronly": 1, "noactive": 1}
        if self._seq is not None:
            params["lastseqno"] = self._seq
        else:
            params["flowStartSeconds"] = -3 * 3600
        req = urllib.request.Request(PSK_URL + "?" + urllib.parse.urlencode(params),
                                     headers={"User-Agent": AGENT, "Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
        root = ET.fromstring(raw)
        n = 0
        for el in root:
            tag = el.tag.rsplit("}", 1)[-1]
            if tag == "lastSequenceNumber":
                self._seq = int(el.get("value", "0")) or self._seq
            elif tag == "receptionReport":
                rx = (el.get("receiverCallsign") or "").upper()
                band = _band(el.get("frequency"))
                if not rx or not band:
                    continue
                t = float(el.get("flowStartSeconds") or 0)
                key = (rx, band)
                if key in self.reports and self.reports[key]["t"] >= t:
                    continue
                self.reports[key] = {"call": rx, "band": band, "t": t, "loc": el.get("receiverLocator") or "",
                                     "dxcc": el.get("receiverDXCC") or "", "snr": el.get("sNR"),
                                     "mode": el.get("mode") or ""}
                n += 1
        cut = time.time() - KEEP_S
        self.reports = {k: v for k, v in self.reports.items() if v["t"] >= cut}
        log.info("PSK Reporter: %d new reception reports of %s", n, call)

    def psk_view(self) -> dict:
        band = _band(self._vfo())
        now = time.time()
        rows = [r for r in self.reports.values() if r["band"] == band and now - r["t"] <= WINDOW_S]
        me = grid_ll(self._grid() or "")
        far, far_call = 0, ""
        for r in rows:
            ll = grid_ll(r["loc"]) if r["loc"] else None
            if me and ll:
                km = distance_km(me, ll)
                if km > far:
                    far, far_call = km, r["call"]
        return {"band": band, "n": len(rows), "countries": len({r["dxcc"] for r in rows if r["dxcc"]}),
                "far_km": round(far) if far else None, "far_call": far_call,
                "window_min": WINDOW_S // 60, "updated": self.psk_updated or None,
                "error": self.psk_error, "enabled": self.psk_on,
                "waiting": self._first_tx is not None}

    # -- QRZ ---------------------------------------------------------------------
    def _qrz(self, call: str) -> None:
        q = self._qrz_lookup(call)
        v = q.get("views") if q else None
        if v in (None, ""):
            self.qrz_error = "no page views in the QRZ answer (XML subscription?)"
            return
        self.qrz_views, self.qrz_error = int(v), ""
        self.qrz_updated = time.time()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._qrz_day.get("date") != today:
            self._qrz_day = {"date": today, "views": self.qrz_views}
            runtime.save({"qrz_views_day": self._qrz_day})

    def qrz_view(self) -> dict:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        start = self._qrz_day.get("views") if self._qrz_day.get("date") == today else None
        return {"views": self.qrz_views, "today": (self.qrz_views - start) if (self.qrz_views is not None
                                                                               and start is not None) else None,
                "updated": self.qrz_updated or None, "error": self.qrz_error, "enabled": self.qrz_on,
                "configured": bool(self._qrz_ok())}

    def view(self) -> dict:
        return {"psk": self.psk_view(), "qrz": self.qrz_view()}

    # -- loop --------------------------------------------------------------------
    async def run(self) -> None:
        started = time.time()
        qrz_next = started + 20
        first_psk = True                               # one look after a start (a restart forgets)
        while True:
            await asyncio.sleep(10)
            now = time.time()
            if self._tx():
                if self._first_tx is None:
                    self._first_tx = now
            call = (self._call() or "").upper()
            if not call:
                continue
            if not self.psk_on:
                self._first_tx = None                     # off: nothing waits for a query
            elif (first_psk and now - started >= 60) or self._due(now):
                first_psk = False
                self._last_query, self._first_tx = now, None
                try:
                    await asyncio.to_thread(self._query, call)
                    self.psk_error, self.psk_updated = "", time.time()
                except Exception as exc:  # noqa: BLE001
                    self.psk_error = str(exc)[:120]
                    log.warning("PSK Reporter query: %s", exc)
            if now >= qrz_next:
                qrz_next = now + QRZ_EVERY_S
                if self._qrz_ok() and self.qrz_on:
                    try:
                        await asyncio.to_thread(self._qrz, call)
                    except Exception as exc:  # noqa: BLE001
                        self.qrz_error = str(exc)[:120]
                        log.warning("QRZ views: %s", exc)
