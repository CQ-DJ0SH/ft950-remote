import pytest

from app.digi import analyse
from app.dxcc import Dxcc

DX = Dxcc()


@pytest.mark.parametrize("call,entity", [
    ("DJ0SH", "Fed. Rep. of Germany"), ("OH6IH", "Finland"),
    ("RA3FD", "European Russia"), ("PA/K1ABC", "Netherlands"),
    ("K1ABC/P", "United States"), ("CT7AUT", "Portugal"),
    ("9A2VX", "Croatia"), ("K1ABC/4", "United States"),
])
def test_dxcc(call, entity):
    assert DX.lookup(call) == entity


def test_dxcc_loaded():
    assert len(DX) > 300


@pytest.mark.parametrize("msg,exp", [
    ("CQ CT7AUT IM67", {"kind": "CQ", "from": "CT7AUT", "grid": "IM67"}),
    ("CQ DX RA3FD KO96", {"kind": "CQ", "from": "RA3FD", "grid": "KO96"}),
    ("CQ POTA K1ABC FN42", {"kind": "CQ", "from": "K1ABC"}),
    ("9A2VX EW2MS KO34", {"kind": "grid", "to": "9A2VX", "from": "EW2MS"}),
    ("IZ2QPP RA3FD +15", {"kind": "report", "from": "RA3FD"}),
    ("PD3RB RA3P R+04", {"kind": "rreport"}),
    ("DL3ND OH3OJ RR73", {"kind": "RR73", "from": "OH3OJ"}),
    ("GI4OGS SV7BAY 73", {"kind": "73"}),
    ("<DJ0SH> W1AW RRR", {"kind": "RRR", "to": "DJ0SH"}),
    ("TNX FER QSO 73", {"kind": "text", "from": None}),
])
def test_analyse(msg, exp):
    got = analyse(msg)
    assert {k: got.get(k) for k in exp} == exp


def test_dxcc_position_longitude_is_east_positive():
    d = Dxcc()
    if not len(d):
        pytest.skip("cty.dat not installed")
    name, lat, lon = d.position("GM3RTG")          # cty.dat counts west positive
    assert name == "Scotland" and 55 < lat < 59 and -6 < lon < -2
    assert d.position("JA1ABC")[2] > 120 and d.position("K1ABC")[2] < -60
