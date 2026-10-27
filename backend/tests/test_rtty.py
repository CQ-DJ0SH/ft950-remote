import numpy as np

from app import rtty


def _feed(sk, x):
    pcm = (np.clip(x, -4, 4) * 8000).astype(np.int16)
    for i in range(0, len(pcm), 960):
        sk.feed(pcm[i:i + 960])


def test_encode_shifts_and_unshift_on_space():
    codes = rtty.encode("AB 12 C")
    # LTRS LTRS A B space FIGS 1 2 space C (back in letters after the space)
    assert codes == [31, 31, 3, 25, 4, 27, 23, 19, 4, 14]


def test_two_signals_decoded_in_both_sidebands():
    for upper in (False, True):
        rng = np.random.default_rng(2)
        a = rtty.waveform("CQ CQ TEST DE DJ0SH JO62 599 001 K", 1400, upper)
        b = rtty.waveform("TU 73 UR 599 0042 DE OH6IH", 2210, upper)
        n = max(len(a), len(b)) + 48000
        x = np.zeros(n)
        x[:len(a)] += 0.3 * a
        x[24000:24000 + len(b)] += 0.2 * b
        x += 0.15 * rng.standard_normal(n)
        sk = rtty.RTTYSkimmer()
        sk.upper_mark = upper
        sk.set_enabled(True)
        _feed(sk, x)
        shown = {round(c["hz"] / 10) * 10: c["text"].strip() for c in sk.snapshot()["channels"]}
        assert shown[1400] == "CQ CQ TEST DE DJ0SH JO62 599 001 K"
        assert shown[2210] == "TU 73 UR 599 0042 DE OH6IH"


def test_noise_alone_shows_nothing():
    rng = np.random.default_rng(3)
    sk = rtty.RTTYSkimmer()
    sk.set_enabled(True)
    _feed(sk, 0.3 * rng.standard_normal(48000 * 15))
    assert sk.snapshot()["channels"] == []


def test_duration_matches_waveform():
    t = "CQ CQ DE DJ0SH"
    assert abs(rtty.duration_s(t) - len(rtty.waveform(t, 1500, False)) / 48000) < 0.05
