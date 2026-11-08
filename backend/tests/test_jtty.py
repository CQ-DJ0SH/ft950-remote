"""JTTY: the transmit audio from jttyd, the 48 -> 12 kHz feed, the send request
(the decoder's 20-35 s table set-up is not run here)."""
import numpy as np
import pytest
from pydantic import ValidationError

from app import jtty
from app.models import JttySendReq

needs_jttyd = pytest.mark.skipif(not jtty.available(), reason="jttyd not installed")


@needs_jttyd
def test_waveform_frames_and_level():
    w, info = jtty.waveform("CQ CQ DE DJ0SH DJ0SH JO62 K", 1234.0, 48_000)
    assert info["ok"] and info["frames"] == 6 and info["text"] == "CQ CQ DE DJ0SH DJ0SH JO62 K"
    assert abs(len(w) / 48_000 - info["seconds"]) < 0.01 and 11.0 < info["seconds"] < 11.6
    assert 0.9 < np.abs(w).max() <= 1.0
    spec = np.abs(np.fft.rfft(w)); peak = np.argmax(spec) * 48_000 / len(w)
    assert 1200 < peak < 1400                                   # the four tones above f0


@needs_jttyd
def test_waveform_rejects_nothing_to_send():
    with pytest.raises(RuntimeError):
        jtty.waveform("   ", 1500.0, 48_000)


def test_feed_decimates_to_12k_and_waits_while_starting():
    r = jtty.JttyReceiver()
    r._proc, r.enabled, r.state = object(), True, "ready"
    r.feed(np.zeros(960, dtype=np.int16))
    assert len(r._q) == 1 and len(r._q[0]) == 240 * 2           # 20 ms at 12 kHz, int16
    r.state = "starting"                                         # warming up on its own signal
    r.feed(np.zeros(960, dtype=np.int16))
    assert len(r._q) == 1


def test_send_request_validation():
    assert JttySendReq(text="CQ CQ DJ0SH JO62", hz=1500).hz == 1500
    for bad in ({"text": "", "hz": 1500}, {"text": "CQ", "hz": 100}, {"text": "ÄÖÜ", "hz": 1500}):
        with pytest.raises(ValidationError):
            JttySendReq(**bad)
