"""Settings changed in the web UI, persisted next to the backend.

``config.Settings`` holds the defaults (code, environment, ``.env``); this file
holds what an operator saved in Settings, and wins over both. It is local
station data (callsign, port), so it is gitignored like TM-V71 Remote's
runtime.json.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("ft950")

FILE = Path(__file__).resolve().parents[1] / "runtime.json"
KEYS = {"my_call", "my_grid", "serial_port", "audio_device",
        "digi_depth", "digi_decoder", "digi_low_hz", "digi_high_hz", "ft_closing", "digi_colors", "tx_level", "serial_baud", "codec_playback",
        "rx_gain", "tx_gain", "rx_buffer_ms", "tx_buffer_ms", "ptt_tail_ms", "roger", "tx_auto_gain", "rx_level_db", "tx_comp", "tx_preemph", "hackrf_url",
        "shelly_host", "shelly_channel", "shelly_user", "shelly_password",
        "auto_power_off", "auto_power_off_s",
        # Settings ▸ Log (holds the Wavelog key and QRZ password: file is 0600)
        "wavelog_url", "wavelog_key", "wavelog_station_id",
        "qrz_username", "qrz_password", "qrz_api_key", "auto_log", "qrz_before_log",
        "mem_labels",                    # {"4": "FT8 20m"}: memory key names (radio has no CAT tag)
        "mem_extra",                     # {"7": {"split": true, "vfo_b_hz": 29540000}}: not in a radio memory
        # Hunt: {"K1ABC": {"t": 1761480000.0, "dxcc": …, "snr": …, "band": …}}: the no-reply list
        "hunt_blocked", "hunt_target", "hunt_min_snr", "hunt_minutes"}


def load() -> dict:
    try:
        data = json.loads(FILE.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log.warning("runtime.json unreadable, ignored: %s", exc)
        return {}
    return {k: v for k, v in data.items() if k in KEYS}


def save(update: dict) -> dict:
    data = load()
    data.update({k: v for k, v in update.items() if k in KEYS})
    tmp = FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:         # secrets inside: owner-only
        f.write(json.dumps(data, indent=2, sort_keys=True))
    os.replace(tmp, FILE)                 # never a half-written file
    return data
