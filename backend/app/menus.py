"""FT-950 audio menus (EX) for Settings ▸ Audio, with a saved default.

Ranges and options come from the CAT reference (Table 2/3, menus 001-118).
The wire format — number of digits, with or without sign — is *not* reliably
given there (e.g. "–10 ~ +10"), so it is taken from the radio's own read
answer and reused for writing; every write is verified by read-back.

The default is a snapshot of the values the station works with (taken on the
first full read, re-taken on request), so a change can always be undone.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

DEFAULTS_FILE = Path(__file__).resolve().parents[1] / "ex_defaults.json"

_EQ_LOW = {0: "OFF", **{i: f"{i * 100} Hz" for i in range(1, 8)}}
_EQ_MID = {0: "OFF", **{i: f"{600 + i * 100} Hz" for i in range(1, 10)}}
_EQ_HIGH = {0: "OFF", **{i: f"{1400 + i * 100} Hz" for i in range(1, 19)}}


@dataclass
class Menu:
    no: int
    group: str
    label: str
    kind: str                       # int | sint | enum | micgain
    lo: int = 0
    hi: int = 100
    step: int = 1
    unit: str = ""
    options: dict[int, str] = field(default_factory=dict)

    def describe(self) -> dict:
        return {"no": self.no, "group": self.group, "label": self.label, "kind": self.kind,
                "min": self.lo, "max": self.hi, "step": self.step, "unit": self.unit,
                "options": self.options}


MENUS: list[Menu] = [
    Menu(50, "DATA", "DATA TX GAIN", "int"),
    Menu(51, "DATA", "DATA OUTPUT LEVEL (RX to the Pi)", "int"),
    Menu(52, "DATA", "DATA VOX DELAY", "int", 30, 3000, 10, "ms"),
    Menu(53, "DATA", "DATA VOX GAIN", "int"),
    Menu(54, "DATA", "PKT DISPLAY OFFSET", "sint", -3000, 3000, 10, "Hz"),
    Menu(55, "DATA", "PKT CARRIER POINT", "sint", -3000, 3000, 10, "Hz"),
    Menu(61, "DATA", "RTTY OUTPUT LEVEL", "int"),
    Menu(64, "SSB", "SSB TX BPF", "enum", options={0: "100–3000 Hz", 1: "100–2900 Hz",
                                                   2: "200–2800 Hz", 3: "300–2700 Hz", 4: "400–2600 Hz"}),
    Menu(65, "SSB", "LSB CARRIER POINT", "sint", -200, 200, 10, "Hz"),
    Menu(66, "SSB", "USB CARRIER POINT", "sint", -200, 200, 10, "Hz"),
    Menu(36, "MIC", "AM MIC GAIN", "micgain"),
    Menu(56, "MIC", "FM MIC GAIN", "micgain"),
    Menu(109, "MIC", "PROC COMPRESSION", "int"),
    Menu(91, "MIC EQ", "LOW · FREQ", "enum", options=_EQ_LOW),
    Menu(92, "MIC EQ", "LOW · GAIN", "sint", -10, 10, 1, "dB"),
    Menu(93, "MIC EQ", "LOW · BW", "int", 1, 10),
    Menu(94, "MIC EQ", "MID · FREQ", "enum", options=_EQ_MID),
    Menu(95, "MIC EQ", "MID · GAIN", "sint", -10, 10, 1, "dB"),
    Menu(96, "MIC EQ", "MID · BW", "int", 1, 10),
    Menu(97, "MIC EQ", "HIGH · FREQ", "enum", options=_EQ_HIGH),
    Menu(98, "MIC EQ", "HIGH · GAIN", "sint", -10, 10, 1, "dB"),
    Menu(99, "MIC EQ", "HIGH · BW", "int", 1, 10),
    Menu(100, "PROC EQ", "LOW · FREQ", "enum", options=_EQ_LOW),
    Menu(101, "PROC EQ", "LOW · GAIN", "sint", -10, 10, 1, "dB"),
    Menu(102, "PROC EQ", "LOW · BW", "int", 1, 10),
    Menu(103, "PROC EQ", "MID · FREQ", "enum", options=_EQ_MID),
    Menu(104, "PROC EQ", "MID · GAIN", "sint", -10, 10, 1, "dB"),
    Menu(105, "PROC EQ", "MID · BW", "int", 1, 10),
    Menu(106, "PROC EQ", "HIGH · FREQ", "enum", options=_EQ_HIGH),
    Menu(107, "PROC EQ", "HIGH · GAIN", "sint", -10, 10, 1, "dB"),
    Menu(108, "PROC EQ", "HIGH · BW", "int", 1, 10),
    Menu(114, "VOX", "VOX SOURCE", "enum", options={0: "MIC", 1: "DATA"}),
    Menu(115, "VOX", "VOX GAIN", "int"),
    Menu(116, "VOX", "VOX DELAY", "int", 30, 3000, 10, "ms"),
    Menu(117, "VOX", "ANTI-TRIP GAIN", "int"),
    Menu(25, "OTHER", "BEEP LEVEL", "int"),
    Menu(112, "OTHER", "AM CARRIER LEVEL", "int"),
]
BY_NO = {m.no: m for m in MENUS}


def parse(raw: str) -> int:
    """'030' → 30, '+1500' → 1500, '-05' → -5, '--05' (manual style) → -5."""
    s = raw.strip()
    neg = s.startswith("-")
    digits = s.lstrip("+-")
    if not digits.isdigit():
        raise ValueError(f"not a menu value: {raw!r}")
    return -int(digits) if neg else int(digits)


def fmt(value: int, like: str) -> str:
    """Format ``value`` the way the radio wrote ``like`` (width, sign)."""
    signed = like[:1] in "+-"
    width = len(like.lstrip("+-"))
    if signed:
        return ("-" if value < 0 else "+") + str(abs(value)).zfill(width)
    if value < 0:
        raise ValueError("negative value for an unsigned menu")
    return str(value).zfill(width)


def validate(m: Menu, value: int) -> int:
    if m.kind == "enum":
        if value not in m.options:
            raise ValueError(f"EX{m.no:03d}: {value} is not an option")
    elif m.kind == "micgain":
        if value != 1000 and not 0 <= value <= 100:
            raise ValueError(f"EX{m.no:03d}: 1000 (MIC knob) or 0-100")
    elif not m.lo <= value <= m.hi or (value - m.lo) % m.step:
        raise ValueError(f"EX{m.no:03d}: {m.lo}…{m.hi} in steps of {m.step}")
    return value


def load_defaults() -> dict[str, str]:
    try:
        return json.loads(DEFAULTS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_defaults(raw_by_no: dict[int, str]) -> dict[str, str]:
    data = {f"{k:03d}": v for k, v in sorted(raw_by_no.items())}
    tmp = DEFAULTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, DEFAULTS_FILE)
    return data


def item(m: Menu, raw: Optional[str], default_raw: Optional[str]) -> dict:
    d = m.describe()
    d.update(raw=raw, value=parse(raw) if raw else None,
             default_raw=default_raw, default=parse(default_raw) if default_raw else None)
    d["changed"] = raw is not None and default_raw is not None and parse(raw) != parse(default_raw)
    return d
