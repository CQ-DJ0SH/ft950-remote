"""CPU time and memory of the WSJT-X decoder (jt9), for Settings ▸ Pi Hardware.

Every jt9 run reports its own resource use when it ends (``os.wait4``): user +
system CPU seconds and the peak resident memory. One decode pass can be several
runs at once (the band split over three cores), so runs that start together form
a pass: its CPU is the sum, its memory the sum of the peaks (all are resident at
the same time), its run time the longest part.

Per minute the runs are folded into one row for the 24-hour graph, kept on disk
like the host history (``sys_history``) so a restart does not lose the day.
"""
from __future__ import annotations

import logging
import os
import time
from collections import deque

log = logging.getLogger("ft950")

STEP_S = 60
KEEP = 24 * 60
_TRIM_AT = 3 * KEEP
_FILE = os.path.join(os.path.dirname(__file__), "dec_history.csv")
PASS_GAP_S = 0.5                 # runs starting within this are one pass

# ts, CPU ms in the minute, runs, passes, peak pass memory MB, longest pass ms
_FIELDS = 6
_ring: deque = deque(maxlen=KEEP)
_runs: deque = deque(maxlen=400)       # (start, end, cpu_s, wall_s, rss_kb, decoder)


def add_run(start: float, end: float, cpu_s: float, rss_kb: int, decoder: str) -> None:
    """One finished jt9 process (called from the decoder's worker thread)."""
    _runs.append((start, end, cpu_s, end - start, rss_kb, decoder))


def _passes(runs: list) -> list[dict]:
    """Group runs that started together into passes."""
    out: list[dict] = []
    for start, end, cpu, wall, rss, dec in sorted(runs):
        p = out[-1] if out else None
        if p and start - p["start"] <= PASS_GAP_S:
            p["cpu_s"] += cpu
            p["wall_s"] = max(p["wall_s"], end - p["start"])
            p["rss_kb"] += rss
            p["parts"] += 1
            p["part_rss_kb"] = max(p["part_rss_kb"], rss)
        else:
            out.append({"start": start, "cpu_s": cpu, "wall_s": wall, "rss_kb": rss,
                        "part_rss_kb": rss, "parts": 1, "decoder": dec})
    return out


def live() -> dict:
    """The last pass and the last minute, for the table."""
    now = time.time()
    runs = list(_runs)
    if not runs:
        return {}
    passes = _passes(runs)
    last = passes[-1]
    minute = [r for r in runs if r[1] >= now - 60]
    mp = _passes(minute)
    return {
        "decoder": last["decoder"],
        "last_cpu_s": round(last["cpu_s"], 2),
        "last_wall_s": round(last["wall_s"], 2),
        "last_mem_mb": round(last["rss_kb"] / 1024, 1),
        "last_part_mem_mb": round(last["part_rss_kb"] / 1024, 1),
        "last_parts": last["parts"],
        "last_age_s": round(now - last["start"], 1),
        "min_cpu_s": round(sum(r[2] for r in minute), 2),
        "min_passes": len(mp),
        "min_runs": len(minute),
        "peak_mem_mb": round(max((p["rss_kb"] for p in passes), default=0) / 1024, 1),
    }


def load() -> None:
    try:
        with open(_FILE, encoding="utf-8") as f:
            lines = f.readlines()[-KEEP:]
    except OSError:
        return
    cut = time.time() - KEEP * STEP_S
    for line in lines:
        parts = line.split(",")
        if len(parts) != _FIELDS:
            continue
        try:
            row = tuple(int(p) for p in parts)
        except ValueError:
            continue
        if row[0] >= cut:
            _ring.append(row)
    log.info("decoder history: %d samples restored", len(_ring))


def _trim() -> None:
    try:
        with open(_FILE, encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) <= _TRIM_AT:
            return
        with open(_FILE, "w", encoding="utf-8") as f:
            f.writelines(lines[-KEEP:])
    except OSError as exc:
        log.debug("decoder history trim failed: %s", exc)


def sample() -> None:
    """Fold the runs of the last minute into one row (called once a minute)."""
    now = time.time()
    minute = [r for r in _runs if r[1] >= now - STEP_S]
    ps = _passes(minute)
    row = (int(now),
           int(round(sum(r[2] for r in minute) * 1000)),
           len(minute), len(ps),
           int(round(max((p["rss_kb"] for p in ps), default=0) / 1024)),
           int(round(max((p["wall_s"] for p in ps), default=0) * 1000)))
    _ring.append(row)
    try:
        with open(_FILE, "a", encoding="utf-8") as f:
            f.write(",".join(str(v) for v in row) + "\n")
    except OSError as exc:
        log.debug("decoder history write failed: %s", exc)
        return
    if row[0] % (STEP_S * KEEP) < STEP_S:
        _trim()


def series(hours: float = 24.0) -> dict:
    cut = time.time() - max(0.0, hours) * 3600
    rows = [r for r in _ring if r[0] >= cut]
    cols = list(zip(*rows)) if rows else [()] * _FIELDS
    cores = os.cpu_count() or 1
    return {
        "step_s": STEP_S,
        "n": len(rows),
        "cores": cores,
        "ts": list(cols[0]),
        # share of the whole Pi (all cores) over the minute, in %
        "cpu_pct": [round(v / 1000 / STEP_S / cores * 100, 2) for v in cols[1]],
        "cpu_s": [round(v / 1000, 2) for v in cols[1]],
        "runs": list(cols[2]),
        "passes": list(cols[3]),
        "mem_mb": list(cols[4]),
        "wall_ms": list(cols[5]),
    }
