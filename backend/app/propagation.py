"""HF band conditions for the station's own locator, from real VOACAP.

The strip under the DIGI waterfall shows four band pairs (80-40, 30-20, 17-15,
12-10) in green / yellow / red, with a sun or a moon for day or night at the
station, the way iDigi shows them - plus 6 m on its own. The values here are not a published
summary: VOACAP (voacapl, Debian package ``voacapl``) computes them for *this*
locator. The deck format, parser and REL handling follow CQ-DJ0SH/voacap-matrix.

VOACAP predicts a path, not a place, so "how open is 20 m from here" is put to
a ring of paths: 12 bearings x 1500 / 4000 / 9000 km = 36 circuits, short and
long hops in every direction. Per band and UTC hour the reliability is the
mean over that ring, and a pair is as good as its better band - the one you
would switch to.

One voacapl run covers all 24 UTC hours of the month's median ionosphere, so
the day is computed once (about 0.5 s on a Pi 5) and the current hour is a
lookup. It is computed again when the UTC date, the locator or the sunspot
number changes. SSN: NOAA SWPC's latest monthly value, fetched once a day; the
last good one is kept in backend/propagation.json for when the Pi is offline.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import logging
import math
import os
import re
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path

log = logging.getLogger("ft950.prop")

BACKEND = Path(__file__).resolve().parents[1]
ITSHFBC = BACKEND / "itshfbc"                  # voacapl's working tree (gitignored)
CACHE = BACKEND / "propagation.json"          # last good SSN (gitignored)
SSN_URL = "https://services.swpc.noaa.gov/json/solar-cycle/observed-solar-cycle-indices.json"
SSN_FALLBACK = 60.0

BANDS = [(80, 3.65), (40, 7.10), (30, 10.10), (20, 14.10),
         (17, 18.10), (15, 21.20), (12, 24.90), (10, 28.50), (6, 50.30)]
# 6 m stands alone: VOACAP models the F2 layer only, so it shows the rare F2
# openings of a solar maximum - not sporadic-E, which is what opens 6 m in a
# European summer. The UI says so on the chip.
GROUPS = [("80-40", (80, 40)), ("30-20", (30, 20)),
          ("17-15", (17, 15)), ("12-10", (12, 10)), ("6", (6,))]
GOOD, FAIR = 0.60, 0.30                       # mean ring REL -> green / yellow / red
RING_KM = (1500, 4000, 9000)
RING_STEP_DEG = 30

# The station as VOACAP sees it: 100 W, FT8 (13 dB-Hz needed, i.e. WSJT-X
# -21 dB in 2500 Hz), an isotropic antenna - no antenna data is known, and a
# guessed pattern would be worse than none. Quiet rural noise, as in
# voacap-matrix.
POWER_KW = 0.10
REQ_SNR = 13.0
NOISE_DBW = 145.0
ANTENNA = "default/isotrope"


# -- geometry --------------------------------------------------------------------
def grid_latlon(grid: str) -> tuple[float, float]:
    g = (grid or "").strip().upper()
    if len(g) < 4 or not re.match(r"^[A-R]{2}\d{2}([A-X]{2})?", g):
        raise ValueError(f"not a Maidenhead locator: {grid!r}")
    lon = (ord(g[0]) - 65) * 20 - 180 + int(g[2]) * 2
    lat = (ord(g[1]) - 65) * 10 - 90 + int(g[3])
    if len(g) >= 6:
        lon += (ord(g[4]) - 65) * 5 / 60 + 2.5 / 60
        lat += (ord(g[5]) - 65) * 2.5 / 60 + 1.25 / 60
    else:
        lon, lat = lon + 1.0, lat + 0.5
    return lat, lon


def destination(lat: float, lon: float, bearing: float, km: float) -> tuple[float, float]:
    d, b = km / 6371.0, math.radians(bearing)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1),
                         math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540) % 360 - 180


def solar_elevation(lat: float, lon: float, when: dt.datetime) -> float:
    day = when.timetuple().tm_yday
    hour = when.hour + when.minute / 60 + when.second / 3600
    decl = math.radians(23.44 * math.sin(math.radians(360 / 365 * (day - 81))))
    ha = math.radians(15 * (hour + lon / 15.0 - 12))
    la = math.radians(lat)
    s = math.sin(la) * math.sin(decl) + math.cos(la) * math.cos(decl) * math.cos(ha)
    return math.degrees(math.asin(max(-1.0, min(1.0, s))))


# -- VOACAP deck and output (fixed-column Fortran cards, see voacap-matrix) -------
def _lat(v: float, w: int) -> str:
    return f"{abs(v):{w}.2f}{'N' if v >= 0 else 'S'}"


def _lon(v: float) -> str:
    return f"{abs(v):9.2f}{'E' if v >= 0 else 'W'}"


def build_deck(tx: tuple[float, float], rx: tuple[float, float], ssn: float,
               when: dt.datetime) -> str:
    ant = ANTENNA.ljust(21)
    antenna = lambda n: (f"ANTENNA   {n:5d}{1:5d}{2:5d}{30:5d}{0.0:10.3f}"
                         f"[{ant}]{0.0:5.1f}{1.0:10.4f}")
    freqs = [f for _, f in BANDS] + [0.0] * (11 - len(BANDS))
    return "\n".join([
        "COMMENT    ft950-remote propagation",
        "LINEMAX      55",
        "COEFFS    CCIR",
        "TIME          1   24    1    1",
        f"MONTH      {when.year}{when.month:5.2f}",
        f"SUNSPOT   {ssn:5.1f}",
        f"LABEL     {'Home':20}{'Ring':20}",
        "CIRCUIT   " + _lat(tx[0], 5) + _lon(tx[1]) + _lat(rx[0], 9) + _lon(rx[1]) + "  S     0",
        f"SYSTEM    {POWER_KW:5.2f}{NOISE_DBW:5.0f}{0.10:5.2f}{90.0:5.0f}"
        f"{REQ_SNR:5.2f}{3.00:5.2f}{0.10:5.2f}",
        "FPROB      1.00 1.00 1.00 0.00",
        antenna(1), antenna(2),
        "FREQUENCY " + "".join(f"{f:5.2f}" for f in freqs),
        "METHOD       30    0",
        "EXECUTE", "QUIT", ""])


def parse_rel(text: str) -> dict[int, list[float]]:
    """{utc hour: [REL per band]} from a Method-30 output. Each hour block opens
    with '<h>.0 ... FREQ' and holds one line ending in 'REL'; its first value
    is VOACAP's own default frequency, the next ones are ours."""
    out: dict[int, list[float]] = {}
    hour = None
    for line in text.splitlines():
        m = re.match(r"^\s*(\d+)\.0\s+\S+\s+.*FREQ\s*$", line)
        if m:
            hour = int(m.group(1)) % 24
            continue
        s = line.strip()
        if hour is not None and s.endswith("REL"):
            vals = []
            for t in s[:-3].split():
                try:
                    vals.append(0.0 if t == "-" else float(t))
                except ValueError:
                    pass
            if len(vals) >= 1 + len(BANDS):
                out[hour] = vals[1:1 + len(BANDS)]
            hour = None
    if len(out) < 24:
        raise RuntimeError(f"VOACAP output incomplete: {len(out)}/24 hours")
    return out


