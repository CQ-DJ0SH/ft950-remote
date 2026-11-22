"""Station statistics: PSK Reporter only as its author asks, the panel figures."""
import io
import time
import urllib.request

from app import stats

XML = b"""<?xml version="1.0"?>
<receptionReports>
  <lastSequenceNumber value="1000"/>
  <receptionReport receiverCallsign="DL1EYR" receiverLocator="JN58SD" senderCallsign="DJ0SH" frequency="7074400" flowStartSeconds="%d" mode="FT8" receiverDXCC="Fed. Rep. of Germany" sNR="-18" />
  <receptionReport receiverCallsign="CT1CJJ" receiverLocator="IN50RU" senderCallsign="DJ0SH" frequency="7074405" flowStartSeconds="%d" mode="FT8" receiverDXCC="Portugal" sNR="-14" />
  <receptionReport receiverCallsign="K1ABC" receiverLocator="FN42" senderCallsign="DJ0SH" frequency="14074500" flowStartSeconds="%d" mode="FT8" receiverDXCC="United States" sNR="-20" />
</receptionReports>"""


def _stats(vfo=7074000):
    return stats.StationStats(lambda: "DJ0SH", lambda: "JO62qk", lambda: vfo, lambda: False,
                              lambda c: {"views": "1234"}, lambda: True)


def test_psk_reporter_only_after_tx_and_not_before_5_minutes():
    s = _stats()
    now = 10_000.0
    assert not s._due(now)                          # no transmission: no query
    s._first_tx = now
    assert not s._due(now + 60)                     # 5 minutes after the transmission
    assert s._due(now + 300)
    s._last_query, s._first_tx = now + 300, now + 310
    assert not s._due(now + 500)                    # and 5 minutes since the last query
    assert s._due(now + 610)


def test_reports_parsed_and_counted_for_the_vfo_band(monkeypatch):
    t = int(time.time()) - 120
    raw = XML % (t, t, t)
    seen = {}

    class R(io.BytesIO):
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def fake(req, timeout=0):
        seen["url"] = req.full_url
        return R(raw)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    s = _stats()
    s._query("DJ0SH")
    assert "flowStartSeconds" in seen["url"] and "noactive=1" in seen["url"]
    v = s.psk_view()
    assert v["band"] == "40m" and v["n"] == 2 and v["countries"] == 2 and v["far_call"] == "CT1CJJ"
    s._query("DJ0SH")                               # incremental from now on
    assert "lastseqno=1000" in seen["url"]


def test_qrz_views_since_the_day_began(monkeypatch, tmp_path):
    from app import runtime
    monkeypatch.setattr(runtime, "FILE", tmp_path / "runtime.json")
    s = _stats()
    s._qrz_day = {}
    s._qrz("DJ0SH")
    s._qrz_lookup = lambda c: {"views": "1240"}
    s._qrz("DJ0SH")
    v = s.qrz_view()
    assert v["views"] == 1240 and v["today"] == 6


def test_switched_off_shows_it(monkeypatch):
    s = _stats()
    s.psk_on, s.qrz_on = False, False
    v = s.view()
    assert v["psk"]["enabled"] is False and v["qrz"]["enabled"] is False
