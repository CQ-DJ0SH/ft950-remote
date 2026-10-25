import pytest

from app.menus import BY_NO, fmt, parse, validate


@pytest.mark.parametrize("raw,val", [("030", 30), ("+1500", 1500), ("-0200", -200), ("+00", 0), ("--05", -5), ("1000", 1000)])
def test_parse(raw, val):
    assert parse(raw) == val


def test_fmt_follows_the_radios_answer():
    assert fmt(45, "030") == "045"
    assert fmt(-50, "+1500") == "-0050"
    assert fmt(0, "-05") == "+00"
    with pytest.raises(ValueError):
        fmt(-1, "030")


def test_validate():
    assert validate(BY_NO[52], 300) == 300
    with pytest.raises(ValueError):
        validate(BY_NO[52], 305)          # 10 ms steps
    with pytest.raises(ValueError):
        validate(BY_NO[64], 7)            # not an option
    assert validate(BY_NO[56], 1000) == 1000 and validate(BY_NO[56], 40) == 40


def test_eq_tables():
    assert BY_NO[97].options[4] == "1800 Hz"   # the manual's "18000 Hz" is a typo
    assert BY_NO[94].options[9] == "1500 Hz" and BY_NO[91].options[7] == "700 Hz"
