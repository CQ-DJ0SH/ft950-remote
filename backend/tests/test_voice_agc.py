import numpy as np

from app.voice import VoiceHub


class _Cap:
    def subscribe(self, fn):
        pass


def _hub(**kw):
    h = VoiceHub(_Cap(), lambda: "null", **kw)
    h.mic_levels["p"] = -120.0
    h.active = "p"
    return h


def test_manual_gain_when_auto_off():
    h = _hub(tx_gain=2.0)
    assert h._gain(np.full(960, 100, np.int16), True) == 2.0


def test_agc_drives_toward_target_and_holds_in_pauses():
    h = _hub(tx_auto_gain=True)
    quiet = (1000 * np.sin(np.arange(960) / 5)).astype(np.int16)    # rms ~707
    for _ in range(200):
        g = h._gain(quiet, True)
    assert abs(g - 5000 / 707) < 0.3                  # rose slowly to ~7x
    loud = (20000 * np.sin(np.arange(960) / 5)).astype(np.int16)    # rms ~14 000
    g = h._gain(loud, True)
    assert g < 4                                      # fast down on a loud burst
    held = h._gain(np.zeros(960, np.int16), True)     # pause: below the noise gate
    assert held == g
    assert h._gain(quiet, False) == g                 # not on the air: no learning


def test_preroll_seeds_speech_since_key_press(monkeypatch):
    from app.voice import speech_start
    h = _hub()
    silence = np.zeros(960, np.int16)
    talk = (3000 * np.sin(np.arange(960) / 5)).astype(np.int16)
    for b in [silence] * 10 + [talk] * 8:          # muted until the key press, then speech
        h.push_tx(b, "p")
    assert len(speech_start(list(h._pre["p"]))) == 8
    h.device_getter = lambda: "null"
    import app.voice as v
    monkeypatch.setattr(v, "PcmOut", type("Fake", (), {"__init__": lambda s, d, p: None,
                                                       "start": lambda s: None, "stop": lambda s: None}))
    h.open_tx(hold=True)
    assert h._grace == 8 * 960 and h.tx_backlog_ms() == 160
    assert not h._pull().any()                     # held: silence until the radio is keyed
    h.release_tx()
    h._priming = False
    assert h._pull().any()                         # then the first spoken block plays
    h.close_tx()


def test_comp_and_preemph_shape_only_when_on():
    h = _hub()
    x = (2000 * np.sin(2 * np.pi * 2500 * np.arange(960) / 48000)).astype(np.int16)
    assert np.array_equal(h._shape(x), x)                       # both off: untouched
    h.tx_preemph = True
    low = (2000 * np.sin(2 * np.pi * 400 * np.arange(960) / 48000)).astype(np.int16)
    g_hi = np.abs(h._shape(x)[200:]).max() / 2000
    h._reset_shape()
    g_lo = np.abs(h._shape(low)[200:]).max() / 2000
    assert g_hi > 1.2 * g_lo                                       # treble lifted against the bass
    h.tx_preemph, h.tx_comp = False, True
    quiet = (300 * np.sin(np.arange(960) / 5)).astype(np.int16)
    for _ in range(20):
        z = h._shape(quiet)
    assert np.abs(z).max() > np.abs(quiet).max()                  # makeup gain lifts quiet speech
    assert np.abs(h._shape((30000 * np.sin(np.arange(960) / 5)).astype(np.int16))).max() < 32000   # limiter
