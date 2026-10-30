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
import re
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
PARTNER_SPAN_MAX = 1000    # wider than this, the fast partner pass looks at the partner only
# Room around the partner for the narrow passes. Measured with jt9 on recorded
# periods: FT8 ±150 Hz lost 2 of 54 decodes, ±200 none; FT4 needs far more
# (±150 Hz found 1 of 24, ±250 all 24) - its decoder wants margin above the tones.
PARTNER_MARGIN = {"FT8": 200, "FT4": 300}
HUNT_MAX_S = 180 * 60       # default; Settings ▸ FT8 ▸ Hunt, 0 = no switch-off
HUNT_BLOCK_MAX = 1000       # calls kept on the no-reply list
# CQ: callers that were not taken (two at once, or calling during a QSO) wait
# here and are answered after the QSO instead of the next CQ - for this long
# after they were last heard, unless heard with someone else before.
QUEUE_MAX_AGE = 120
QUEUE_KINDS = ("grid", "report", "call")
# A caller decoded while our CQ is already on the air: the CQ is cut and the
# answer goes out in the same period - if it can still start this much before
# the latest start (FT_LATEST_START) once the CQ is stopped and PTT re-keyed.
PREEMPT_MARGIN = 0.3


def report(snr: int) -> str:
    """WSJT-X report format: -05, +12 (clamped to the encodable -30…+49)."""
    snr = max(-30, min(49, int(snr)))
    return f"{'-' if snr < 0 else '+'}{abs(snr):02d}"


def std_call(call: str) -> bool:
    """A call FT8/FT4 can carry in full in a standard message (WSJT-X's stdcall
    in packjt77): up to 6 characters, the call area digit at position 2 or 3
    with at least one letter before it and at most 3 letters after; /P and /R
    are allowed on top."""
    c = re.sub(r"/[PR]$", "", call.upper())
    if "/" in c or not 3 <= len(c) <= 6 or not c.isalnum():
        return False
    area = next((i for i in range(len(c) - 1, 0, -1) if c[i].isdigit()), -1)
    pre, suf = c[:area], c[area + 1:]
    return (area in (1, 2) and any(ch.isalpha() for ch in pre)
            and sum(ch.isdigit() for ch in pre) < area and suf.isalpha() and len(suf) <= 3)


