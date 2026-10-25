"""Callsign → DXCC entity from cty.dat (shipped with WSJT-X).

cty.dat (CT9 format): an entity line
    ``Name: CQ: ITU: Cont: Lat: Lon: TZ: Prefix:``
followed by comma-separated prefixes ending with ``;``. ``=CALL`` entries are
exact calls; ``(..)``/``[..]``/``<..>``/``{..}``/``~..~`` overrides are
stripped (only the entity is needed). Lookup is exact match first, then the
longest matching prefix.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

CTY_PATHS = ["/usr/share/wsjtx/cty.dat", "/usr/share/hamradio-files/cty.dat"]
_OVERRIDES = re.compile(r"\(.*?\)|\[.*?\]|<.*?>|\{.*?\}|~.*?~")
# Suffixes that do not change the entity (portable, mobile, QRP …).
_SUFFIXES = {"P", "M", "MM", "AM", "QRP", "A", "B", "LH"}


class Dxcc:
    def __init__(self, path: Optional[str] = None) -> None:
        self.exact: dict[str, str] = {}
        self.prefix: dict[str, str] = {}
        self.pos: dict[str, tuple[float, float]] = {}    # entity → (lat, lon east+)
        p = next((Path(x) for x in ([path] if path else CTY_PATHS)
                  if x and Path(x).is_file()), None)
        if p:
            self._load(p.read_text(errors="replace"))

    def _load(self, text: str) -> None:
        for block in text.split(";"):
            head, _, body = block.strip().partition("\n")
            fields = head.split(":")
            if len(fields) < 8:
                continue
            name = fields[0].strip()
            try:                 # cty.dat counts longitude positive to the WEST
                self.pos.setdefault(name, (float(fields[4]), -float(fields[5])))
            except ValueError:
                pass
            items = [fields[7].strip()] + body.replace("\n", "").split(",")
            for it in items:
                it = _OVERRIDES.sub("", it).strip()
                if not it:
                    continue
                if it.startswith("="):
                    self.exact[it[1:]] = name
                else:
                    self.prefix.setdefault(it, name)

    def __len__(self) -> int:
        return len(set(self.prefix.values()))

    def lookup(self, call: str) -> Optional[str]:
        call = call.upper().strip("<>")
        if call in self.exact:
            return self.exact[call]
        if "/" in call:
            parts = [x for x in call.split("/")
                     if x and x not in _SUFFIXES and not x.isdigit()]
            # PA/K1ABC → PA; K1ABC/P → K1ABC; the shorter part is the prefix.
            call = min(parts, key=len) if len(parts) > 1 else (parts or [call])[0]
            if call in self.exact:
                return self.exact[call]
        for n in range(len(call), 0, -1):
            hit = self.prefix.get(call[:n])
            if hit:
                return hit
        return None

    def position(self, call: str) -> Optional[tuple[str, float, float]]:
        """(entity, lat, lon) of the call's DXCC entity — its centre, not the QTH."""
        name = self.lookup(call)
        return (name, *self.pos[name]) if name in self.pos else None
