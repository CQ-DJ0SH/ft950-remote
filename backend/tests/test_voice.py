import numpy as np

from app.voice import BLOCK, VoiceHub, force_opus_mono


class _Cap:
    def subscribe(self, fn): self.fn = fn


def _hub(**kw):
    return VoiceHub(_Cap(), lambda: "null", **kw)


def test_force_opus_mono_rewrites_fmtp():
    sdp = "a=rtpmap:111 opus/48000/2\r\na=fmtp:111 minptime=10;stereo=1\r\n"
    out = force_opus_mono(sdp)
    assert "stereo=1" not in out and "stereo=0;sprop-stereo=0" in out


def test_mic_is_metered_but_not_queued_while_closed():
    h = _hub(); h.active = "a"
    h.push_tx(np.full(BLOCK, 3000, np.int16), "a")
    assert h.levels["tx"] > -30 and h.tx_backlog_ms() == 0


def test_tx_buffer_caps_latency():
    h = _hub(tx_buffer_ms=100)
    h._tx_open = h._mic_on = True; h.active = "a"
    for _ in range(20):                       # 400 ms of mic audio arriving at once
        h.push_tx(np.ones(BLOCK, np.int16), "a")
    assert h.tx_backlog_ms() <= 100


def test_prefill_then_play_then_refill():
    h = _hub(tx_buffer_ms=150)                # prime = 60 ms = 3 blocks
    h._tx_open = h._mic_on = True; h.active = "a"
    h.push_tx(np.full(BLOCK, 100, np.int16), "a")
    assert not h._pull().any()                # 20 ms only: still priming (silence)
    for _ in range(2):
        h.push_tx(np.full(BLOCK, 100, np.int16), "a")
    assert (h._pull() == 100).all()           # 60 ms reached: plays
    h._pull(); h._pull()                      # drain
    assert not h._pull().any() and h._priming # ran dry: silence, re-priming


def test_rx_fanout_applies_gain():
    h = _hub(rx_gain=2.0)
    q = h.rx_subscribe()
    h._on_rx(np.full(BLOCK, 1000, np.int16), 0.0)
    assert np.frombuffer(q[0], np.int16)[0] == 2000


def test_mic_echo_records_then_replays_without_tx():
    h = _hub(); h.active = "a"
    q = h.rx_subscribe("a")
    other = h.rx_subscribe("b")
    h.set_mic_test(True)
    h.push_tx(np.full(BLOCK, 1234, np.int16), "a")
    assert h.tx_backlog_ms() == 0                     # never queued for TX
    h._on_rx(np.full(BLOCK, 50, np.int16), 0)
    assert not np.frombuffer(q.pop(), np.int16).any() # RX muted while recording
    h.set_mic_test(False)
    assert h.echo_busy
    h._on_rx(np.full(BLOCK, 50, np.int16), 0)
    e = np.frombuffer(q.pop(), np.int16)                       # the echo, brought to listening level
    assert e.min() == e.max() > 1234
    assert (np.frombuffer(other.pop(), np.int16) == 50).all() # others hear the radio
    h._on_rx(np.full(BLOCK, 50, np.int16), 0)
    assert not h.echo_busy                            # done, back to RX


def test_only_the_keying_browsers_mic_reaches_tx():
    import pytest
    h = _hub()
    h.mic_levels = {"a": -120.0, "b": -120.0}
    with pytest.raises(RuntimeError):         # two mics, no id: refuse
        h.claim_mic(None)
    h.claim_mic("a")
    h._tx_open = h._mic_on = True
    h.push_tx(np.full(BLOCK, 111, np.int16), "a")
    h.push_tx(np.full(BLOCK, 999, np.int16), "b")   # other browser: metered only
    assert h.tx_backlog_ms() == 20 and h.mic_levels["b"] > -60


def test_roger_beep_shape():
    from app.voice import RATE, render_roger
    b = render_roger()
    assert abs(len(b) / RATE - 0.24) < 0.002            # 3 × 60 ms + 2 × 30 ms
    assert abs(b[0]) < 50 and abs(b[-1]) < 50            # soft edges, no click
    assert np.max(np.abs(b)) <= 0.28 * 32767 + 1


def test_release_gates_mic_and_queues_roger():
    h = _hub(roger=True)
    h.mic_levels = {"a": -120.0}; h.claim_mic("a")
    h._tx_open = h._mic_on = True
    h.mic_off()
    h.push_tx(np.full(BLOCK, 500, np.int16), "a")       # after release: not queued
    assert h.tx_backlog_ms() == 0
    assert abs(h.play_roger() - 0.24) < 0.002 and h.tx_backlog_ms() == 240


