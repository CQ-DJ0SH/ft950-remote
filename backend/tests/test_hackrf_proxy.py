import asyncio
from pathlib import Path

from app.hackrf_proxy import OFFSET_HZ, HackRFProxy


def _p(freq=14_074_000, url="https://127.0.0.1:1"):
    return HackRFProxy(lambda: url, Path("/nonexistent"), lambda: freq)


def test_follow_is_done_here_never_upstream():
    p = _p()
    cfg = p._prepare({"mode": "pan", "follow": True, "lna": 16})
    assert cfg["follow"] is False                      # the TM-V71 must not follow its own radio
    assert cfg["center"] == 14_350_000 + 30_000         # whole 20 m band in view, DC notch just above it
    assert p.follow is True


def test_no_follow_keeps_manual_center_and_sweep_untouched():
    p = _p()
    assert p._prepare({"follow": False, "center": 7_100_000})["center"] == 7_100_000
    p.follow = True
    assert "center" not in p._prepare({"mode": "sweep", "sweep_start": 1_800_000})


def test_unreachable_source_reports_instead_of_raising():
    st = asyncio.run(_p().status())
    assert st["reachable"] is False and st["running"] is False and "unreachable" in st["error"]


def test_follow_shows_the_whole_band_with_the_dc_notch_outside():
    from app.hackrf_proxy import EDGE_HZ, SPAN, band_center
    p = _p(freq=14_074_000)
    c = p.target()
    assert c == 14_350_000 + EDGE_HZ                          # just above 20 m
    assert c - SPAN // 2 <= 14_000_000                        # whole band inside the span
    assert band_center(28_000_000, 29_700_000) == 28_850_000  # 10 m is too wide: middle
    assert _p(freq=13_000_000).target() == 13_000_000 + 250_000   # outside a band: VFO + offset


def test_band_view_asks_for_a_fine_zoom_row():
    p = _p(7_074_000)
    assert p._prepare({"mode": "pan"})["zoom"] == [6_998_000, 7_202_000]     # 40 m ± 2 kHz
    assert p._prepare({"follow": False})["zoom"] == [0, 0]                   # no band view → off
    assert _p(8_000_000)._prepare({"mode": "pan"})["zoom"] == [0, 0]         # outside a band


def test_zoom_support_is_learnt_from_the_status():
    p = _p(7_074_000)
    assert p.zoom_supported is None
    p._note({"running": True, "center": 7_230_000})                    # older TM-V71 Remote
    assert p.zoom_supported is False
    p._note({"running": True, "center": 7_230_000, "zoom": [6_998_000, 7_202_000]})
    assert p.zoom_supported is True and p.upstream_zoom == p.wanted_zoom()
