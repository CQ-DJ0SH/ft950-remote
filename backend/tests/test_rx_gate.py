"""The keyboard-mode receivers hear silence while we transmit (no echo of our own text)."""
import numpy as np

from app import main


def test_receivers_get_silence_while_and_just_after_transmitting(monkeypatch):
    got = []
    feed = main._rx_only(lambda m, t=0.0: got.append(bool(m.any())))
    chunk = np.ones(960, dtype=np.int16)
    monkeypatch.setattr(main.service, "_state", {"tx": True})
    feed(chunk)                                   # transmitting: silence
    monkeypatch.setattr(main.service, "_state", {"tx": False})
    monkeypatch.setattr(main.tx, "state", {"active": None})
    feed(chunk)                                   # within the hold-off after TX: still silence
    monkeypatch.setattr(main, "_rx_quiet_until", 0.0)
    feed(chunk)                                   # receiving again
    monkeypatch.setattr(main.tx, "state", {"active": "rtty"})
    feed(chunk)                                   # our RTTY keyed (before the radio reports TX)
    assert got == [False, False, True, False]
