from app.dxcc import Dxcc
from app.logbook import WorkedIndex, adif_mode, band_for_hz, build_adif, parse_adif


def test_ft4_is_mfsk_submode():
    assert adif_mode("FT4") == ("MFSK", "FT4")
    assert adif_mode("ft8") == ("FT8", "")


def test_adif_lengths_are_bytes_and_records_stay_apart():
    a = build_adif({"call": "DL1ABC", "mode": "FT4", "name": "Jörg", "rst_sent": "-12"})
    assert "<NAME:5>Jörg" in a
    recs = parse_adif("header<EOH>" + a + build_adif({"call": "K1ABC", "mode": "FT8"}))
    assert [r["CALL"] for r in recs] == ["DL1ABC", "K1ABC"]
    assert recs[0]["NAME"] == "Jörg" and recs[0]["SUBMODE"] == "FT4"
    assert "NAME" not in recs[1]


def test_parse_wavelog_style_export():
    txt = ("Wavelog export\n<ADIF_VER:5>3.1.4 <EOH>\n"
           "<call:5>OH6IH <band:3>20m <mode:3>FT8 <qso_date:8>20261024 <eor>\n"
           "<call:7>RA3FD/P <band:3>40m <mode:4>MFSK <submode:3>FT4 <eor>\n")
    recs = parse_adif(txt)
    assert [(r["CALL"], r["BAND"]) for r in recs] == [("OH6IH", "20m"), ("RA3FD/P", "40m")]


def test_band_for_hz():
    assert band_for_hz(14_074_000) == "20m" and band_for_hz(50_313_000) == "6m"
    assert band_for_hz(None) == ""


def test_worked_index(tmp_path, monkeypatch):
    import app.logbook as lb
    monkeypatch.setattr(lb, "WORKED_FILE", tmp_path / "w.json")
    w = WorkedIndex(Dxcc())
    w.add("OH6IH", "20m")
    st = w.status("OH6IH", "20m", "Finland")
    assert st == {"call": True, "call_band": True, "dxcc": True, "dxcc_band": True}
    st = w.status("OH2XYZ", "40m", "Finland")
    assert st == {"call": False, "call_band": False, "dxcc": True, "dxcc_band": False}
    w.save()
    assert WorkedIndex(Dxcc()).status("OH6IH", "20m", "Finland")["call_band"]


def test_qrz_logbook_answer_parsing(monkeypatch):
    from app.logbook import Qrz
    q = Qrz(api_key="KEY")
    monkeypatch.setattr(q, "_logbook", lambda p: {"RESULT": "OK", "LOGID": "123", "COUNT": "1"})
    assert q.log_qso("<CALL:5>OH6IH<EOR>") == {"ok": True, "message": "QRZ-Log #123"}
    monkeypatch.setattr(q, "_logbook", lambda p: {"RESULT": "FAIL", "REASON": "duplicate"})
    assert q.log_qso("x") == {"ok": False, "message": "QRZ logbook: duplicate"}


def test_reset_index_is_not_synced(tmp_path, monkeypatch):
    import app.logbook as lb
    monkeypatch.setattr(lb, "WORKED_FILE", tmp_path / "w.json")
    w = WorkedIndex(Dxcc())
    w.add("OH6IH", "20m"); w.synced_at = 123.0
    w.reset()
    assert w.synced_at is None and w.count == 0


def test_log_qso_to_one_target_only(tmp_path):
    from app.logbook import LogBook

    class WL:
        sent = []
        station_id = "1"
        def configured(self): return True
        def log_qso(self, adif, power_w=None): WL.sent.append(adif); return {"ok": True, "message": "ok"}

    class Q:
        sent = []
        def configured(self): return False
        def upload_ready(self): return True
        def log_qso(self, adif): Q.sent.append(adif); return {"ok": True, "message": "QRZ-Log #1"}

    class Worked:
        added = []
        def add(self, call, band): Worked.added.append(call)
        def save(self): pass

    class Dx:
        def lookup(self, call): return "Poland"

    lb = LogBook.__new__(LogBook)                      # no runtime.json, no network
    lb.wavelog, lb.qrz, lb.worked, lb.dxcc = WL(), Q(), Worked(), Dx()
    lb.qrz_before_log, lb._recent = False, []
    lb._save_recent = lambda: None
    q = {"call": "sp2ddd", "freq_hz": 14_074_000, "mode": "FT8"}

    r = lb.log_qso(dict(q), station_callsign="DJ0SH", my_grid="JO62qk", to=("qrz",))
    assert Q.sent and not WL.sent                        # QRZ only
    assert r["entry"]["wavelog"] is None and r["entry"]["qrz"] is True and Worked.added == ["SP2DDD"]
    r = lb.log_qso(dict(q), station_callsign="DJ0SH", my_grid="JO62qk", to=("wavelog",))
    assert len(WL.sent) == 1 and len(Q.sent) == 1 and r["entry"]["qrz"] is None and r["ok"] is True
