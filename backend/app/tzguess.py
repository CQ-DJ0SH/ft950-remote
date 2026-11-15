"""The local time of a station, as its offset from UTC in hours - a guess.

cty.dat gives one standard-time offset per DXCC entity. For entities that
span several zones (Canada, USA, Russia, Australia, Brazil …) the station's
longitude decides instead (its locator, else the entity's centre - then the
guess is rough). Summer time comes from the IANA rules (zoneinfo) where it
is common: Europe, USA / Canada, Alaska, southeast Australia, New Zealand.
Elsewhere standard time is taken (most of the tropics and Asia have none)."""
from __future__ import annotations

import datetime as dt
from typing import Optional
from zoneinfo import ZoneInfo

# entities whose zone the longitude decides: (west edge of a zone, standard
# offset, IANA zone for summer time or None) from west to east
_WIDE = {
    "Canada": [(-180, -8, "America/Vancouver"), (-120, -7, "America/Edmonton"),
               (-102, -6, "America/Winnipeg"), (-90, -5, "America/Toronto"),
               (-64, -4, "America/Halifax"), (-59.5, -3.5, "America/St_Johns")],
    "United States": [(-180, -8, "America/Los_Angeles"), (-114.5, -7, "America/Denver"),
                      (-101, -6, "America/Chicago"), (-86.5, -5, "America/New_York")],
    "Australia": [(0, 8, None), (129, 9.5, "Australia/Adelaide"), (141, 10, "Australia/Sydney")],
    "Brazil": [(-75, -5, None), (-66, -4, None), (-51, -3, None)],
    "Mexico": [(-120, -7, None), (-106, -6, None), (-89.5, -5, None)],
    "European Russia": [(0, 2, None), (22, 3, None), (46, 4, None), (51, 5, None)],
    "Asiatic Russia": [(55, 5, None), (70, 6, None), (82, 7, None), (97, 8, None),
                       (112, 9, None), (127, 10, None), (142, 11, None), (157, 12, None)],
    "Kazakhstan": [(45, 5, None)],
    "Indonesia": [(90, 7, None), (115.5, 8, None), (127, 9, None)],
    "Argentina": [(-80, -3, None)],
    "Greenland": [(-75, -4, None), (-60, -2, None), (-25, -1, None)],
}
# no summer time in these European entities
_EU_NO_DST = {"European Russia", "Belarus", "Kaliningrad", "Iceland", "Turkey", "Georgia",
              "Armenia", "Azerbaijan", "Asiatic Turkey", "European Turkey"}
_EU_ZONE = {0.0: "Europe/London", 1.0: "Europe/Berlin", 2.0: "Europe/Helsinki"}
_NAMED = {"Alaska": "America/Anchorage", "New Zealand": "Pacific/Auckland",
          "Azores": "Atlantic/Azores", "Canary Islands": "Atlantic/Canary",
          "Madeira Islands": "Atlantic/Madeira"}


# no locator heard: the call district narrows Canada and Australia down (the
# districts follow provinces / states there; in the USA they no longer do)
_DISTRICT_LON = {
    "Canada": {"VE1": -63, "VA1": -63, "VE9": -66, "VA9": -66, "VY2": -63, "VO1": -53, "VO2": -60,
               "VE2": -72, "VA2": -72, "VE3": -80, "VA3": -80, "VE4": -97, "VA4": -97,
               "VE5": -106, "VA5": -106, "VE6": -114, "VA6": -114, "VE7": -123, "VA7": -123,
               "VY1": -135, "VE8": -114, "VY0": -94},
    "Australia": {"VK1": 149, "VK2": 151, "VK3": 145, "VK4": 153, "VK5": 138.6, "VK6": 116,
                  "VK7": 147, "VK8": 131},
}


def district_lon(entity: Optional[str], call: str) -> Optional[float]:
    """A longitude for the call's district (Canada, Australia), else None."""
    table = _DISTRICT_LON.get(entity or "")
    if not table:
        return None
    base = call.split("/")[0].upper()
    return next((v for k, v in table.items() if base.startswith(k)), None)


def _summer(zone: Optional[str], std: float, t: float) -> float:
    if not zone:
        return std
    try:
        tz = ZoneInfo(zone)
    except Exception:  # noqa: BLE001 - no tz database: standard time
        return std
    when = dt.datetime.fromtimestamp(t, tz)
    return std + (when.dst() or dt.timedelta()).total_seconds() / 3600


def utc_offset(entity: Optional[str], zone: Optional[tuple[str, float]],
               lon: Optional[float], t: float,
               centre_lon: Optional[float] = None) -> Optional[tuple[float, bool]]:
    """(offset in hours east+, rough) of the station's local time at t.
    rough: a wide entity without the station's own longitude - then the zone
    at the entity's centre (cty.dat's own offset is outdated for some, e.g.
    European Russia +4 instead of Moscow's +3)."""
    if not entity or not zone:
        return None
    cont, std = zone
    if entity in _WIDE:
        bands = _WIDE[entity]
        rough = lon is None
        if rough:
            if centre_lon is None:
                return std, True
            lon = centre_lon
        pick = bands[0]
        for b in bands:
            if lon >= b[0]:
                pick = b
        return _summer(pick[2], pick[1], t), rough
    if entity in _NAMED:
        return _summer(_NAMED[entity], std, t), False
    if cont == "EU" and entity not in _EU_NO_DST and std in _EU_ZONE:
        return _summer(_EU_ZONE[std], std, t), False
    return std, False
