"""Offline tests for the jt9 parser and the 48k→12k resampler."""
import numpy as np

from app.audio import RATE, to_12k
from app.digi import auto_mode, parse_jt9

# Real jt9 output from the radio (14.080 MHz FT4, 2026-10-24 14:00:30 UTC).
JT9_LIVE = """\
140030   5  0.1  722 +  9A2VX EW2MS KO34                        
140030   2  0.0 1431 +  CQ CT7AUT IM67                          
140030   5 -0.1 1860 +  IZ2QPP RA3FD +15                        
140030  -4 -0.1 2126 +  G8TKY SV1EKI KM18                       
<DecodeFinished>   0   4        0
"""


def test_parse_live_ft4():
    d = parse_jt9(JT9_LIVE)
    assert len(d) == 4
    assert d[0] == {"utc": "140030", "snr": 5, "dt": 0.1, "df": 722,
                    "msg": "9A2VX EW2MS KO34", "ap": None}
    assert d[3]["snr"] == -4 and d[3]["df"] == 2126


def test_parse_ft8_ap_marker():
    d = parse_jt9("140045 -18  0.3 1250 ~  DJ0SH DL1ABC JO31   a2\n")
    assert d[0]["msg"] == "DJ0SH DL1ABC JO31" and d[0]["ap"] == "a2"


def test_auto_mode():
    assert auto_mode(14_080_000) == "FT4"
    assert auto_mode(14_074_000) == "FT8"
    assert auto_mode(None) == "FT8"


def test_to_12k_keeps_tone_and_length():
    t = np.arange(RATE * 2) / RATE
    y = to_12k(np.sin(2 * np.pi * 1500 * t) * 10000)
    assert len(y) == 24000
    f = np.fft.rfftfreq(len(y), 1 / 12000)[np.argmax(np.abs(np.fft.rfft(y)))]
    assert abs(f - 1500) < 1
    assert 0.95 < np.max(np.abs(y[1000:-1000])) / 10000 < 1.05


def test_to_12k_removes_hum_and_aliases():
    t = np.arange(RATE) / RATE
    y = to_12k(np.sin(2 * np.pi * 50 * t) * 10000       # hum
               + np.sin(2 * np.pi * 9000 * t) * 10000)  # would alias to 3000
    assert np.max(np.abs(y[500:-500])) < 50


def _svc():
    from app.digi import MODES, DigiService
    s = DigiService.__new__(DigiService)
    s._fits = {m: 0 for m in MODES}
    return s


def test_adapt_is_stable_at_measured_ft8_times():
    s = _svc(); s.depth = {"FT8": 2, "FT4": 3}
    for t in (3.5, 3.3, 3.8, 3.4, 3.9):           # parallel d2 times, busy band
        s._adapt("FT8", t)
        assert s.depth["FT8"] == 2


def test_adapt_steps_down_on_risk_and_up_when_it_fits():
    s = _svc(); s.depth = {"FT8": 2, "FT4": 3}
    s._adapt("FT8", 4.6)                           # over the 4 s latency budget
    assert s.depth["FT8"] == 1
    s._adapt("FT8", 1.0)                           # 1.0 × 3.5 = 3.5 s fits …
    assert s.depth["FT8"] == 1                     # … but only once so far
    s._adapt("FT8", 1.0)
    assert s.depth["FT8"] == 2
    s._adapt("FT8", 2.0); s._adapt("FT8", 2.0)     # 2 × 1.8 = 3.6 s fits
    assert s.depth["FT8"] == 3
    s._adapt("FT8", 3.9)                           # deep run fits: stays
    assert s.depth["FT8"] == 3


def test_single_fast_cycle_does_not_go_deeper():
    # After boot: a near-empty slice, then normal busy cycles.
    s = _svc(); s.depth = {"FT8": 2, "FT4": 3}
    for t in (1.5, 3.3, 3.6):
        s._adapt("FT8", t)
    assert s.depth["FT8"] == 2