def test_echo_replay_is_brought_to_listening_level():
    from app.voice import ECHO_TARGET_DBFS, echo_playback
    t = np.arange(48_000 * 2) / 48_000
    quiet = (0.03 * np.sin(2 * np.pi * 300 * t) * (np.sin(2 * np.pi * 1.5 * t) > 0) * 32767).astype(np.int16)
    y, info = echo_playback(quiet)
    y = y.astype(np.float32) / 32768
    rms = 20 * np.log10(np.sqrt(np.mean(y[np.abs(y) > 1e-3] ** 2)))
    assert abs(rms - ECHO_TARGET_DBFS) < 1.5              # speech, not the pauses, sets the level
    spike = quiet.copy(); spike[1000:1010] = 32000        # one click must not keep the replay quiet
    y2, info2 = echo_playback(spike)
    assert info2["gain_db"] > 10 and np.abs(y2.astype(np.float32)).max() / 32768 <= 0.9
    assert not echo_playback(np.zeros(0, np.int16))[0].size


def test_preemphasis_lifts_the_treble_for_ssb():
    from app.tx_dsp import PREEMPH_REF_HZ, PREEMPH_US, PreEmphasis
    fs = 48_000
    def db_out(hz):
        p = PreEmphasis(PREEMPH_US, fs, PREEMPH_REF_HZ)
        x = (8000 * np.sin(2 * np.pi * hz * np.arange(fs) / fs)).astype(np.int16)
        y = np.concatenate([p.process(b) for b in np.split(x, 50)]).astype(np.float64)[fs // 10:]
        return 20 * np.log10(np.sqrt(np.mean(y ** 2)) / (8000 / np.sqrt(2)))
    assert abs(db_out(1000)) < 0.3                  # loudness unchanged at 1 kHz
    assert db_out(2700) > 6                         # treble clearly lifted
    assert db_out(300) < -3                         # lows a little lower


def test_1750_tone_replaces_the_mic_while_keyed_and_disarms_on_release():
    h = _hub(); h.active = "a"
    h._tx_open = h._mic_on = True
    h.tone_1750, h.tone_level = True, 0.5
    h.push_tx(np.full(BLOCK, 3000, np.int16), "a")
    a = np.concatenate([h._pull() for _ in range(5)]).astype(np.float64)
    spec = np.abs(np.fft.rfft(a))
    assert abs(np.argmax(spec) * 48_000 / len(a) - 1750) < 15       # a 1750 Hz tone, not the mic
    assert 0.45 < np.abs(a).max() / 32767 < 0.5                     # at the TX drive
    assert np.abs(np.diff(a)).max() < 0.5 * 32767 * 2 * np.pi * 1750 / 48_000 * 1.05   # phase-continuous, no clicks
    h.mic_off()                                                     # PTT released
    assert h.tone_1750 is False


def test_preroll_head_start_is_given_back_in_speech_pauses():
    # the pre-roll lets the queue exceed the cap at key-up; it must not stay
    # that full for the whole over (that delayed speech and the release)
    h = _hub(tx_buffer_ms=100)
    h._tx_open = h._mic_on = True; h.active = "a"
    speech, silence = np.full(BLOCK, 3000, np.int16), np.zeros(BLOCK, np.int16)
    with h._lock:
        h._tx.extend([speech] * 25); h._grace = 25 * BLOCK      # 500 ms head start
    for _ in range(30):                       # a pause, while the codec plays in real time
        h.push_tx(silence, "a"); h._pull()
    h.push_tx(silence, "a")                   # the next block re-checks the head start
    assert h._grace == 0 and h.tx_backlog_ms() <= 100
    h.push_tx(speech, "a")                    # speech is never skipped
    assert h.tx_backlog_ms() > 0


def test_release_trims_trailing_silence_and_plays_out_a_short_rest():
    h = _hub(tx_buffer_ms=150)
    h._tx_open = h._mic_on = True; h.active = "a"
    h.push_tx(np.full(BLOCK, 3000, np.int16), "a")          # 20 ms speech (still priming)
    for _ in range(4):
        h.push_tx(np.zeros(BLOCK, np.int16), "a")           # the breath before letting go
    h.mic_off()
    assert h.trim_tail() == 80 and h.tx_backlog_ms() == 20
    assert h._pull().any() and h.tx_backlog_ms() == 0       # no waiting for a pre-fill now
