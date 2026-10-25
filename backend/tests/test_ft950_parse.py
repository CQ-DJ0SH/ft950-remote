"""Offline tests — fixtures are real answers captured from the radio."""
import pytest

from app.ft950 import (ChannelInfo, FT950Error, format_channel, parse_channel,
                       width_column, width_hz, width_index_for)

IF_LIVE = "00414080000+000000C00000"     # IF; on 14.080 DATA-U
OI_LIVE = "00429540000-999000B01000"     # OI; VFO-B 29.540 FM-N, CTCSS


def test_parse_if_live():
    c = parse_channel(IF_LIVE)
    assert c == ChannelInfo(channel=4, freq_hz=14_080_000, clar_hz=0,
                            rx_clar=False, tx_clar=False, mode="DATA-U",
                            mem_mode="VFO", ctcss=0, tone=0, shift=0)


def test_parse_oi_live_negative_clar_and_ctcss():
    c = parse_channel(OI_LIVE)
    assert (c.freq_hz, c.clar_hz, c.mode, c.ctcss) == (29_540_000, -9990, "FM-N", 1)


@pytest.mark.parametrize("payload", [IF_LIVE, OI_LIVE])
def test_format_roundtrip(payload):
    assert format_channel(parse_channel(payload)) == payload


def test_negative_clar():
    assert parse_channel("00014074000-005010200000").clar_hz == -50


def test_unknown_mode_is_kept_not_fatal():
    assert parse_channel("00014074000+000000Z00000").mode == "Z"


@pytest.mark.parametrize("bad", ["", "0041408", IF_LIVE + "0", "00414080000+0000xxC00000"])
def test_malformed_raises(bad):
    with pytest.raises(FT950Error):
        parse_channel(bad)


def test_data_u_uses_pkt_column_measured():
    # Measured on the radio: SH011 = 1700 Hz, SH013 = 2400 Hz, SH020 ignored.
    assert width_column("DATA-U", False) == "PKT-W"
    assert width_hz("DATA-U", False, 11) == 1700
    assert width_hz("DATA-U", False, 13) == 2400
    assert width_hz("DATA-U", False, 20) is None


def test_usb_reaches_3000():
    assert width_hz("USB", False, 20) == 3000
    assert width_index_for("USB", False, 3000) == 20


def test_width_index_for_data_u_caps_at_2400():
    assert width_index_for("DATA-U", False, 3000) == 13


def test_fm_has_no_width():
    assert width_column("FM", False) is None
    assert width_index_for("FM", False, 2400) is None


class _FakeSerial:
    """Answers queries from a dict, like the radio does on the wire."""
    def __init__(self, answers):
        self.answers, self.out, self.is_open = answers, b"", True
    def reset_input_buffer(self): pass
    def flush(self): pass
    def write(self, data):
        cmd = data.decode().rstrip(";")
        self.out = (self.answers.get(cmd, "") + "").encode()
    def read(self, n):
        b, self.out = self.out[:1], self.out[1:]
        return b


def _radio(answers):
    from app.ft950 import FT950
    r = FT950("fake", timeout=0.1)
    r._serial = _FakeSerial(answers)
    return r


def test_read_memory_live_answers():
    # Real answers: P1 shows the current channel (004), not the requested one;
    # an empty channel answers "?;".
    r = _radio({"MR005": "MR00414074000+000000C00000;", "MR011": "?;"})
    c = r.read_memory(5)
    assert (c.channel, c.freq_hz, c.mode) == (5, 14_074_000, "DATA-U")
    assert r.read_memory(11) is None


def test_question_mark_is_immediate_not_a_timeout():
    import time
    from app.ft950 import FT950Rejected
    r = _radio({"MR012": "?;"})
    t = time.monotonic()
    with pytest.raises(FT950Rejected):
        r.query("MR012")
    assert time.monotonic() - t < 0.1


def test_antenna_and_tuner_read_back():
    r = _radio({"AN0": "AN020;", "AC": "AC001;"})
    r.set_antenna(2)                 # AN02 → reads back AN020
    r.set_tuner(True)                # AC001 → reads back AC001
    with pytest.raises(ValueError):
        r.set_antenna(3)


def test_write_memory_sends_mw_and_verifies():
    from app.ft950 import FT950Error
    sent = []
    r = _radio({"MR007": "MR00714074000+000000C00000;"})
    orig = r._serial.write
    r._serial.write = lambda d: (sent.append(d.decode()), orig(d))[1]
    c = r.write_memory(7, 14_074_000, "DATA-U")
    assert "MW00714074000+000000C00000;" in sent
    assert (c.channel, c.freq_hz, c.mode) == (7, 14_074_000, "DATA-U")
    with pytest.raises(FT950Error):          # read-back still shows the old value
        r.write_memory(7, 7_074_000, "DATA-U")
    with pytest.raises(ValueError):
        r.write_memory(7, 7_074_000, "AM-N")


