"""HackRF waterfall from TM-V71 Remote on another Pi (as its own HackRF panel).

The HackRF One is plugged into the Pi that runs TM-V71 Remote (default
https://192.168.201.155:8443). That app already turns it into spectrum rows
(``/api/hackrf``, ``/ws/hackrf``); this module relays both, so the browser
only ever talks to this server.

*Follow* is done here, not on the other Pi: TM-V71 Remote would follow its own
radio. While following, the HackRF is centred on the FT-950's VFO A plus
``OFFSET_HZ`` — TM-V71 Remote notches the DC spike in the middle of the span,
which would otherwise blank the very segment the FT-950 listens to.

The HackRF is single-tenant: whatever this page sets (centre, gains, mode) is
what TM-V71 Remote's own waterfall shows too.
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

import websockets
from fastapi import WebSocket, WebSocketDisconnect

log = logging.getLogger("ft950.hackrf")

DEFAULT_URL = "https://192.168.201.155:8443"
OFFSET_HZ = 250_000            # dial frequency sits left of centre, clear of the DC notch
RETUNE_HZ = 400_000            # re-centre once VFO A + offset is this far off (span 2 MHz)
MIN_CENTER = 1_000_000         # HackRF tuning floor
SPAN = 2_000_000               # panadapter span (TM-V71 Remote: the HackRF's minimum rate)
EDGE_HZ = 30_000               # keep the DC notch this far outside the band
ZOOM_MARGIN_HZ = 2_000         # fine zoom row a little wider than the band
# Amateur bands, IARU Region 1 (Hz)
BANDS = [(1_810_000, 2_000_000), (3_500_000, 3_800_000), (5_351_500, 5_366_500),
         (7_000_000, 7_200_000), (10_100_000, 10_150_000), (14_000_000, 14_350_000),
         (18_068_000, 18_168_000), (21_000_000, 21_450_000), (24_890_000, 24_990_000),
         (28_000_000, 29_700_000), (50_000_000, 52_000_000)]


def band_of(f: int) -> tuple[int, int] | None:
    return next(((lo, hi) for lo, hi in BANDS if lo <= f <= hi), None)


def band_center(lo: int, hi: int) -> int:
    """Centre that shows the whole band: just above its top edge, so the DC
    notch in the middle of the span falls outside it — when the band fits in
    one half of the span; otherwise (10 m, 6 m) the band's own middle."""
    if hi + EDGE_HZ - lo <= SPAN // 2:
        return hi + EDGE_HZ
    return (lo + hi) // 2


