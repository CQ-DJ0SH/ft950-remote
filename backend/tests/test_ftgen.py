"""Our own TX waveforms must decode with jt9 — the strongest test short of RF."""
import shutil
import subprocess
import wave

import numpy as np
import pytest

from app.audio import to_12k
from app.ftgen import RATE, START_S, duration, tones, waveform, FtError

pytestmark = pytest.mark.skipif(not shutil.which("jt9"), reason="wsjtx not installed")


def _decode(tmp_path, mode, msg, f0, snr):
    period, flag, tr, name = (15, "-8", "15", "261024_000000.wav") if mode == "FT8" else (7.5, "-5", "7", "261024_000030.wav")
    w = waveform(tones(msg, mode), mode, f0)
    n = int(period * RATE)
    x = np.zeros(n)
    i0 = int(START_S * RATE)
    x[i0:i0 + len(w)] = w
    noise = 0.5 / 10 ** (snr / 10) * (RATE / 2) / 2500
    x += np.random.default_rng(7).normal(0, np.sqrt(noise), n)
    y = to_12k(x / np.max(np.abs(x)) * 0.3 * 32767)
    path = tmp_path / name
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(12000)
        f.writeframes(np.clip(y, -32768, 32767).astype("<i2").tobytes())
    out = subprocess.run(["jt9", flag, "-p", tr, "-d", "3", "-a", str(tmp_path), "-t", str(tmp_path), str(path)],
                         capture_output=True, text=True, cwd=tmp_path, timeout=60).stdout
    return [ln.split() for ln in out.splitlines() if msg in ln]


@pytest.mark.parametrize("mode,msg,f0,snr", [
    ("FT8", "CQ DJ0SH JO62", 1500, -12),
    ("FT8", "DL1ABC DJ0SH RR73", 2350, -18),
    ("FT4", "DL1ABC DJ0SH R-12", 1200, -10),
    ("FT4", "CQ DJ0SH JO62", 2600, -14),
])
def test_own_waveform_decodes(tmp_path, mode, msg, f0, snr):
    hits = _decode(tmp_path, mode, msg, f0, snr)
    assert hits, f"{mode} {msg!r} not decoded"
    utc, rx_snr, dt, df = hits[0][:4]
    assert abs(float(dt)) <= 0.1 and abs(int(df) - f0) <= 3 and abs(int(rx_snr) - snr) <= 3


def test_durations():
    assert abs(duration("FT8") - 12.64) < 0.01 and abs(duration("FT4") - 5.04) < 0.01


def test_unpackable_message_is_refused():
    with pytest.raises(FtError):
        tones("THIS IS FAR TOO LONG FOR FT8 MESSAGES", "FT8")
