"""PSK Reporter datagrams and the reporting rules — nothing is sent."""
import struct

from app import pskreporter as pr
from app.digi import analyse


def _hex(s):
    return bytes.fromhex(s.replace(" ", ""))


def test_templates_match_the_published_bytes():
    # receiver template B from pskreporter.info/pskdev.html
    assert pr.RX_TEMPLATE == _hex("00 03 00 2C 99 92 00 04 00 01 80 02 FF FF 00 00 76 8F 80 04 FF FF 00 00 76 8F"
                                  "80 08 FF FF 00 00 76 8F 80 09 FF FF 00 00 76 8F 00 00")
    # sender: the published fields in their order, with sNR and senderLocator (no iMD)
    assert pr.TX_TEMPLATE == _hex("00 02 00 3C 99 93 00 07 80 01 FF FF 00 00 76 8F 80 05 00 04 00 00 76 8F"
                                  "80 06 00 01 00 00 76 8F 80 0A FF FF 00 00 76 8F 80 0B 00 01 00 00 76 8F"
                                  "80 03 FF FF 00 00 76 8F 00 96 00 04")


def _dec(msg, snr=-10, df=1200, t0=1000.0, ap=None):
    return {"msg": msg, "snr": snr, "df": df, "t0": t0, "rf_hz": 7_074_000 + df, "ap": ap, **analyse(msg)}


def _rep(**kw):
    sent = []
    r = pr.PskReporter(lambda: ("DJ0SH", "JO62qk"), enabled=True, antenna="Dipole", send=sent.append, **kw)
    return r, sent


def test_datagram_layout_and_sets_are_padded():
    r, _ = _rep()
    r.add([_dec("CQ EA8ABC IL18", snr=-3), _dec("DJ0SH K1ABC FN42", snr=5, df=900)], "FT8")
    [dg] = r.build(now=2000.0)
    ver, length, t, seq, dom = struct.unpack(">HHIII", dg[:16])
    assert (ver, length, t, seq) == (10, len(dg), 2000, 0) and dom == r.domain
    sets, i = [], 16
    while i < len(dg):
        sid, slen = struct.unpack(">HH", dg[i:i + 4])
        assert slen % 4 == 0
        sets.append((sid, dg[i:i + slen])); i += slen
    assert [s for s, _ in sets] == [3, 2, 0x9992, 0x9993]
    rx = sets[2][1]
    assert b"\x05DJ0SH\x06JO62qk\x0cFT950 Remote\x06Dipole" in rx
    tx = sets[3][1]
    assert b"\x06EA8ABC" + struct.pack(">I", 7_075_200) + b"\xfd\x03FT8\x01\x04IL18" + struct.pack(">I", 1000) in tx
    assert b"\x05K1ABC" + struct.pack(">I", 7_074_900) + b"\x05\x03FT8\x01\x04FN42" in tx
    assert r.seq == 2                                    # counts records, not datagrams


def test_templates_only_in_the_first_three_then_hourly():
    r, _ = _rep()
    has = []
    for k in range(5):
        r.add([_dec(f"CQ K{k}ABC FN42")], "FT8")
        has.append(pr.RX_TEMPLATE in r.build(now=1000.0 + k * 300)[0])
    assert has == [True, True, True, False, False]
    r.add([_dec("CQ W9XYZ EN52")], "FT8")
    assert pr.RX_TEMPLATE in r.build(now=1000.0 + 2 * 300 + 3600)[0]


def test_once_per_hour_per_band_and_mode_unless_the_locator_is_new():
    r, _ = _rep()
    assert r.add([_dec("SP9DPI GU4WRP RR73")], "FT8") == 1          # no locator known yet
    r.build(now=1000.0)
    import time
    for k in r.reported.values():
        k["t"] = time.time()
    assert r.add([_dec("SP9DPI GU4WRP 73")], "FT8") == 0             # same band and mode: not again
    assert r.add([_dec("CQ GU4WRP IN89")], "FT8") == 1               # locator now known: once more
    assert r.add([_dec("CQ GU4WRP IN89")], "FT4") == 1               # other mode
    assert len(r.pending) == 2


def test_skips_own_call_unsure_and_unresolved_decodes_and_when_off():
    r, _ = _rep()
    assert r.add([_dec("CQ DJ0SH JO62"), _dec("CQ K1ABC FN42", ap="?"),
                  _dec("<...> K1ABC -05"), _dec("DJ0SH <...> -05"), _dec("TNX 73 GL"),
                  _dec("CQ W1AW FN31")], "FT8", me="DJ0SH") == 1
    assert [k[0] for k in r.pending] == ["W1AW"]
    r.enabled = False
    assert r.add([_dec("CQ W9XYZ EN52")], "FT8") == 0


def test_many_spots_split_into_full_datagrams():
    r, _ = _rep()
    r.add([_dec(f"CQ K{k % 10}A{chr(65 + k // 10 % 26)}{chr(65 + k // 260)} FN42", df=200 + k) for k in range(200)], "FT8")
    dgs = r.build(now=1000.0)
    assert len(dgs) > 1 and all(len(d) <= pr.MAX_DATAGRAM for d in dgs)
    assert r.seq == 200 and not r.pending