class HackRFProxy:
    def __init__(self, url_getter: Callable[[], str], ca_file: Path,
                 freq_getter: Callable[[], Optional[int]]) -> None:
        self.url_getter = url_getter
        self.ca_file = ca_file
        self.freq_getter = freq_getter
        self.follow = True
        self.clients = 0
        self.running = False
        self.mode = "pan"
        self.center: Optional[int] = None
        self.upstream_zoom: Optional[list] = None   # zoom TM-V71 Remote reports …
        self.zoom_supported: Optional[bool] = None  # … if its version knows zoom (None = not seen yet)

    # -- upstream REST ------------------------------------------------------
    def _ssl(self) -> ssl.SSLContext:
        if self.ca_file.is_file():
            return ssl.create_default_context(cafile=str(self.ca_file))
        return ssl.create_default_context()

    @property
    def url(self) -> str:
        return (self.url_getter() or DEFAULT_URL).rstrip("/")

    def _call(self, path: str, body: Optional[dict] = None) -> dict:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data, method="GET" if body is None else "POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=4, context=self._ssl()) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:200]
            raise HackRFError(f"HackRF source answered {e.code}: {detail}") from e
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise HackRFError(f"HackRF source {self.url} unreachable: {getattr(e, 'reason', e)}") from e

    def _note(self, st: dict) -> dict:
        self.running = bool(st.get("running"))
        self.mode = st.get("mode", self.mode)
        if st.get("center"):
            self.center = int(st["center"])
        self.zoom_supported = "zoom" in st          # older TM-V71 Remote: no zoom, no retries
        self.upstream_zoom = st.get("zoom")
        b = self.band() if self.follow else None
        return {**st, "follow": self.follow, "source": self.url, "reachable": True,
                "offset_hz": OFFSET_HZ, "band": list(b) if b else None}

    def band(self) -> tuple[int, int] | None:
        f = self.freq_getter()
        return band_of(int(f)) if f else None

    def target(self) -> Optional[int]:
        """Follow: the whole band of VFO A in view; outside a band, VFO A + offset."""
        f = self.freq_getter()
        if not f:
            return None
        b = band_of(int(f))
        return max(MIN_CENTER, band_center(*b) if b else int(f) + OFFSET_HZ)

    def _prepare(self, cfg: dict) -> dict:
        if "follow" in cfg:
            self.follow = bool(cfg["follow"])
        cfg["follow"] = False                    # never follow the TM-V71
        if self.follow and cfg.get("mode", self.mode) == "pan" and (t := self.target()):
            cfg["center"] = t
        # band view: ask TM-V71 Remote for a fine row of just the band (zf0/zf1/zdb);
        # the normal 512-bin row would leave only ~50 bins for 40 m
        cfg["zoom"] = self.wanted_zoom()
        return cfg

    def wanted_zoom(self) -> list:
        b = self.band() if self.follow else None
        return [b[0] - ZOOM_MARGIN_HZ, b[1] + ZOOM_MARGIN_HZ] if b else [0, 0]

    async def status(self) -> dict:
        try:
            return self._note(await asyncio.to_thread(self._call, "/api/hackrf"))
        except HackRFError as e:
            return {"reachable": False, "running": False, "error": str(e), "source": self.url,
                    "follow": self.follow, "offset_hz": OFFSET_HZ}

    async def start(self, cfg: dict) -> dict:
        return self._note(await asyncio.to_thread(self._call, "/api/hackrf/start", self._prepare(cfg)))

    async def stop(self) -> dict:
        return self._note(await asyncio.to_thread(self._call, "/api/hackrf/stop", {}))

    async def configure(self, cfg: dict) -> dict:
        return self._note(await asyncio.to_thread(self._call, "/api/hackrf/config", self._prepare(cfg)))

    # -- follow VFO A ---------------------------------------------------------
    async def follow_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            if not (self.follow and self.clients and self.running and self.mode == "pan"):
                continue
            t = self.target()
            want = self.wanted_zoom()
            stale_zoom = self.zoom_supported is not False and (self.upstream_zoom or [0, 0]) != want
            if t and (stale_zoom or self.center is None
                      or (t != self.center and (self.band() or abs(t - self.center) > RETUNE_HZ))):
                try:
                    await self.configure({"center": t})
                    log.info("HackRF re-centred to %.3f MHz (VFO A + offset)", t / 1e6)
                except HackRFError as e:
                    log.warning("HackRF follow: %s", e)

    # -- relay /ws/hackrf -------------------------------------------------------
    async def relay(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients += 1
        wss = self.url.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/ws/hackrf"
        try:
            ctx = self._ssl() if wss.startswith("wss://") else None
            async with websockets.connect(wss, ssl=ctx, max_size=4_000_000, open_timeout=5) as up:
                async def browser_side():           # returns when the browser leaves
                    while True:
                        await ws.receive_text()

                async def upstream_side():
                    async for msg in up:
                        m = json.loads(msg)
                        if m.get("t") == "status":
                            m = self._note({k: v for k, v in m.items() if k != "t"}) | {"t": "status"}
                        elif m.get("t") in ("pan", "sweep"):
                            self.running, self.mode = True, m["t"]
                            self.center = m.get("center", self.center)
                            b = self.band() if self.follow and m["t"] == "pan" else None
                            if b and m.get("f0", 0) <= b[0] and b[1] <= m.get("f1", 0):
                                m["band"] = list(b)          # the page shows just this band
                        await ws.send_text(json.dumps(m))

                tasks = [asyncio.create_task(browser_side()), asyncio.create_task(upstream_side())]
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
                for t in done:
                    if t.exception() and not isinstance(t.exception(), WebSocketDisconnect):
                        raise t.exception()
        except WebSocketDisconnect:
            pass
        except Exception as e:  # noqa: BLE001  upstream gone / refused
            try:
                await ws.send_text(json.dumps({"t": "status", "reachable": False, "running": False,
                                               "error": f"HackRF source {self.url}: {e}",
                                               "source": self.url, "follow": self.follow}))
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
        finally:
            self.clients -= 1


class HackRFError(RuntimeError):
    pass
