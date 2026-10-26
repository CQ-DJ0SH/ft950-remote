"""Sequencer state machine with fake decoder/transmitter — no radio, no time."""
import asyncio

import pytest

from app.sequencer import Sequencer, report


class _Mgr:
    async def broadcast(self, m): pass


class _Svc:
    status = {"vfo": {"freq_hz": 14_074_000}, "power": 15}


class _Tx:
    service = _Svc()


class _Log:
    auto_log = False


class _Digi:
    config = {"low_hz": 200, "high_hz": 3000}
    tx_periods: set = set()
    def current_mode(self): return "FT8"


class _Set:
    my_call, my_grid = "DJ0SH", "JO62qk"


def _seq():
    s = Sequencer(_Digi(), _Tx(), _Log(), _Mgr(), _Set())
    s.kind, s.mode = "answer", "FT8"
    return s


def _d(msg, snr=-10, t0=15.0, **kw):
    from app.digi import analyse
    d = {"msg": msg, "snr": snr, "dt": 0.1, "df": 1200, "t0": t0, "mode": "FT8", "period_utc": "12:00:15.0"}
    d.update(analyse(msg), **kw)
    return d


def test_report_format():
    assert report(-5) == "-05" and report(12) == "+12" and report(-45) == "-30"


def test_answer_flow():
    s = _seq()
    s.session = s._new_session(_d("CQ OH6IH KP13"))
    s.step = "call"
    assert s._next_message() == "OH6IH DJ0SH JO62"
    asyncio.run(s.on_decodes([_d("DJ0SH OH6IH -07", t0=30.0)], 30.0, "FT8", True))
    assert s.step == "rreport" and s._next_message() == "OH6IH DJ0SH R-10"
    asyncio.run(s.on_decodes([_d("DJ0SH OH6IH RR73", t0=60.0)], 60.0, "FT8", True))
    assert s.step == "73" and s._next_message() == "OH6IH DJ0SH 73"


def test_cq_flow_picks_strongest_and_repeats_rr73_only_on_request():
    s = _seq(); s.kind, s.step = "cq", "cq"
    assert s._next_message() == "CQ DJ0SH JO62"
    asyncio.run(s.on_decodes([_d("DJ0SH K1ABC FN42", snr=-15), _d("DJ0SH DL2XYZ JO40", snr=-3)], 30.0, "FT8", True))
    assert s.session["call"] == "DL2XYZ" and s._next_message() == "DL2XYZ DJ0SH -03"
    s._log_tx(s._next_message(), 45.0, {})               # the report goes out: now fixed
    asyncio.run(s.on_decodes([_d("DJ0SH DL2XYZ R-08", t0=60.0)], 60.0, "FT8", True))
    assert s.step == "rr73" and s._next_message() == "DL2XYZ DJ0SH RR73"
    assert s.session["report_sent"] == "-03"             # unchanged by their later SNR
    s._complete(); s.step = "done"                       # what the loop does after sending
    assert s._next_message() is None and s.session["state"] == "done"
    assert s.session["qso"]["rst_sent"] == "-03" and s.session["qso"]["rst_rcvd"] == "-08"
    asyncio.run(s.on_decodes([_d("DJ0SH DL2XYZ R-08", t0=90.0)], 90.0, "FT8", True))
    assert s.step == "rr73"                              # they missed RR73: send it again


def test_closing_message_rrr_setting():
    s = _seq(); s.kind, s.step = "cq", "cq"; s.closing = "RRR"
    asyncio.run(s.on_decodes([_d("DJ0SH DL2XYZ JO40", snr=-3)], 30.0, "FT8", True))
    s._log_tx(s._next_message(), 45.0, {})
    asyncio.run(s.on_decodes([_d("DJ0SH DL2XYZ R-08", t0=60.0)], 60.0, "FT8", True))
    assert s.step == "rr73" and s._next_message() == "DL2XYZ DJ0SH RRR"
    s._complete(); s.step = "done"
    asyncio.run(s.on_decodes([_d("DJ0SH DL2XYZ 73", t0=90.0)], 90.0, "FT8", True))
    assert s.step == "done" and s._next_message() is None


