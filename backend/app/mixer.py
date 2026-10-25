"""Codec playback level (radio TX audio) via amixer, and the ALSA card list.

The PCM2903B has no capture control at all (measured): the RX level into the
Pi is set on the radio only, menu 051, which the Audio tab reaches over CAT.
Playback is the one control the codec has — it sets how hard the Pi drives
the radio's DATA input when transmitting.
"""
from __future__ import annotations

import re
import subprocess
from typing import Optional

CONTROL = "PCM"


def _card(device: str) -> str:
    """'hw:CARD=CODEC,DEV=0' / 'hw:1,0' → amixer card argument."""
    m = re.search(r"CARD=([^,]+)", device) or re.search(r"hw:(\d+)", device)
    return m.group(1) if m else "1"


def get_playback(device: str) -> Optional[int]:
    try:
        out = subprocess.run(["amixer", "-c", _card(device), "sget", CONTROL],
                             capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"\[(\d+)%\]", out)
    return int(m.group(1)) if m else None


def set_playback(device: str, percent: int) -> Optional[int]:
    percent = max(0, min(100, int(percent)))
    subprocess.run(["amixer", "-q", "-c", _card(device), "sset", CONTROL,
                    f"{percent}%"], timeout=3, check=True)
    return get_playback(device)


def capture_devices() -> list[dict]:
    """ALSA capture cards as {device, name} (``arecord -l``)."""
    try:
        out = subprocess.run(["arecord", "-l"], capture_output=True, text=True,
                             timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    devs = []
    for m in re.finditer(r"card (\d+): (\S+) \[([^\]]+)\], device (\d+)", out):
        devs.append({"device": f"hw:CARD={m.group(2)},DEV={m.group(4)}",
                     "name": f"{m.group(3)} (card {m.group(1)})"})
    return devs
