"""VOACAP band conditions: geometry, deck/parse round trip, and - where
voacapl is installed - a real run on a throw-away tree."""
import datetime as dt
import shutil

import pytest

from app import propagation as p


def test_grid_latlon():
    assert p.grid_latlon("JO62qk") == pytest.approx((52.4375, 13.375))
    assert p.grid_latlon("JO62") == pytest.approx((52.5, 13.0))
    with pytest.raises(ValueError):
        p.grid_latlon("")
    with pytest.raises(ValueError):
        p.grid_latlon("ZZ99")


def test_destination():
    lat, lon = 52.0, 13.0
    assert p.destination(lat, lon, 90, 0) == pytest.approx((lat, lon))
    n = p.destination(lat, lon, 0, 1111.95)          # 10 degrees of arc due north
    assert n == pytest.approx((62.0, 13.0), abs=0.01)
    w = p.destination(10.0, -179.0, 270, 500)        # across the date line
    assert -180 <= w[1] <= 180 and w[1] > 170


def test_level_thresholds():
    assert p.level(0.60) == "good" and p.level(0.59) == "fair"
    assert p.level(0.30) == "fair" and p.level(0.29) == "poor"


def test_deck_columns():
    deck = p.build_deck((52.44, 13.38), (-33.9, -70.6), 60.3,
                        dt.datetime(2026, 10, 25, tzinfo=dt.timezone.utc)).splitlines()
    circuit = next(l for l in deck if l.startswith("CIRCUIT"))
    # 10X, F5.2,A1, 3(F9.2,A1): fixed columns, hemisphere letters in place
    assert circuit[10:16] == "52.44N" and circuit[16:26] == "    13.38E"
    assert circuit[26:36] == "    33.90S" and circuit[36:46] == "    70.60W"
    freq = next(l for l in deck if l.startswith("FREQUENCY"))
    assert len(freq) == 10 + 11 * 5                   # 11 slots, ours first
    assert freq[10:15] == " 3.65" and freq[45:50] == "28.50" and freq[50:55] == "50.30"
    assert "SUNSPOT    60.3" in deck
    month = next(l for l in deck if l.startswith("MONTH"))
    assert month[11:15] == "2026" and month[15:20] == "10.00"   # I4 year, F5.2 month: "202610.00"


def _fake_output(hours=24):
    lines = []
    for h in range(1, hours + 1):
        lines.append(f"   {h}.0  9.4  3.6  7.1 10.1 14.1 18.1 21.2 24.9 28.5 50.3  0.0  0.0 FREQ")
        lines.append("        0.10 0.90 0.80 0.70 0.60 0.50 0.40 0.30   -  0.05     REL")
    return "\n".join(lines)


def test_parse_rel():
    rel = p.parse_rel(_fake_output())
    assert sorted(rel) == list(range(24))            # hour 24 folds to 0
    assert rel[5] == [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.0, 0.05]   # "-" is 0
    with pytest.raises(RuntimeError):
        p.parse_rel(_fake_output(23))


def test_deck_name_limit(tmp_path):
    with pytest.raises(ValueError):
        p._circuit("/bin/true", tmp_path, "", stem="x" * 17)   # 21 chars with .dat


def test_status_without_locator():
    assert p.Propagation().status("")["available"] is False


@pytest.mark.skipif(not shutil.which("voacapl"), reason="voacapl not installed")
def test_real_run_is_isotropic_and_repeatable(tmp_path, monkeypatch):
    # a throw-away tree: never the service's own backend/itshfbc
    monkeypatch.setattr(p, "BACKEND", tmp_path)
    monkeypatch.setattr(p, "ITSHFBC", tmp_path / "itshfbc")
    when = dt.datetime(2026, 10, 25, 16, tzinfo=dt.timezone.utc)
    a = p.compute_day("JO62qk", 60.3, when)
    b = p.compute_day("JO62qk", 60.3, when)
    assert a == b
    assert all(0.0 <= r <= 1.0 for h in a.values() for r in h)
    head = (tmp_path / "itshfbc" / "run" / "gain01.dat").read_text().splitlines()[0]
    assert "ISOTROPE" in head                         # not the shipped Const 17 dB table
    # October, SSN 60, 16 UTC in Berlin: 20 m open, 10 m mostly closed
    i20, i10 = [b for b, _ in p.BANDS].index(20), [b for b, _ in p.BANDS].index(10)
    assert a[16][i20] > 0.6 and a[16][i10] < 0.4


def test_halves_and_six_metres(monkeypatch):
    # a made-up day: everything good by day, 12-10 dead at night; 6 m on its own
    day = {h: [0.7] * 9 if 7 <= h <= 15 else [0.8, 0.8, 0.8, 0.7, 0.2, 0.1, 0.0, 0.0, 0.0]
           for h in range(24)}
    pr = p.Propagation()
    monkeypatch.setattr(p, "compute_day", lambda grid, ssn, when: day)
    monkeypatch.setattr(pr, "_ssn", lambda: (60.0, "2026-09"))
    s = pr.status("JO62qk")
    names = [g["name"] for g in s["groups"]]
    assert names == ["80-40", "30-20", "17-15", "12-10", "6"]
    assert s["groups"][4]["bands"] == {"6": s["groups"][4]["rel"]}
    d, n = s["halves"]["day"], s["halves"]["night"]
    assert set(d["hours"]) | set(n["hours"]) == set(range(24))
    assert not set(d["hours"]) & set(n["hours"])
    # Berlin in late October: the night wraps midnight, the day does not
    assert d["from"] <= d["to"] and n["from"] > n["to"]
    assert [g["level"] for g in n["groups"]][3] == "poor"
