"""MFSK32: fldigi's coding, transmit -> receive loopback, the skimmer."""
import numpy as np

from app import mfsk, rtty

TEXT = "CQ CQ de DJ0SH DJ0SH pse k\nThe quick brown fox 0123456789"


def _feed(x, reverse=False):
    s = mfsk.MFSKSkimmer()
    s.enabled, s.reverse = True, reverse
    x = x / max(1e-9, np.max(np.abs(x))) * 0.5 * 32767
    for i in range(0, len(x), 960):
        s.feed(x[i:i + 960].astype(np.int16))
    return [c for c in s.channels if c.plausible]


def _sig(hz, lead=1.0, snr=None, reverse=False, seed=1):
    w = mfsk.waveform(TEXT, hz, reverse)
    x = np.concatenate([np.zeros(int(lead * 48000)), w, np.zeros(96000)])
    if snr is not None:
        rng = np.random.default_rng(seed)
        x = x + rng.normal(0, np.sqrt(np.mean(w ** 2) / 10 ** (snr / 10) * 24000 / 2500), len(x))
    return x


def test_varicode_is_fldigis():
    assert len(mfsk.VARICODE) == 256 and mfsk.VARICODE[32] == "100" and mfsk.VARICODE[ord("e")] == "1000"
    assert all(c.startswith("1") and c.endswith("00") and "001" not in c for c in mfsk.VARICODE)   # "001" ends a character


def test_gray_round_trip_and_interleaver():
    assert all(mfsk.gray_decode(mfsk.gray_encode(i)) == i for i in range(16))
    tx, rx = mfsk.Interleaver(4, 10, True), mfsk.Interleaver(4, 10, False)
    data = [((i * 7) >> 1) & 15 for i in range(400)]
    out = []
    for d in data:
        w = tx.bits(d)
        soft = rx.symbols([255 if w >> (3 - k) & 1 else 0 for k in range(4)])
        out.append(sum((1 if v > 128 else 0) << (3 - k) for k, v in enumerate(soft)))
    # after the interleaver's delay the data come out unchanged
    delay = next(d for d in range(200) if out[d:d + 100] == data[:100])
    assert out[delay:] == data[:len(data) - delay]


def test_loopback_clean_offset_and_mirrored():
    # real audio always has some noise (without any, the floor is meaningless)
    for hz, kw in ((1500.0, {}), (1203.7, {"lead": 0.77}), (1800.0, {"reverse": True})):
        chs = _feed(_sig(hz, snr=20, **kw), reverse=kw.get("reverse", False))
        assert len(chs) == 1 and abs(chs[0].hz - hz) < 4      # tones are 31 Hz apart
        assert TEXT in chs[0].text


def test_weak_signal_decodes():
    chs = _feed(_sig(1500.0, lead=2.0, snr=-6))
    assert len(chs) == 1 and "The quick brown fox" in chs[0].text


def test_rtty_and_noise_are_no_mfsk():
    rng = np.random.default_rng(3)
    r = rtty.waveform("RYRYRY CQ TEST DE SM5ABC SM5ABC K " * 3, 1500, True)
    x = np.concatenate([r, r]) + rng.normal(0, 0.3, 2 * len(r))
    assert _feed(x) == []