def _cap():
    from collections import deque
    from app.audio import AudioCapture
    c = AudioCapture.__new__(AudioCapture)
    c._est, c._anchor, c._total = deque(maxlen=1500), None, 0
    return c


def _feed(c, start, chunks, jitter=0.01):
    import random
    from app.audio import CHUNK, RATE
    for k in range(chunks):
        c._total += CHUNK
        c._update_anchor(start + c._total / RATE + random.uniform(0, jitter))


def test_anchor_follows_forward_clock_step():
    c = _cap()
    _feed(c, 1000.0, 500)                 # 10 s on the stale boot clock
    assert abs(c._anchor - 1000.0) < 0.02
    _feed(c, 1030.0, 100)                 # NTP steps the clock +30 s
    assert abs(c._anchor - 1030.0) < 0.02


def test_anchor_takes_backward_step_at_once():
    c = _cap()
    _feed(c, 1000.0, 200)
    _feed(c, 998.0, 1)
    assert abs(c._anchor - 998.0) < 0.02


def test_anchor_ignores_jitter():
    c = _cap()
    _feed(c, 1000.0, 1000, jitter=0.2)    # heavy scheduling jitter
    assert abs(c._anchor - 1000.0) < 0.02


def test_parse_two_trailing_markers():
    d = parse_jt9("153700 -23  0.5 2028 ~  CQ LA8ENA JO48           ? a2\n")
    assert d[0]["msg"] == "CQ LA8ENA JO48" and d[0]["ap"] == "? a2"



def test_decoder_choice_falls_back(monkeypatch, tmp_path):
    from app import digi
    exe = tmp_path / "jt9-fast"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setattr(digi, "DECODERS", [{"id": "jt9", "path": "jt9", "label": ""},
                                           {"id": "jt9-fast", "path": str(exe), "label": ""}])
    assert digi.digi_jt9_path("jt9-fast") == str(exe)
    assert digi.digi_jt9_path("jt9") == "jt9"
    assert digi.digi_jt9_path("nonsense") == "jt9"          # unknown id: never a path from outside
    exe.unlink()
    assert digi.digi_jt9_path("jt9-fast") == "jt9"          # a missing build must not stop decoding


def test_mt_decoder_runs_one_threaded_full_pass(monkeypatch, tmp_path):
    import asyncio
    from app import digi
    svc = digi.DigiService.__new__(digi.DigiService)
    svc.config = {"low_hz": 200, "high_hz": 3000, "my_call": "", "my_grid": "",
                  "jt9": "/x/jt9-33", "jt9_mt": True}
    svc._last_df = {}
    calls = []

    async def fake(mode, wav, depth, rng=None, sub="", threads=0):
        calls.append((mode, rng, sub, threads))
        return ""
    monkeypatch.setattr(svc, "_jt9", fake)
    asyncio.run(svc._jt9_parallel("FT8", tmp_path / "a.wav", 2))
    assert calls == [("FT8", None, "", digi.PARTS)]           # one process, PARTS threads, whole band
    calls.clear()
    svc.config["jt9_mt"] = False
    (tmp_path / "a.wav").write_bytes(b"")
    asyncio.run(svc._jt9_parallel("FT8", tmp_path / "a.wav", 2))
    assert len(calls) == digi.PARTS and all(c[3] == 0 for c in calls)   # band split, no threads


def test_a_late_slice_is_waited_for_not_dropped():
    import asyncio
    import numpy as np
    from app import digi

    class Cap:
        running = True
        calls = 0
        def extract(self, t, n):
            Cap.calls += 1
            return None if Cap.calls < 4 else np.zeros(10)       # complete after ~0.15 s
    svc = digi.DigiService.__new__(digi.DigiService)
    svc.capture = Cap()
    assert asyncio.run(svc._slice("FT8", 15.0, 15)) is not None

    class Gone(Cap):
        def extract(self, t, n):
            return None                                           # never arrives
    svc.capture = Gone()
    assert asyncio.run(svc._slice("FT8", 15.0, 15)) is None       # gives up after SLICE_WAIT