def test_other_stations_and_duplicates_are_ignored():
    s = _seq()
    s.session = s._new_session(_d("CQ OH6IH KP13")); s.step = "call"
    asyncio.run(s.on_decodes([_d("DL1ABC OH6IH -07"), _d("DJ0SH K1ABC -01")], 30.0, "FT8", True))
    assert s.step == "call"
    rep_ = _d("DJ0SH OH6IH -07", t0=30.0)
    asyncio.run(s.on_decodes([rep_], 30.0, "FT8", True))
    asyncio.run(s.on_decodes([dict(rep_)], 30.0, "FT8", False))   # full pass repeats it
    assert sum(m["dir"] == "rx" and m["msg"] == "DJ0SH OH6IH -07" for m in s.session["msgs"]) == 1


def test_clear_sessions_keeps_running_qso():
    import asyncio
    from app.sequencer import Sequencer
    s = Sequencer.__new__(Sequencer)
    from collections import deque
    running = {"state": "running"}
    s.sessions = deque([running, {"state": "done"}, {"state": "no reply"}], maxlen=50)
    s.session = running
    async def publish():
        return {"n": len(s.sessions)}
    s.publish = publish
    assert asyncio.run(s.clear_sessions()) == {"n": 1} and s.sessions[0] is running
    s.session = None
    assert asyncio.run(s.clear_sessions()) == {"n": 0}


def test_auto_answer_takes_the_strongest_caller_when_idle():
    s = Sequencer(_Digi(), _Tx(), _Log(), _Mgr(), _Set())
    started = []
    async def fake_answer(d, hz):
        started.append((d["from"], hz))
    s.answer = fake_answer
    decs = [_d("DJ0SH IK4RVY JN54", snr=-15), _d("DJ0SH SP3ABC JO82", snr=-4), _d("CQ DL1XYZ JO40", snr=5)]
    asyncio.run(s.on_decodes(decs, 15.0, "FT8", False))
    assert started == []                                  # switch off: nothing happens
    asyncio.run(s.set_auto(True, 1750))
    asyncio.run(s.on_decodes(decs, 15.0, "FT8", True))    # early/partial pass: ignored
    assert started == []
    asyncio.run(s.on_decodes(decs, 15.0, "FT8", False))
    assert started == [("SP3ABC", 1750.0)]                # strongest station calling me


def test_tx_frequency_is_kept_inside_the_radio_tx_filter():
    from app.tx import clamp_tx_hz
    assert clamp_tx_hz(2900) == 2700 and clamp_tx_hz(50) == 200 and clamp_tx_hz(1425) == 1425
    s = _seq()
    s.kind = None
    s._loop = lambda: asyncio.sleep(0)               # no transmitting in the test
    asyncio.run(s.answer(_d("CQ OH5KNL KP30", df=2900), 2900))
    assert s.tx_hz == 2700


def test_repeated_rrr_after_our_73_is_answered_again():
    s = _seq()
    s.session = s._new_session(_d("CQ OH6IH KP13"))
    s.step = "rreport"
    asyncio.run(s.on_decodes([_d("DJ0SH OH6IH RRR", t0=60.0)], 60.0, "FT8", True))
    assert s.step == "73"
    s._log_tx(s._next_message(), 75.0, {}); s.tries += 1
    s._complete(); s.step = "done"                       # what the loop does after sending 73
    asyncio.run(s.on_decodes([_d("DJ0SH OH6IH RRR", t0=90.0)], 90.0, "FT8", True))
    assert s.step == "73" and s._next_message() == "OH6IH DJ0SH 73"   # they missed it: again
    s._log_tx(s._next_message(), 105.0, {}); s.step = "done"
    s.tries = 99                                          # partner never hears us: no endless loop
    asyncio.run(s.on_decodes([_d("DJ0SH OH6IH RR73", t0=120.0)], 120.0, "FT8", True))
    assert s.step == "done"


def _w(call_band=False, call=False, dxcc=True, dxcc_band=True):
    return {"call_band": call_band, "call": call, "dxcc": dxcc, "dxcc_band": dxcc_band}