def test_ctcss_set_and_verify():
    from app.ft950 import CTCSS_TONES
    r = _radio({"CN0": "CN012;", "CT0": "CT02;"})
    r.set_ctcss(mode=2, tone=12)                  # 100.0 Hz, encode only
    assert CTCSS_TONES[12] == 100.0 and len(CTCSS_TONES) == 50
    with pytest.raises(ValueError):
        r.set_ctcss(tone=50)


def test_vrf_switch_checks_the_on_off_digit():
    from app.ft950 import FT950Error
    r = _radio({"VF0": "VF01128 1;"})
    r.set_vrf(True)
    with pytest.raises(FT950Error):
        r.set_vrf(False)                      # radio still reports on


def test_read_slow_survives_values_missing_in_am_fm():
    # FM: no width control — SH0 (and here notch/contour) answer "?;". The
    # read must report them as missing instead of failing as a whole.
    from app.ft950 import ChannelInfo, FT950Rejected
    r = _radio({})
    missing = {"SH0", "BP00", "BP01", "CO00", "CO01"}

    def query(cmd):
        if cmd in missing:
            raise FT950Rejected(cmd)
        return cmd + "0000"

    def num(cmd, skip):
        if cmd in missing:
            raise FT950Rejected(cmd)
        return 0
    r.query, r._num = query, num
    r.get_narrow = lambda: False
    r.opposite = lambda: ChannelInfo(0, 7074000, 0, False, False, "FM", "VFO", 0, 0, 0)
    for name in ("get_ifshift", "get_agc", "get_roofing", "get_preamp", "get_att", "get_power", "pll_unlocked"):
        setattr(r, name, lambda: 0)
    r.get_level = r.meter = lambda *_: 0
    r._flag = lambda *_: False
    d = r.read_slow("FM")
    assert d["width"] == {"index": None, "hz": None, "column": d["width"]["column"]}
    assert d["notch"] is False and d["contour"] is False and d["contour_pos"] is None


def test_voice_profile_sideband_by_band():
    from app.radio_service import voice_profile
    md = lambda f: voice_profile(f)[0][1]
    assert md(7_100_000) == "MD08" and md(3_700_000) == "MD08"        # DATA-L below 10 MHz
    assert md(14_200_000) == "MD0C" and md(5_357_000) == "MD0C"       # DATA-U above, and on 60 m


def test_rx_clarifier_switch_verified():
    from app.ft950 import FT950Error
    r = _radio({"RT": "RT1;"})
    r.set_rx_clar(True)
    with pytest.raises(FT950Error):
        r.set_rx_clar(False)                  # radio still reports on


def test_write_memory_with_ctcss():
    sent = []
    r = _radio({"MR009": "MR00929640000+000000B02081;"})
    orig = r._serial.write
    r._serial.write = lambda d: (sent.append(d.decode()), orig(d))[1]
    c = r.write_memory(9, 29_640_000, "FM-N", ctcss=2, tone=8)     # ENC 88.5 Hz
    assert "MW00929640000+000000B02081;" in sent                    # shift (1) of the old channel kept
    assert (c.ctcss, c.tone) == (2, 8)
    with pytest.raises(ValueError):
        r.write_memory(9, 29_640_000, "FM-N", ctcss=3)


def test_recall_sets_ctcss_vfo_b_and_split():
    sent = []
    r = _radio({"MR009": "MR00929640000+000000B02081;", "FA": "FA29640000;", "MD0": "MD0B;",
                "CN0": "CN008;", "CT0": "CT02;", "FB": "FB29540000;", "FT": "FT1;"})
    orig = r._serial.write
    r._serial.write = lambda d: (sent.append(d.decode()), orig(d))[1]
    r.recall_to_vfo(9, vfo_b_hz=29_540_000, split=True)
    for cmd in ("FA29640000;", "MD0B;", "CN008;", "CT02;", "AB;", "FB29540000;", "FT3;"):
        assert cmd in sent
    assert sent.index("AB;") < sent.index("FB29540000;")


def test_recall_ssb_skips_ctcss_and_clears_split():
    sent = []
    r = _radio({"MR003": "MR00314285000+000000200000;", "FA": "FA14285000;", "MD0": "MD02;", "FT": "FT0;"})
    orig = r._serial.write
    r._serial.write = lambda d: (sent.append(d.decode()), orig(d))[1]
    r.recall_to_vfo(3)
    assert "FT2;" in sent and not any(s.startswith(("CT", "CN", "AB", "FB")) for s in sent)
