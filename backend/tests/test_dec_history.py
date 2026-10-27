from app import dec_history as dh


def test_parallel_runs_form_one_pass():
    runs = [(100.0, 101.2, 0.9, 1.2, 30000, "2.7 fast"),     # three parts, started together
            (100.1, 101.5, 1.1, 1.4, 32000, "2.7 fast"),
            (100.2, 101.0, 0.7, 0.8, 28000, "2.7 fast"),
            (107.0, 107.3, 0.2, 0.3, 20000, "2.7 fast")]     # an early pass later on
    ps = dh._passes(runs)
    assert len(ps) == 2
    p = ps[0]
    assert p["parts"] == 3
    assert round(p["cpu_s"], 2) == 2.7                       # CPU adds up
    assert p["rss_kb"] == 90000                               # all resident at once
    assert p["part_rss_kb"] == 32000
    assert round(p["wall_s"], 2) == 1.5                       # from the first start to the last end
