import io
import wave

import numpy as np

from app.recorder import RATE, Recorder


def test_records_decimated_tone_and_wav():
    r = Recorder()
    r.feed(np.ones(960, np.int16))           # off: ignored
    assert r.status()["rec_seconds"] == 0
    r.set(True)
    t = np.arange(48_000) / 48_000
    tone = (8000 * np.sin(2 * np.pi * 1000 * t)).astype(np.int16)
    for i in range(0, len(tone), 960):
        r.feed(tone[i:i + 960])
    st = r.status()
    assert st["recording"] and st["rec_seconds"] == 1.0
    with wave.open(io.BytesIO(r.wav_bytes())) as w:
        assert w.getframerate() == RATE and w.getnframes() == RATE
        y = np.frombuffer(w.readframes(RATE), np.int16)
    # 1 kHz passes the low-pass at full level; no clicks at chunk joins
    assert abs(np.abs(y[200:-200]).max() - 8000) < 300
    r.set(False)
    assert r.status()["rec_seconds"] == 1.0   # stopping keeps the take
    r.set(True)
    assert r.status()["rec_seconds"] == 0     # a new take starts fresh


def test_capture_gain():
    from app.audio import apply_gain
    x = np.array([1000, -1000, 30000], np.int16)
    assert list(apply_gain(x, 6.0206)) == [2000, -2000, 32767]   # +6 dB doubles, clipped
    assert list(apply_gain(x, -20)) == [100, -100, 3000]
