"""FT8/FT4 transmit waveforms — tones from WSJT-X, GFSK as WSJT-X shapes it.

``ft8code``/``ft4code`` (from the wsjtx package, like jt9) turn a message into
channel symbols: FT8 79 tones of 8-FSK, FT4 105 tones of 4-FSK including one
ramp symbol at each end. They also show how the message unpacks again, which
rejects anything that is not a valid standard message before it goes on air.

The waveform follows WSJT-X's gen_ft8wave/gen_ft4wave: frequency steps
smoothed with a Gaussian pulse (BT 2.0 for FT8, 1.0 for FT4), phase-continuous,
amplitude ramps at the ends (FT8: an eighth of a symbol; FT4: its ramp
symbols). Generated directly at 48 kHz for the codec.

  FT8: 6.25 Bd, 6.25 Hz spacing, 79 × 0.16 s = 12.64 s
  FT4: 20.833 Bd, 20.833 Hz spacing, (103 + 2 ramp) × 48 ms = 5.04 s

Verified by decoding our own waveforms with jt9 (DT 0.0, exact frequency and
SNR). FT4 was first built at 23.4375 Bd from memory — nothing decoded; the
measured rate is 576 samples per symbol at 12 kHz.
"""
from __future__ import annotations

import math
import re
import subprocess

import numpy as np

RATE = 48_000
SPEC = {
    "FT8": {"tool": "ft8code", "nsps": 1920 * 4, "bt": 2.0, "nsym": 79, "ramp": 1 / 8},
    "FT4": {"tool": "ft4code", "nsps": 576 * 4, "bt": 1.0, "nsym": 105, "ramp": 1.0},
}
START_S = 0.5           # nominal start inside the period (decoder DT = 0)


class FtError(ValueError):
    pass


def tones(message: str, mode: str) -> list[int]:
    """Channel symbols for ``message``; refuses messages that do not round-trip."""
    spec = SPEC[mode]
    msg = " ".join(message.upper().split())
    out = subprocess.run([spec["tool"], msg], capture_output=True, text=True, timeout=5).stdout
    lines = out.splitlines()
    first = next((ln for ln in lines if re.match(r"\s*1\.\s", ln)), "")
    # "1. CQ DJ0SH JO62    CQ DJ0SH JO62    1.  Standard msg"
    if msg not in first.split("  ")[-1] and first.count(msg) < 2:
        raise FtError(f"Message cannot be encoded: {msg!r} → {first.strip()!r}")
    i = next((k for k, ln in enumerate(lines) if ln.startswith("Channel symbols")), None)
    if i is None:
        raise FtError(f"{spec['tool']}: no channel symbols")
    digits = [int(c) for c in lines[i + 2] if c.isdigit()]
    if len(digits) != spec["nsym"]:
        raise FtError(f"{spec['tool']}: {len(digits)} statt {spec['nsym']} Symbole")
    return digits


def _gfsk_pulse(bt: float, t: np.ndarray) -> np.ndarray:
    c = math.pi * math.sqrt(2 / math.log(2))
    erf = np.vectorize(math.erf)
    return 0.5 * (erf(c * bt * (t + 0.5)) - erf(c * bt * (t - 0.5)))


def waveform(symbols: list[int], mode: str, f0: float, amplitude: float = 1.0) -> np.ndarray:
    """float64 samples at 48 kHz, ``f0`` = audio frequency of tone 0."""
    spec = SPEC[mode]
    if mode == "FT4":
        return _ft4(symbols[1:-1], spec, f0) * amplitude
    nsps, nsym = spec["nsps"], len(symbols)
    pulse = _gfsk_pulse(spec["bt"], (np.arange(3 * nsps) - 1.5 * nsps) / nsps)
    dphi_peak = 2 * math.pi / nsps                    # modulation index 1
    dphi = np.zeros((nsym + 2) * nsps)
    for j, s in enumerate(symbols):
        dphi[j * nsps:(j + 3) * nsps] += dphi_peak * pulse * s
    # dummy symbols before the first and after the last (WSJT-X)
    dphi[:2 * nsps] += dphi_peak * symbols[0] * pulse[nsps:]
    dphi[nsym * nsps:] += dphi_peak * symbols[-1] * pulse[:2 * nsps]
    dphi += 2 * math.pi * f0 / RATE
    phi = np.cumsum(dphi[nsps:(nsym + 1) * nsps])
    wave = np.sin(phi - phi[0])
    nramp = int(round(spec["ramp"] * nsps))
    ramp = (1 - np.cos(np.linspace(0, math.pi, nramp))) / 2
    wave[:nramp] *= ramp
    wave[-nramp:] *= ramp[::-1]
    return wave * amplitude


def _ft4(itone: list[int], spec: dict, f0: float) -> np.ndarray:
    """gen_ft4wave: the 103 channel symbols plus a dummy symbol on each side
    that continues the edge tone — ft4code's "R" columns are placeholders,
    not tone 0 — with a raised-cosine ramp over each dummy symbol."""
    nsps, nsym = spec["nsps"], len(itone)
    pulse = _gfsk_pulse(spec["bt"], (np.arange(3 * nsps) - 1.5 * nsps) / nsps)
    pk = 2 * math.pi / nsps
    dphi = np.zeros((nsym + 2) * nsps)
    for j, s in enumerate(itone):
        dphi[j * nsps:(j + 3) * nsps] += pk * pulse * s
    dphi[:2 * nsps] += pk * itone[0] * pulse[nsps:]
    dphi[nsym * nsps:] += pk * itone[-1] * pulse[:2 * nsps]
    dphi += 2 * math.pi * f0 / RATE
    wave = np.sin(np.cumsum(dphi))
    ramp = (1 - np.cos(np.linspace(0, math.pi, nsps))) / 2
    wave[:nsps] *= ramp
    wave[-nsps:] *= ramp[::-1]
    return wave


def duration(mode: str) -> float:
    return SPEC[mode]["nsym"] * SPEC[mode]["nsps"] / RATE
