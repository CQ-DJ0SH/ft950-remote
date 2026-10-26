"""FT8/FT4 auto-sequencer — the WSJT-X QSO flow.

Answering a station (Answer, or double-click a decode):
    THEM ME GRID  →  (their report)  THEM ME R-NN  →  (their RR73/RRR)  THEM ME 73
Calling CQ:
    CQ ME GRID  →  (a call to me)  THEM ME -NN  →  (their R-report)  THEM ME RR73
(RR73 or RRR, Settings ▸ FT8 — like WSJT-X's "RR73" box only the text changes.)
A repeat of the partner's last message means ours was missed: a repeated
R-report gets RR73 again, a repeated RR73/RRR gets 73 again.

We always transmit in the period opposite to the partner's. The reply to a
period must start in the very next one, so while a sequence runs the decoder
does an early depth-1 pass at 13.4 s (FT8) / 5.74 s (FT4) and the transmitter
may start up to 2.0 s (FT8) / 1.5 s (FT4) late — still inside the period and
decodable (positive DT). A step is repeated at most MAX_TRIES times; CQ at most
CQ_MAX times. A finished QSO is logged automatically when auto-log is on,
otherwise offered to the log form.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from . import ftgen
from .digi import MODES
from .logbook import LogError, band_for_hz
from .tx import FT_LATEST_START, clamp_tx_hz

log = logging.getLogger("ft950.seq")

MAX_TRIES = 5
CQ_MAX = 40
WAIT_MARGIN = 0.45          # stop waiting for decodes this long before the latest start
# Hunt (the DIGI strip's auto-clicker): switches itself off after this long, and
# only calls while a browser shows the DIGI panel - an operator at the station.
HUNT_MAX_S = 180 * 60       # default; Settings ▸ FT8 ▸ Hunt, 0 = no switch-off
HUNT_BLOCK_MAX = 1000       # calls kept on the no-reply list


def report(snr: int) -> str:
    """WSJT-X report format: -05, +12 (clamped to the encodable -30…+49)."""
    snr = max(-30, min(49, int(snr)))
    return f"{'-' if snr < 0 else '+'}{abs(snr):02d}"


class Sequencer:
    def __init__(self, digi, tx, logbook, manager, settings) -> None:
        self.digi, self.tx, self.logbook = digi, tx, logbook
        self.manager, self.settings = manager, settings
        self.kind: Optional[str] = None          # "cq" | "answer" | None
        self.closing = "RR73"                    # or "RRR" (Settings ▸ FT8)
        self.session: Optional[dict] = None
        self.sessions: deque[dict] = deque(maxlen=50)
        self.step: Optional[str] = None
        self.tries = 0
        self.cq_count = 0
        self.mode = "FT8"
        self.parity = 0                          # my TX periods: index % 2 == parity
        # Auto answer: while idle, a station calling me (grid / report /
        # R-report to my call) is answered without a click, on auto_tx_hz.
        self.auto_answer = False
        self.auto_tx_hz: float = 1500.0
        self.tx_hz = 1500.0
        self.last_tx: Optional[dict] = None
        # Hunt: while idle, call a new station's CQ (not worked on this band),
        # wait for the QSO to end; a station that never answered goes on the
        # blocked list and is not called again (persisted by main.py).
        self.hunt = False
        self.hunt_until = 0.0
        self.hunt_blocked: dict[str, float] = {}          # call -> when it was blocked
        self.on_hunt_block = None                         # callback(dict) to persist the list
        # Settings ▸ FT8 ▸ Hunt
        self.hunt_target = "band"       # band: not worked on this band | call: never worked |
                                        # dxcc: DXCC never worked | dxcc_band: DXCC new on this band
        self.hunt_min_snr = -24         # weaker CQs are passed over
        self.hunt_minutes = HUNT_MAX_S // 60   # 0: Hunt never switches itself off
        self._seen: dict[float, bool] = {}       # period t0 → decodes processed
        self._event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    # -- control ----------------------------------------------------------------
    @property
    def me(self) -> str:
        return (self.settings.my_call or "").upper()

    @property
    def grid4(self) -> str:
        return (self.settings.my_grid or "")[:4].upper()

    def _require(self) -> None:
        if not self.me or not self.grid4:
            raise RuntimeError("Callsign and locator missing (Settings ▸ General)")

    async def start_cq(self, tx_hz: float) -> dict:
        self._require()
        await self.halt(stop_tx=False)
        self.mode, self.tx_hz = self.digi.current_mode(), clamp_tx_hz(tx_hz)
        period = MODES[self.mode]["period"]
        self.parity = (math.floor(time.time() / period) + 1) % 2    # the next period
        self.kind, self.step, self.tries, self.cq_count, self.session = "cq", "cq", 0, 0, None
        self._run()
        return await self.publish()

    async def answer(self, d: dict, tx_hz: float) -> dict:
        """Start (or take over) a QSO with the sender of decode ``d``."""
        self._require()
        them = (d.get("from") or "").upper()
        if not them or them.startswith("<"):
            raise RuntimeError("No callsign to answer in this line")
        await self.halt(stop_tx=False)
        self.mode, self.tx_hz = d.get("mode") or self.digi.current_mode(), clamp_tx_hz(tx_hz)
        period = MODES[self.mode]["period"]
        self.parity = (round(d["t0"] / period) + 1) % 2              # opposite to theirs
        self.kind = "answer"
        self.session = self._new_session(d)
        to_me = (d.get("to") or "").upper() == self.me
        kind = d.get("kind")
        self.step = ("report" if to_me and kind == "grid" else
                     "rreport" if to_me and kind == "report" else
                     "rr73" if to_me and kind == "rreport" else
                     "73" if to_me and kind in ("RR73", "RRR") else "call")
        if to_me and kind in ("report", "rreport"):
            self.session["their_report"] = d["msg"].split()[-1].lstrip("R")
        self.tries = 0
        self._log_rx(d)
        self._run()
        return await self.publish()

    async def halt(self, stop_tx: bool = True) -> dict:
        if self._task and self._task is not asyncio.current_task():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
        if self.session and self.session["state"] in ("running",):
            self.session["state"] = "aborted"
        self.kind, self.step = None, None
        if stop_tx:
            await self.tx.stop()
        return await self.publish()

    def _run(self) -> None:
        self._task = asyncio.create_task(self._loop())

    # -- decoder interface ------------------------------------------------------
    def early_range(self) -> Optional[tuple[int, int]]:
        """Early pass only while a sequence runs; narrow once the partner's
        frequency is known (0.4 s instead of 2-3 s)."""
        if not self.kind:
            return None
        if self.session and self.session.get("df"):
            lo = min(self.session["df"], self.tx_hz) - 150
            hi = max(self.session["df"], self.tx_hz) + 150
            return int(max(100, lo)), int(min(3500, hi))
        return int(self.digi.config["low_hz"]), int(self.digi.config["high_hz"])

    async def set_auto(self, on: bool, tx_hz: Optional[float] = None) -> dict:
        self.auto_answer = on
        if tx_hz:
            self.auto_tx_hz = clamp_tx_hz(tx_hz)
        return await self.publish()

    async def set_hunt(self, on: bool, tx_hz: Optional[float] = None) -> dict:
        self.hunt = on
        self.hunt_until = (math.inf if not self.hunt_minutes else time.time() + self.hunt_minutes * 60) if on else 0.0
        if tx_hz:
            self.auto_tx_hz = clamp_tx_hz(tx_hz)
        return await self.publish()

    async def clear_hunt_blocked(self) -> dict:
        self.hunt_blocked.clear()
        if self.on_hunt_block:
            self.on_hunt_block(self.hunt_blocked)
        return await self.publish()

    def _hunt_block(self, call: str) -> None:
        self.hunt_blocked[call] = time.time()
        while len(self.hunt_blocked) > HUNT_BLOCK_MAX:              # oldest out
            del self.hunt_blocked[min(self.hunt_blocked, key=self.hunt_blocked.get)]
        log.info("hunt: %s did not answer - not called again", call)
        if self.on_hunt_block:
            self.on_hunt_block(self.hunt_blocked)

    def hunt_pick(self, decodes: list[dict]) -> Optional[dict]:
        """The station Hunt calls next: a plain CQ (a directed one - CQ DX, CQ NA
        ... - may not mean us) from a call not worked on this band, not blocked,
        not already in a session today. A new DXCC first, then a DXCC new on
        this band, then the strongest. None without the worked-before index."""
        busy = {s["call"] for s in self.sessions if s["state"] in ("running", "done")}
        cands = []
        for d in decodes:
            call = (d.get("from") or "").upper()
            w = d.get("worked")
            if d.get("kind") != "CQ" or not call or call.startswith("<") or call == self.me:
                continue
            tok = d.get("msg", "").upper().split()
            if len(tok) < 2 or tok[1].strip("<>") != call:                    # directed CQ
                continue
            if call in self.hunt_blocked or call in busy or not w:
                continue
            new = {"band": not w.get("call_band"), "call": not w.get("call"),
                   "dxcc": not w.get("dxcc"), "dxcc_band": not w.get("dxcc_band")}.get(self.hunt_target, False)
            if not new or d.get("snr", -99) < self.hunt_min_snr:
                continue
            cands.append(d)
        if not cands:
            return None
        return max(cands, key=lambda d: (not d["worked"].get("dxcc"),
                                         not d["worked"].get("dxcc_band"), d.get("snr", -99)))

    async def on_decodes(self, decodes: list[dict], t0: float, mode: str, early: bool) -> None:
        if not self.kind and self.auto_answer and not early and t0 not in self.digi.tx_periods:
            callers = [d for d in decodes if (d.get("to") or "").upper() == self.me
                       and d.get("kind") in ("grid", "report", "rreport") and d.get("from")
                       and not d["from"].startswith("<")]
            if callers:
                d = max(callers, key=lambda x: x["snr"])                 # strongest caller
                log.info("auto answer: %s (%s)", d["from"], d["msg"])
                try:
                    await self.answer(d, self.auto_tx_hz)
                except RuntimeError as exc:
                    log.warning("auto answer %s: %s", d["from"], exc)
                return
        if not self.kind and self.hunt and not early and t0 not in self.digi.tx_periods:
            if time.time() > self.hunt_until:
                self.hunt = False
                log.info("hunt: %d min over, off", self.hunt_minutes)
                await self.publish()
            elif getattr(self.manager, "count", 1) and mode == self.digi.current_mode():
                d = self.hunt_pick(decodes)
                if d:
                    log.info("hunt: calling %s (%s)", d["from"], d["msg"])
                    try:
                        await self.answer(d, self.auto_tx_hz)
                        self.session["hunt"] = True
                        await self.publish()
                    except RuntimeError as exc:
                        log.warning("hunt %s: %s", d["from"], exc)
                    return
        if not self.kind or mode != self.mode or t0 in self.digi.tx_periods:
            return
        mine = [d for d in decodes if (d.get("to") or "").upper() == self.me]
        relevant = False                 # did this pass bring what the next TX depends on?
        if self.session:
            them = self.session["call"]
            for d in mine:
                if (d.get("from") or "").upper() == them:
                    relevant = True
                    self.session["answered"] = True
                    if not self._known(d):
                        self._log_rx(d)
                        self._advance(d)
        elif self.kind == "cq" and mine:
            callers = [d for d in mine if d.get("kind") in ("grid", "report", "call")]
            if callers:
                d = max(callers, key=lambda x: x["snr"])         # strongest caller
                self.session = self._new_session(d)
                self.step = "rreport" if d["kind"] == "report" else "report"
                if d["kind"] == "report":
                    self.session["their_report"] = d["msg"].split()[-1]
                self.tries = 0
                self._log_rx(d)
                relevant = True
        # A partial pass (early / partner) only settles the period when it
        # found the partner (or a caller); otherwise the full pass decides —
        # an early "nothing" must not make us repeat the old message.
        if early and not relevant:
            await self.publish()
            return
        self._seen[t0] = True
        for old in [k for k in self._seen if k < t0 - 120]:
            del self._seen[old]
        self._event.set()
        await self.publish()

    def _advance(self, d: dict) -> None:
        k, s = d.get("kind"), self.session
        if k == "report" and self.step in ("call", "report"):
            s["their_report"] = d["msg"].split()[-1]
            self.step, self.tries = "rreport", 0
        elif k == "rreport" and self.step in ("report", "rr73", "call", "done"):
            s["their_report"] = d["msg"].split()[-1].lstrip("R")
            self.step, self.tries = "rr73", 0          # (again) RR73
        elif k in ("RR73", "RRR") and self.step in ("rreport", "call", "73"):
            self.step, self.tries = "73", 0
        elif k in ("RR73", "RRR") and self.step == "done" and self._last_tx_was_73() \
                and self.tries < MAX_TRIES:
            # they repeat RRR / RR73: our 73 did not get through — send it again
            # (tries keep counting, so a partner that never hears us ends it)
            self.step = "73"
        elif k == "73":
            self._complete()
            if self.step in ("rr73", "done"):
                self.step = "done"
        elif k == "grid" and self.step == "report":
            s["grid"] = s.get("grid") or (d.get("grid") or "")

    # -- the loop ---------------------------------------------------------------
    async def _loop(self) -> None:
        try:
            while self.kind:
                period = MODES[self.mode]["period"]
                latest = FT_LATEST_START[self.mode]
                n = math.ceil((time.time() - latest + 0.3) / period)
                if n % 2 != self.parity:
                    n += 1
                t0 = n * period
                await asyncio.sleep(max(0.0, t0 - 0.05 - time.time()))
                # wait until the period before (the partner's) has been decoded
                prev = t0 - period
                deadline = t0 + latest - WAIT_MARGIN
                while not self._seen.get(prev) and time.time() < deadline:
                    self._event.clear()
                    try:
                        await asyncio.wait_for(self._event.wait(), max(0.05, deadline - time.time()))
                    except asyncio.TimeoutError:
                        break
                msg = self._next_message()
                if msg is None:
                    await self._finish_round()
                    continue
                self.digi.tx_periods.add(t0)

                async def on_air(offset: float, msg=msg, t0=t0) -> None:
                    # into My/Session Messages as soon as it is on the air
                    self._log_tx(msg, t0, {"start_offset": offset})
                    await self.publish()

                res = await self.tx.send_ft(msg, self.mode, self.tx_hz, t0, on_air=on_air)
                if res.get("sent"):
                    if self.step == "cq":
                        self.cq_count += 1
                    else:
                        self.tries += 1
                    if self.step in ("rr73", "73"):
                        # closing message sent: QSO complete. "done" sends nothing
                        # more — unless the partner repeats the R-report (missed
                        # our RR73), which puts the step back to "rr73".
                        self._complete()
                        self.step = "done"
                await self.publish()
                if self.step == "cq" and self.cq_count >= CQ_MAX:
                    log.info("CQ limit (%d) reached", CQ_MAX)
                    self.kind = None
                elif self.step not in ("cq", "done") and self.tries >= MAX_TRIES:
                    self.session["state"] = "no reply"
                    if self.session.get("hunt") and not self.session.get("answered"):
                        self._hunt_block(self.session["call"])
                    await self._finish_round()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("sequencer stopped: %s", exc)
            if self.session and self.session["state"] == "running":
                self.session["state"] = f"error: {exc}"
            self.kind = None
        finally:
            await self.publish()

    async def _finish_round(self) -> None:
        """Session over: CQ goes on calling, Answer stops."""
        if self.session:
            if self.session["state"] == "running":
                self.session["state"] = "done" if self.session.get("logged") is not None else "ended"
            self.session = None
        if self.kind == "cq":
            self.step, self.tries = "cq", 0
        else:
            self.kind = None

    def _next_message(self) -> Optional[str]:
        me, s = self.me, self.session
        if self.step == "cq":
            return f"CQ {me} {self.grid4}"
        if not s or self.step == "done":
            return None
        # once sent, a report is fixed — repeats and the log use the same value
        them, rep = s["call"], s.get("report_sent") or report(s["snr"])
        return {"call": f"{them} {me} {self.grid4}", "report": f"{them} {me} {rep}",
                "rreport": f"{them} {me} R{rep}", "rr73": f"{them} {me} {self.closing}",
                "73": f"{them} {me} 73"}.get(self.step)

    # -- sessions ---------------------------------------------------------------
    async def clear_sessions(self) -> dict:
        """Forget the session list; a QSO still in progress stays."""
        keep = [s for s in self.sessions if s is self.session and s["state"] == "running"]
        self.sessions.clear()
        self.sessions.extend(keep)
        return await self.publish()

    def _new_session(self, d: dict) -> dict:
        s = {"call": d["from"].upper(), "grid": d.get("grid") or "", "df": d.get("df"),
             "snr": d.get("snr", 0), "dxcc": d.get("dxcc"), "mode": d.get("mode") or self.mode,
             "started": time.time(), "ended": None, "state": "running", "tries": 0,
             "their_report": None, "my_report": report(d.get("snr", 0)), "logged": None,
             "msgs": [], "freq_hz": None}
        self.sessions.appendleft(s)
        return s

    def _known(self, d: dict) -> bool:
        key = (d.get("t0"), d.get("msg"))
        return any((m.get("t0"), m.get("msg")) == key for m in self.session["msgs"] if m["dir"] == "rx")

    def _log_rx(self, d: dict) -> None:
        if self.session:
            self.session["msgs"].append({"dir": "rx", "t0": d.get("t0"), "utc": d.get("period_utc"),
                                         "snr": d.get("snr"), "dt": d.get("dt"), "df": d.get("df"),
                                         "msg": d.get("msg")})
            self.session["snr"] = d.get("snr", self.session["snr"])
            self.session["my_report"] = report(self.session["snr"])
            if d.get("grid"):
                self.session["grid"] = d["grid"]

    def _log_tx(self, msg: str, t0: float, res: dict) -> None:
        utc = datetime.fromtimestamp(t0, timezone.utc).strftime("%H:%M:%S")
        self.last_tx = {"msg": msg, "utc": utc, "df": self.tx_hz, "start_offset": res.get("start_offset")}
        if self.session:
            if self.step in ("report", "rreport") and not self.session.get("report_sent"):
                self.session["report_sent"] = report(self.session["snr"])
            self.session["msgs"].append({"dir": "tx", "t0": t0, "utc": utc, "df": self.tx_hz, "msg": msg})
            self.session["tries"] = self.tries + 1

    def _last_tx_was_73(self) -> bool:
        tx = [m for m in (self.session or {}).get("msgs", []) if m["dir"] == "tx"]
        return bool(tx) and tx[-1]["msg"].split()[-1] == "73"

    def _complete(self) -> None:
        s = self.session
        if not s or s.get("logged") is not None:
            return
        s["ended"] = time.time()
        s["state"] = "done"
        dial = (self.tx.service.status.get("vfo") or {}).get("freq_hz")
        s["freq_hz"] = int(dial + self.tx_hz) if dial else None
        q = {"call": s["call"], "gridsquare": s["grid"], "rst_sent": s.get("report_sent") or s["my_report"],
             "rst_rcvd": s["their_report"] or "", "mode": s["mode"], "freq_hz": s["freq_hz"],
             "tx_pwr": self.tx.service.status.get("power"),
             "qso_date": datetime.fromtimestamp(s["started"], timezone.utc).strftime("%Y%m%d"),
             "time_on": datetime.fromtimestamp(s["started"], timezone.utc).strftime("%H%M%S"),
             "time_off": datetime.fromtimestamp(s["ended"], timezone.utc).strftime("%H%M%S")}
        s["qso"] = q
        # worked from now on (red in the lists) — even before / without a log
        # entry; only in memory: the saved index stays what was really logged
        band = band_for_hz(s["freq_hz"]) if s["freq_hz"] else ""
        try:
            self.logbook.worked.add(s["call"], band)
        except Exception as exc:  # noqa: BLE001 — colouring only
            log.warning("worked mark for %s failed: %s", s["call"], exc)
        if self.logbook.auto_log:
            s["logged"] = "running"
            asyncio.create_task(self._autolog(s, q))
        else:
            s["logged"] = False                 # offered to the log form

    async def _autolog(self, s: dict, q: dict) -> None:
        try:
            r = await asyncio.to_thread(self.logbook.log_qso, q, station_callsign=self.me,
                                        my_grid=self.settings.my_grid, automatic=True)
            s["logged"] = bool(r.get("ok"))
            s["log_message"] = r.get("message")
        except LogError as exc:
            s["logged"], s["log_message"] = False, str(exc)
        await self.publish()

    # -- status -------------------------------------------------------------------
    def status(self) -> dict:
        def view(s: dict) -> dict:
            end = s["ended"] or time.time()
            return {**{k: v for k, v in s.items() if k != "qso"}, "duration_s": round(end - s["started"]),
                    "band": band_for_hz(s.get("freq_hz") or ((self.tx.service.status.get("vfo") or {}).get("freq_hz")))}
        return {"kind": self.kind, "step": self.step, "mode": self.mode, "tx_hz": self.tx_hz,
                "parity": "EVEN" if self.parity == 0 else "ODD", "tries": self.tries,
                "cq_count": self.cq_count, "next": self._next_message() if self.kind else None,
                "last_tx": self.last_tx, "session": view(self.session) if self.session else None,
                "sessions": [view(s) for s in list(self.sessions)[:20]],
                "auto_log": self.logbook.auto_log, "auto_answer": self.auto_answer,
                "hunt": self.hunt,
                "hunt_left_s": (None if math.isinf(self.hunt_until) else max(0, round(self.hunt_until - time.time())))
                if self.hunt else 0,                       # None: no switch-off
                "hunt_blocked": sorted(self.hunt_blocked)}

    async def publish(self) -> dict:
        st = self.status()
        await self.manager.broadcast({"type": "seq", **st})
        return st