# -- the engine ------------------------------------------------------------------
def _itshfbc() -> Path:
    """voacapl needs a writable tree of its own (it writes into run/);
    makeitshfbc copies one from /usr/share/voacapl into $HOME/itshfbc."""
    if not (ITSHFBC / "run").is_dir():
        mk = shutil.which("makeitshfbc")
        if not mk:
            raise RuntimeError("makeitshfbc not found (apt install voacapl)")
        subprocess.run([mk], env={**os.environ, "HOME": str(BACKEND)},
                       check=True, capture_output=True, timeout=60)
    return ITSHFBC


# voacapl takes the deck's file name into a 20-character Fortran field. A
# longer name does not fail: the run goes ahead with whatever antenna table is
# lying in run/gain01.dat - on a fresh tree the shipped "Const 17 dB" one, i.e.
# 17 dB too much gain and every band greener than it is. Measured: 20
# characters work, 22 do not. One short fixed name is enough, because the runs
# are serialised by the lock in compute_day().
STEM = "ft950p"


def _check_antenna(root: Path) -> None:
    """The gain table voacapl used must be the one the deck asked for."""
    want = (Path("/usr/share/voacapl/itshfbc/antennas") / ANTENNA).read_text().split()[0]
    got = (root / "run" / "gain01.dat").read_text(errors="replace").splitlines()[0]
    if want.upper() not in got.upper():
        raise RuntimeError(f"voacapl used the wrong antenna table: {got.strip()!r}")


def _circuit(voacapl: str, root: Path, deck: str, stem: str = STEM) -> dict[int, list[float]]:
    dat, out = root / "run" / f"{stem}.dat", root / "run" / f"{stem}.out"
    if len(dat.name) > 20:
        raise ValueError(f"deck name {dat.name!r} longer than voacapl's 20 characters")
    try:
        dat.write_text(deck)
        # niced: the decoder's jt9 runs on three cores every 15 s and must
        # never wait for a forecast
        subprocess.run([voacapl, str(root), dat.name, out.name], capture_output=True,
                       timeout=60, preexec_fn=lambda: os.nice(19))
        _check_antenna(root)
        return parse_rel(out.read_text(errors="replace"))
    finally:
        for p in (dat, out):
            try:
                p.unlink()
            except OSError:
                pass


