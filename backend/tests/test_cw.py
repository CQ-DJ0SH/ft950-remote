import numpy as np

from app.cw import FS_IN, MORSE, CWSkimmer, cw_duration_s


def keyed(text, wpm, hz, secs, amp=6000, start=0.3):
    """48 kHz audio with ``text`` keyed as CW at ``hz``."""
    unit = 1.2 / wpm
    env = np.zeros(int(secs * FS_IN))
    t = start
    for ch in text:
        if ch == " ":
            t += 4 * unit
            continue
        for s in MORSE[ch]:
            n0, n1 = int(t * FS_IN), int((t + (unit if s == "." else 3 * unit)) * FS_IN)
            env[n0:n1] = 1.0
            t += (unit if s == "." else 3 * unit) + unit
        t += 2 * unit
    ramp = np.convolve(env, np.ones(240) / 240, mode="same")      # 5 ms edges, no clicks
    return amp * ramp * np.sin(2 * np.pi * hz * np.arange(len(env)) / FS_IN)


def test_two_signals_decoded_side_by_side():
    rng = np.random.default_rng(1)
    secs = 22.0
    a = keyed("CQ CQ DE DL1ABC DL1ABC K ", 18, 600, secs)
    b = keyed("TEST PA3XYZ PA3XYZ TEST ", 25, 950, secs, start=0.8)
    x = np.clip(a + b + rng.normal(0, 400, len(a)), -32768, 32767).astype(np.int16)
    sk = CWSkimmer()
    sk.set_enabled(True)
    for i in range(0, len(x), 960):                 # 20 ms chunks, as AudioCapture delivers
        sk.feed(x[i:i + 960])
    ch = {round(c["hz"] / 50) * 50: c for c in sk.snapshot()["channels"]}
    assert 600 in ch and 950 in ch, ch.keys()
    assert "DL1ABC" in ch[600]["text"], ch[600]["text"]
    assert "PA3XYZ" in ch[950]["text"], ch[950]["text"]
    assert 15 <= ch[600]["wpm"] <= 21 and 21 <= ch[950]["wpm"] <= 30


def test_duration():
    assert abs(cw_duration_s("PARIS ", 20) - 3.0) < 0.15     # PARIS = 50 units = 3 s at 20 WPM


def test_dot_noise_channel_is_hidden():
    from app.cw import Channel
    c = Channel(470)
    c.text = "E EHIEEEEE5EEIEEEPE IEE 6EEEE EEDEIEEEHEEEEEEEIEEEERIE EEEH "
    assert not c.plausible
    c.text = "CQ CQ DE DL1ABC DL1ABC K TEST 5NN TU "
    assert c.plausible
