from app.digi import PART_OVERLAP_HZ, dedupe, split_band


def test_equal_split_overlaps():
    r = split_band(200, 3000, 3)
    assert r[0][0] == 200 and r[-1][1] == 3000 and len(r) == 3
    assert all(a[1] - b[0] == PART_OVERLAP_HZ for a, b in zip(r, r[1:]))


def test_split_follows_last_decodes():
    busy = [1000 + 10 * i for i in range(30)]          # all stations between 1000 and 1300 Hz
    r = split_band(200, 3000, 3, busy)
    assert 1000 < r[0][1] < 1200 and 1100 < r[1][1] < 1350   # seams inside the busy stretch


def test_dedupe_keeps_best_snr_once():
    d = [{"msg": "CQ DL1ABC JO62", "df": 1180, "snr": -12},
         {"msg": "CQ DL1ABC JO62", "df": 1183, "snr": -9},
         {"msg": "CQ DL1ABC JO62", "df": 2400, "snr": -15}]    # same text, other frequency: kept
    out = dedupe(d)
    assert [(x["df"], x["snr"]) for x in out] == [(1183, -9), (2400, -15)]