def compute_day(grid: str, ssn: float, when: dt.datetime) -> dict[int, list[float]]:
    """{utc hour: [mean REL over the ring, per band]} for the month of ``when``."""
    voacapl = shutil.which("voacapl")
    if not voacapl:
        raise RuntimeError("voacapl not installed (apt install voacapl)")
    root = _itshfbc()
    home = grid_latlon(grid)
    acc = {h: [0.0] * len(BANDS) for h in range(24)}
    n = 0
    # voacapl keeps its scratch files under fixed names in run/ (gain01.dat,
    # voacapx.out, ...), so two runs on one tree overwrite each other's antenna
    # tables and results. The lock holds across processes, not only threads.
    with open(root / "run" / ".lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        for km in RING_KM:
            for brg in range(0, 360, RING_STEP_DEG):
                rel = _circuit(voacapl, root, build_deck(home, destination(*home, brg, km), ssn, when))
                for h in range(24):
                    acc[h] = [a + r for a, r in zip(acc[h], rel[h])]
                n += 1
    return {h: [round(a / n, 3) for a in acc[h]] for h in range(24)}


def level(rel: float) -> str:
    return "good" if rel >= GOOD else "fair" if rel >= FAIR else "poor"


class Propagation:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._day: dict[int, list[float]] | None = None
        self._key: tuple | None = None            # (utc date, grid, ssn) of _day
        self._error = ""

    # -- sunspot number --------------------------------------------------------
    def _ssn(self) -> tuple[float, str]:
        try:
            cache = json.loads(CACHE.read_text())
        except (OSError, ValueError):
            cache = {}
        today = dt.datetime.now(dt.timezone.utc).date().isoformat()
        if cache.get("fetched") != today:
            try:
                with urllib.request.urlopen(SSN_URL, timeout=8) as r:
                    last = json.loads(r.read())[-1]
                cache = {"ssn": float(last["ssn"]), "month": last["time-tag"], "fetched": today}
                tmp = CACHE.with_suffix(".tmp")
                tmp.write_text(json.dumps(cache))
                os.replace(tmp, CACHE)
            except Exception as exc:  # noqa: BLE001 - offline is normal for a Pi
                log.info("SSN not fetched (%s), using %s", exc, cache.get("ssn", SSN_FALLBACK))
        return float(cache.get("ssn", SSN_FALLBACK)), cache.get("month", "")

    # -- what the strip shows --------------------------------------------------
    def status(self, grid: str) -> dict:
        now = dt.datetime.now(dt.timezone.utc)
        if not grid:
            return {"available": False, "reason": "no locator set (Settings ▸ General)"}
        with self._lock:
            ssn, month = self._ssn()
            key = (now.date(), grid.upper(), ssn)
            if key != self._key:
                try:
                    self._day = compute_day(grid, ssn, now)
                    self._key, self._error = key, ""
                    log.info("VOACAP band conditions for %s, SSN %.0f computed", grid, ssn)
                except Exception as exc:  # noqa: BLE001
                    self._day, self._key, self._error = None, None, str(exc)
                    log.warning("VOACAP failed: %s", exc)
            if self._day is None:
                return {"available": False, "reason": self._error or "not computed"}
            day = self._day
        lat, lon = grid_latlon(grid)
        idx = {b: i for i, (b, _) in enumerate(BANDS)}

        def pairs(rel: list[float]) -> list[dict]:
            out = []
            for name, bands in GROUPS:
                best = max(rel[idx[b]] for b in bands)
                out.append({"name": name, "rel": round(best, 3), "level": level(best),
                            "bands": {str(b): round(rel[idx[b]], 3) for b in bands}})
            return out

        def groups(h: int) -> list[dict]:
            return pairs(day[h % 24])

        # Day and night as a whole, for the sun/moon switch: every UTC hour of
        # today whose middle has the sun up at this locator counts as day, the
        # rest as night, and each half is the mean of its hours. Polar day or
        # night leaves one half empty - it is then simply not offered.
        def is_day(h: int) -> bool:
            mid = now.replace(hour=h, minute=30, second=0, microsecond=0)
            return solar_elevation(lat, lon, mid) > -0.833

        halves = {}
        for name, want in (("day", True), ("night", False)):
            hrs = [h for h in range(24) if is_day(h) == want]
            if hrs:
                mean = [sum(day[h][i] for h in hrs) / len(hrs) for i in range(len(BANDS))]
                hset = set(hrs)            # one stretch on the 24-hour circle
                start = next((h for h in hrs if (h - 1) % 24 not in hset), hrs[0])
                halves[name] = {"hours": hrs, "from": start, "to": (start + len(hrs) - 1) % 24,
                                "groups": pairs(mean)}
        return {
            "available": True, "engine": "voacap", "grid": grid.upper(),
            "ssn": ssn, "ssn_month": month, "utc_hour": now.hour,
            "day": solar_elevation(lat, lon, now) > -0.833,
            "groups": groups(now.hour),
            "halves": halves,
            # the next three hours, for the trend in the tooltip
            "next": [{"utc_hour": (now.hour + k) % 24,
                      "levels": [g["level"] for g in groups(now.hour + k)]} for k in (1, 2, 3)],
            "model": {"power_w": round(POWER_KW * 1000), "mode": "FT8", "antenna": "isotropic",
                      "paths": len(RING_KM) * (360 // RING_STEP_DEG),
                      "ring_km": list(RING_KM)},
        }