def ft_call(call: str) -> str:
    """The partner's call as it goes into my messages: a non-standard call
    (DL1ABC/QRP, PA/DJ0SH) as a hash, <DL1ABC/QRP>, the way WSJT-X sends it —
    in full it would not fit and ft8code would cut it to DL1ABC."""
    return call if std_call(call) else f"<{call}>"


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
        self.cq_to = ""                          # directed CQ: "DX", "EU", "POTA" … ("" = plain CQ)
        self.mode = "FT8"
        self.parity = 0                          # my TX periods: index % 2 == parity
        # Auto answer: while idle, a station calling me (grid / report /
        # R-report to my call) is answered without a click, on auto_tx_hz.
        self.auto_answer = False
        self.auto_tx_hz: float = 1500.0
        # AF (adopt frequency, the DIGI strip's switch): Auto Answer / Auto Hunt
        # transmit on the partner's frequency instead of the TX box
        self.adopt_freq = True
        self.tx_hz = 1500.0
        self.last_tx: Optional[dict] = None
        # Hunt: while idle, call a new station's CQ (not worked on this band),
        # wait for the QSO to end; a station that never answered goes on the
        # blocked list and is not called again (persisted by main.py).
        self.hunt = False
        self.hunt_until = 0.0
        # call -> {"t": when, "dxcc", "snr", "band", "grid", "df", "mode", "tries"} (manual: only t)
        self.hunt_blocked: dict[str, dict] = {}
        self.on_hunt_block = None                         # callback(dict) to persist the list
        # Settings ▸ FT8 ▸ Hunt
        self.hunt_target = "band"       # band: not worked on this band | call: never worked |
                                        # dxcc: DXCC never worked | dxcc_band: DXCC new on this band
        self.hunt_min_snr = -24         # weaker CQs are passed over
        self.hunt_minutes = HUNT_MAX_S // 60   # 0: Hunt never switches itself off
        self._seen: dict[float, bool] = {}       # period t0 → decodes processed
        # CQ caller queue: call -> {"d": latest decode to me, "t": last heard, "first": first heard}
        self.queue: dict[str, dict] = {}
        self.queue_first: Optional[str] = None   # moved to the front by hand (a click)
        self._airing: Optional[dict] = None      # {"t0", "step"} of the message on the air
        self._preempt: Optional[float] = None    # period whose CQ was cut for an answer
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

    async def start_cq(self, tx_hz: float, cq_to: str = "") -> dict:
        """CQ, optionally directed: "CQ DX …", "CQ EU …", "CQ POTA …" (FT8/FT4
        standard messages allow up to four letters after CQ)."""
        self._require()
        self.cq_to = (cq_to or "").strip().upper()
        await self.halt(stop_tx=False)
        self.mode, self.tx_hz = self.digi.current_mode(), clamp_tx_hz(tx_hz)
        period = MODES[self.mode]["period"]
        self.parity = (math.floor(time.time() / period) + 1) % 2    # the next period
        self.kind, self.step, self.tries, self.cq_count, self.session = "cq", "cq", 0, 0, None
        self.queue.clear(); self.queue_first = None
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
        if not d.get("manual"):                             # a DX Call typed in was never received
            self._log_rx(d)
        # The decode we answer is the partner's period, already evaluated: the
        # loop need not wait for it (it was decoded before the sequence ran,
        # so on_decodes never marked it), so a click early in our own period
        # still goes out in that period instead of waiting until 1.55 s.
        if d.get("t0") is not None:
            self._seen[d["t0"]] = True
        self._run()
        return await self.publish()

    async def call_station(self, call: str, grid: str, tx_hz: float, parity: str = "auto") -> dict:
        """DX Call: call a station typed in by hand. With a recent decode of it,
        that decode is answered (its frequency, SNR and period, as a
        double-click); without one we call in the next period, or in the even /
        odd one asked for, with a -10 report until we hear it."""
        self._require()
        call, grid = call.strip().upper(), grid.strip().upper()[:4]
        if call == self.me:
            raise RuntimeError(f"{call} is your own callsign")
        if not re.match(r"^[A-R]{2}\d{2}$", grid):        # optional and only shown: a bad one is dropped
            grid = ""
        mode = self.digi.current_mode()
        period = MODES[mode]["period"]
        heard = [d for d in self.digi.decodes
                 if (d.get("from") or "").upper() == call and d.get("mode") == mode]
        if heard and parity == "auto":
            return await self.answer(heard[-1], tx_hz)
        n = math.floor(time.time() / period)               # the current period
        want = {"even": 0, "odd": 1}.get(parity)
        ours = n + 1 if want is None else (n + 1 if (n + 1) % 2 == want else n + 2)
        d = {"msg": f"CQ {call} {grid}".strip(), "kind": "CQ", "from": call, "to": None,
             "grid": grid, "t0": (ours - 1) * period, "snr": heard[-1]["snr"] if heard else -10,
             "df": heard[-1]["df"] if heard else round(tx_hz), "dt": 0.0, "mode": mode,
             "dxcc": self.digi.dxcc.lookup(call) if hasattr(self.digi, "dxcc") else None,
             "manual": True}                                # typed in, never received
        return await self.answer(d, tx_hz)

    async def halt(self, stop_tx: bool = True) -> dict:
        if self._task and self._task is not asyncio.current_task():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
        if self.session and self.session["state"] in ("running",):
            self.session["state"] = "aborted"
        self.kind, self.step = None, None
        self.queue.clear(); self.queue_first = None
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
            df = self.session["df"]
            m = PARTNER_MARGIN.get(self.mode, 200)
            lo = min(df, self.tx_hz) - m
            hi = max(df, self.tx_hz) + m
            if hi - lo > PARTNER_SPAN_MAX:
                # partner far from our TX frequency: the partner answers on its own
                # frequency, so decode around it only. (The span over both was so
                # wide that the digi service skipped the fast partner pass, and every
                # reply came only with the full pass - after our TX had started.)
                lo, hi = df - m, df + m
            return int(max(100, lo)), int(min(3500, hi))
        return int(self.digi.config["low_hz"]), int(self.digi.config["high_hz"])

    def _auto_hz(self, d: dict) -> float:
        """TX frequency for a QSO started by Auto Answer or Auto Hunt: the
        partner's own frequency while AF (adopt frequency) is on, else the TX box."""
        if self.adopt_freq and d.get("df"):
            return clamp_tx_hz(d["df"])
        return self.auto_tx_hz

    def _set_common(self, tx_hz: Optional[float], adopt: Optional[bool]) -> None:
        if tx_hz:
            self.auto_tx_hz = clamp_tx_hz(tx_hz)
        if adopt is not None:
            self.adopt_freq = adopt

    async def set_auto(self, on: bool, tx_hz: Optional[float] = None, adopt: Optional[bool] = None) -> dict:
        self.auto_answer = on
        self._set_common(tx_hz, adopt)
        return await self.publish()

    async def set_hunt(self, on: bool, tx_hz: Optional[float] = None, adopt: Optional[bool] = None) -> dict:
        if on and not self.hunt:                     # switching on starts the clock; already on keeps it
            self.hunt_until = math.inf if not self.hunt_minutes else time.time() + self.hunt_minutes * 60
        elif not on:
            self.hunt_until = 0.0
        self.hunt = on
        self._set_common(tx_hz, adopt)
        return await self.publish()

    async def clear_hunt_blocked(self) -> dict:
        self.hunt_blocked.clear()
        if self.on_hunt_block:
            self.on_hunt_block(self.hunt_blocked)
        return await self.publish()

    async def set_hunt_blocked(self, call: str, blocked: bool) -> dict:
        """Edit the no-reply list by hand (Settings ▸ FT8 ▸ Hunt)."""
        call = call.strip().upper()
        if blocked:
            self._hunt_block(call, {"manual": True})
        elif self.hunt_blocked.pop(call, None) is not None and self.on_hunt_block:
            self.on_hunt_block(self.hunt_blocked)
        return await self.publish()

    def _hunt_block(self, call: str, info: Optional[dict] = None) -> None:
        self.hunt_blocked[call] = {"t": time.time(), **(info or {})}
        while len(self.hunt_blocked) > HUNT_BLOCK_MAX:              # oldest out
            del self.hunt_blocked[min(self.hunt_blocked, key=lambda c: self.hunt_blocked[c]["t"])]
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

    async def on_decodes(self, decodes: list[dict], t0: float, mode: str, early: bool,
                         partner: bool = False) -> None:
        if not self.kind and self.auto_answer and not early and t0 not in self.digi.tx_periods:
            callers = [d for d in decodes if (d.get("to") or "").upper() == self.me
                       and d.get("kind") in ("grid", "report", "rreport") and d.get("from")
                       and not d["from"].startswith("<")]
            if callers:
                d = max(callers, key=lambda x: x["snr"])                 # strongest caller
                log.info("auto answer: %s (%s)", d["from"], d["msg"])
                try:
                    await self.answer(d, self._auto_hz(d))
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
                        await self.answer(d, self._auto_hz(d))
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
            # the partner's other messages (their CQ, their QSO with someone
            # else): into Session Messages too, for the picture - they do not
            # count as an answer and change nothing in the sequence
            for d in decodes:
                if ((d.get("from") or "").upper() == them and (d.get("to") or "").upper() != self.me
                        and not self._known(d)):
                    self._log_rx(d, other=True)
        if self.kind == "cq":
            self._queue_update(decodes)
            if not self.session and self.step == "cq" and self._take_queued():
                relevant = True
                await self._preempt_cq(t0)
        # A partial pass (early / partner) only settles the period when it
        # found the partner (or a caller); otherwise the full pass decides —
        # an early "nothing" must not make us repeat the old message.
        # The partner pass (partner=True) searched the partner's range on the
        # complete slice: nothing from them there settles the period as well -
        # we repeat at once instead of waiting for the full pass (1.5-2.5 s, so
        # every repeat went out ~1.9 s late). The 13.4 s early pass does not.
        if early and not relevant and not (partner and self.session):
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

    # -- CQ caller queue -----------------------------------------------------------
    def _queue_update(self, decodes: list[dict]) -> None:
        """Callers to me other than the partner join (or refresh) the queue; a
        queued call heard with anyone else (their CQ, another QSO) leaves it."""
        them = self.session["call"] if self.session else None
        recent = {x["call"] for x in self.sessions
                  if x["state"] == "done" and (x["ended"] or 0) > time.time() - 600}
        now = time.time()
        for d in decodes:
            frm = (d.get("from") or "").upper()
            if not frm or frm.startswith("<") or frm == self.me or frm == them:
                continue
            if (d.get("to") or "").upper() == self.me:
                if d.get("kind") in QUEUE_KINDS and frm not in recent:
                    q = self.queue.get(frm)
                    self.queue[frm] = {"d": d, "t": now, "first": q["first"] if q else now}
            elif frm in self.queue:
                log.info("queue: %s now with someone else (%s) - dropped", frm, d.get("msg"))
                self.queue.pop(frm, None)
                if self.queue_first == frm:
                    self.queue_first = None

    def _queue_rank(self, call: str) -> tuple:
        """Front of the queue: picked by hand, then a new DXCC, a DXCC new on
        this band, a call not worked on this band, then the strongest."""
        d = self.queue[call]["d"]
        w = d.get("worked") or {}
        known = bool(w)
        return (call == self.queue_first, known and not w.get("dxcc"),
                known and not w.get("dxcc_band"), known and not w.get("call_band"), d.get("snr", -99))

    def _queue_order(self) -> list[str]:
        cut = time.time() - QUEUE_MAX_AGE
        for c in [c for c, q in self.queue.items() if q["t"] < cut]:
            log.info("queue: %s not heard for %d s - dropped", c, QUEUE_MAX_AGE)
            del self.queue[c]
        if self.queue_first not in self.queue:
            self.queue_first = None
        return sorted(self.queue, key=self._queue_rank, reverse=True)

    def _take_queued(self) -> bool:
        """CQ with no QSO running: start the QSO with the front caller."""
        order = self._queue_order()
        if not order:
            return False
        call = order[0]
        d = self.queue.pop(call)["d"]
        if self.queue_first == call:
            self.queue_first = None
        self.session = self._new_session(d)
        self.step = "rreport" if d["kind"] == "report" else "report"
        if d["kind"] == "report":
            self.session["their_report"] = d["msg"].split()[-1]
        self.tries = 0
        self._log_rx(d)
        log.info("CQ: answering %s (%s), %d more waiting", call, d.get("msg"), len(self.queue))
        return True

    async def queue_to_front(self, call: str) -> dict:
        call = call.strip().upper()
        if call not in self.queue:
            raise RuntimeError(f"{call} is not waiting")
        self.queue_first = call
        return await self.publish()

    async def _preempt_cq(self, t0_decoded: float) -> None:
        """A caller found while our CQ is on the air: cut the CQ if the answer
        can still go out in this period (the loop sends it at once)."""
        a = self._airing
        if not a or a["step"] != "cq":
            return
        late = time.time() - a["t0"]
        if late > FT_LATEST_START[self.mode] - PREEMPT_MARGIN:
            return
        log.info("caller decoded %.2f s into our CQ: CQ cut, answer in this period", late)
        self._preempt = a["t0"]
        await self.tx.stop()

    # -- the loop ---------------------------------------------------------------
    async def _loop(self) -> None:
        try:
            again: Optional[float] = None       # CQ cut for an answer: same period once more
            while self.kind:
                period = MODES[self.mode]["period"]
                latest = FT_LATEST_START[self.mode]
                n = math.ceil((time.time() - latest + 0.3) / period)
                if n % 2 != self.parity:
                    n += 1
                t0 = again if again is not None else n * period
                again = None
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
                # when the next message was chosen, and whether the partner's
                # period had been decoded by then (a late decision = late TX start)
                log.info("TX decision %+.2f s into the period, partner period %s: %s",
                         time.time() - t0, "decoded" if self._seen.get(prev) else "NOT decoded (deadline)",
                         msg or "-")
                if msg is None:
                    await self._finish_round()
                    continue
                self.digi.tx_periods.add(t0)
                sent_step = self.step       # the step this message belongs to
                # Session Messages: the message is entered as soon as it is
                # decided ("pending"), before PTT; on the air it becomes a
                # normal TX line, not sent it stays as "skipped". A CQ belongs
                # to no session (also not to one a caller starts meanwhile).
                entry = None
                if self.session and sent_step != "cq":
                    if sent_step in ("report", "rreport") and not self.session.get("report_sent"):
                        self.session["report_sent"] = report(self.session["snr"])   # = what msg carries
                    entry = {"dir": "tx", "t0": t0, "utc": self._utc(t0), "df": self.tx_hz,
                             "msg": msg, "pending": True}
                    self.session["msgs"].append(entry)
                    await self.publish()

                async def on_air(offset: float, msg=msg, t0=t0, entry=entry) -> None:
                    # into My Messages as soon as it is on the air
                    self._log_tx(msg, t0, {"start_offset": offset}, entry=entry)
                    await self.publish()

                self._airing = {"t0": t0, "step": sent_step}
                try:
                    res = await self.tx.send_ft(msg, self.mode, self.tx_hz, t0, on_air=on_air)
                finally:
                    self._airing = None
                    if entry is not None and entry.pop("pending", None):
                        entry["skipped"] = True          # never keyed: too late, halted, refused
                if self._preempt == t0:
                    self._preempt = None
                    if self.session and self.step != "cq":
                        again = t0               # the cut CQ is not counted
                        continue
                self._after_send(sent_step, res)
                await self.publish()
                if self.step == "cq" and self.cq_count >= CQ_MAX:
                    log.info("CQ limit (%d) reached", CQ_MAX)
                    self.kind = None
                elif self.step not in ("cq", "done") and self.tries >= MAX_TRIES:
                    self.session["state"] = "no reply"
                    s = self.session
                    if s.get("hunt") and not s.get("answered"):
                        vfo = (self.tx.service.status.get("vfo") or {}).get("freq_hz")
                        self._hunt_block(s["call"], {
                            "dxcc": s.get("dxcc"), "snr": s.get("snr"), "grid": s.get("grid") or "",
                            "df": s.get("df"), "mode": s.get("mode"), "tries": self.tries,
                            "band": band_for_hz(s.get("freq_hz") or vfo)})
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

    def _after_send(self, sent_step: Optional[str], res: dict) -> None:
        """Book a transmission: tries / CQ count, and the QSO's end after the
        closing message - judged by the step the message belonged to."""
        if res.get("sent") and self.step != sent_step:
            # The partner's reply was decoded while this (older) message
            # was on the air, and the step moved on: what we sent is not
            # the new step's message - it goes out next period. Counting
            # it as sent once ended a QSO without our 73 (RR73 came in
            # during a repeated R-report, the loop took that as "73 sent").
            log.info("reply arrived during TX (%s -> %s): next message next period",
                     sent_step, self.step)
        elif res.get("sent"):
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

    async def _finish_round(self) -> None:
        """Session over: CQ goes on calling, Answer stops."""
        if self.session:
            if self.session["state"] == "running":
                self.session["state"] = "done" if self.session.get("logged") is not None else "ended"
            self.session = None
        if self.kind == "cq":
            self.step, self.tries = "cq", 0
            self._take_queued()               # a waiting caller instead of the next CQ
        else:
            self.kind = None

    def _next_message(self) -> Optional[str]:
        me, s = self.me, self.session
        if self.step == "cq":
            return f"CQ {self.cq_to + ' ' if self.cq_to else ''}{me} {self.grid4}"
        if not s or self.step == "done":
            return None
        # once sent, a report is fixed — repeats and the log use the same value
        them, rep = ft_call(s["call"]), s.get("report_sent") or report(s["snr"])
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

    def _log_rx(self, d: dict, other: bool = False) -> None:
        if self.session:
            m = {"dir": "rx", "t0": d.get("t0"), "utc": d.get("period_utc"),
                 "snr": d.get("snr"), "dt": d.get("dt"), "df": d.get("df"), "msg": d.get("msg")}
            if other:                        # the partner, but not to me: shown, nothing else
                m["other"] = True
                self.session["msgs"].append(m)
                return
            if (d.get("to") or "").upper() != self.me:
                m["other"] = True            # the decode that started it (their CQ, their RR73 to someone)
            self.session["msgs"].append(m)
            self.session["snr"] = d.get("snr", self.session["snr"])
            self.session["my_report"] = report(self.session["snr"])
            if d.get("grid"):
                self.session["grid"] = d["grid"]

    @staticmethod
    def _utc(t0: float) -> str:
        return datetime.fromtimestamp(t0, timezone.utc).strftime("%H:%M:%S")

    _NEW = object()

    def _log_tx(self, msg: str, t0: float, res: dict, entry=_NEW) -> None:
        """On the air. ``entry``: the session's pending line (made a normal TX
        line), None for a message that belongs to no session (CQ); left out,
        a new line is added (direct calls)."""
        utc = self._utc(t0)
        self.last_tx = {"msg": msg, "utc": utc, "df": self.tx_hz, "start_offset": res.get("start_offset")}
        if self.session and entry is not None:
            if self.step in ("report", "rreport") and not self.session.get("report_sent"):
                self.session["report_sent"] = report(self.session["snr"])
            if entry is self._NEW:
                self.session["msgs"].append({"dir": "tx", "t0": t0, "utc": utc, "df": self.tx_hz, "msg": msg})
            else:
                entry.pop("pending", None)
                entry["start_offset"] = res.get("start_offset")
            self.session["tries"] = self.tries + 1

    def _last_tx_was_73(self) -> bool:
        tx = [m for m in (self.session or {}).get("msgs", [])
              if m["dir"] == "tx" and not m.get("pending") and not m.get("skipped")]
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
        # auto-log: Wavelog (Sessions ▸ Auto-log) and the QRZ.com logbook
        # (Settings ▸ Log), each on its own
        to = tuple(t for t, on in (("wavelog", self.logbook.auto_log),
                                   ("qrz", getattr(self.logbook, "auto_log_qrz", False))) if on)
        if to:
            s["logged"] = "running"
            asyncio.create_task(self._autolog(s, q, to))
        else:
            s["logged"] = False                 # offered to the log form

    async def _autolog(self, s: dict, q: dict, to: tuple = ("wavelog",)) -> None:
        try:
            r = await asyncio.to_thread(self.logbook.log_qso, q, station_callsign=self.me,
                                        my_grid=self.settings.my_grid, automatic=True, to=to)
            oks, msgs = [], []
            if "wavelog" in to:
                oks.append(bool(r.get("ok")))
                msgs.append(f"Wavelog: {r.get('message')}")
            if "qrz" in to:
                qr = r.get("qrz")
                oks.append(bool(qr and qr.get("ok")))
                msgs.append(qr["message"] if qr else "QRZ.com logbook not set up (Settings ▸ Log: API key)")
            s["logged"] = all(oks)
            s["log_message"] = " · ".join(msgs)
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
                "cq_count": self.cq_count, "cq_to": self.cq_to, "next": self._next_message() if self.kind else None,
                "last_tx": self.last_tx, "session": view(self.session) if self.session else None,
                "sessions": [view(s) for s in list(self.sessions)[:20]],
                "auto_log": self.logbook.auto_log, "auto_answer": self.auto_answer,
                "hunt": self.hunt, "adopt_freq": self.adopt_freq,
                "hunt_left_s": (None if math.isinf(self.hunt_until) else max(0, round(self.hunt_until - time.time())))
                if self.hunt else 0,                       # None: no switch-off
                "hunt_blocked": sorted(self.hunt_blocked),
                "queue": self._queue_view() if self.kind == "cq" else []}

    def _queue_view(self) -> list[dict]:
        now = time.time()
        out = []
        for c in self._queue_order():
            q = self.queue[c]; d = q["d"]
            out.append({"call": c, "msg": d.get("msg"), "snr": d.get("snr"), "df": d.get("df"),
                        "dxcc": d.get("dxcc"), "first": q["first"], "age_s": round(now - q["t"]),
                        "front": c == self.queue_first})
        return out

    async def publish(self) -> dict:
        st = self.status()
        await self.manager.broadcast({"type": "seq", **st})
        return st