def test_hunt_picks_new_plain_cq_and_skips_blocked_directed_worked():
    s = Sequencer(_Digi(), _Tx(), _Log(), _Mgr(), _Set())
    decs = [_d("CQ DL1AAA JO40", snr=5, worked=_w(call_band=True)),        # worked on this band
            _d("CQ DX ON4BBB JO20", snr=4, worked=_w()),                   # directed CQ
            _d("CQ OK1CCC JO70", snr=-5, worked=_w()),
            _d("CQ SP2DDD JO93", snr=0, worked=_w()),                      # strongest new one
            _d("CQ G4EEE IO91", snr=9, worked=None),                       # no worked-before info
            _d("CQ K1FFF FN42", snr=-28, worked=_w(dxcc=False))]           # new DXCC, but too weak
    assert s.hunt_pick(decs)["from"] == "SP2DDD"
    s.hunt_min_snr = -30
    assert s.hunt_pick(decs)["from"] == "K1FFF"                            # a new DXCC goes first
    s.hunt_blocked["K1FFF"] = {"t": 1.0}
    assert s.hunt_pick(decs)["from"] == "SP2DDD"
    s.hunt_target = "dxcc"
    assert s.hunt_pick(decs) is None                                       # only new DXCC wanted


def test_hunt_calls_when_idle_and_blocks_a_station_that_never_answers():
    s = Sequencer(_Digi(), _Tx(), _Log(), _Mgr(), _Set())
    blocked = []
    s.on_hunt_block = lambda b: blocked.append(dict(b))
    started = []
    async def fake_answer(d, hz):
        started.append(d["from"])
        s.session = s._new_session(d)
    s.answer = fake_answer
    decs = [_d("CQ SP2DDD JO93", snr=0, worked=_w())]
    asyncio.run(s.on_decodes(decs, 15.0, "FT8", False))
    assert started == []                                                   # Hunt off
    asyncio.run(s.set_hunt(True, 1600))
    assert s.auto_tx_hz == 1600.0 and s.status()["hunt"]
    asyncio.run(s.on_decodes(decs, 15.0, "FT8", True))                     # early pass: no
    assert started == []
    asyncio.run(s.on_decodes(decs, 15.0, "FT8", False))
    assert started == ["SP2DDD"] and s.session["hunt"]
    # the QSO ran out of tries without one answer: blocked, never called again
    s.session["state"] = "no reply"
    s._hunt_block(s.session["call"])
    assert "SP2DDD" in s.hunt_blocked and blocked and "SP2DDD" in s.status()["hunt_blocked"]
    s.session, s.kind = None, None
    assert s.hunt_pick(decs) is None
    asyncio.run(s.clear_hunt_blocked())
    assert s.hunt_blocked == {} and blocked[-1] == {}


def test_hunt_switches_itself_off():
    s = Sequencer(_Digi(), _Tx(), _Log(), _Mgr(), _Set())
    asyncio.run(s.set_hunt(True))
    s.hunt_until = 0.0                                                     # time is up
    asyncio.run(s.on_decodes([_d("CQ SP2DDD JO93", worked=_w())], 15.0, "FT8", False))
    assert s.hunt is False and s.session is None


def test_hunt_without_switch_off():
    s = Sequencer(_Digi(), _Tx(), _Log(), _Mgr(), _Set())
    assert s.hunt_minutes == 180
    s.hunt_minutes = 0
    asyncio.run(s.set_hunt(True))
    assert s.status()["hunt_left_s"] is None
    asyncio.run(s.on_decodes([], 15.0, "FT8", False))
    assert s.hunt is True


def test_hunt_list_edited_by_hand():
    s = Sequencer(_Digi(), _Tx(), _Log(), _Mgr(), _Set())
    saved = []
    s.on_hunt_block = lambda b: saved.append(dict(b))
    asyncio.run(s.set_hunt_blocked(" sp2ddd ", True))
    assert "SP2DDD" in s.hunt_blocked and "SP2DDD" in saved[-1]
    assert s.hunt_blocked["SP2DDD"]["manual"] and s.hunt_blocked["SP2DDD"]["t"] > 0
    assert s.hunt_pick([_d("CQ SP2DDD JO93", worked=_w())]) is None
    asyncio.run(s.set_hunt_blocked("SP2DDD", False))
    assert s.hunt_blocked == {} and saved[-1] == {}


def test_answer_marks_the_partners_period_as_evaluated():
    s = Sequencer(_Digi(), _Tx(), _Log(), _Mgr(), _Set())
    s._run = lambda: None                                   # no loop in the test
    asyncio.run(s.answer(_d("CQ SP2DDD JO93", t0=30.0), 1500))
    assert s._seen.get(30.0) is True                        # TX in the next period starts at once
