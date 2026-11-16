"""Offline tests for brief/stats.py: made-up prices with hand-checkable answers."""

import math

import numpy as np
import pandas as pd
import pytest

from brief import stats


def series(values, start="2026-01-01"):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)), dtype=float)


def test_one_and_five_day_changes():
    s = series([100, 101, 102, 103, 104, 105, 110])
    out = stats.summarize(s)
    assert out["last"] == 110
    assert out["chg_1d_pct"] == pytest.approx((110 / 105 - 1) * 100)
    assert out["chg_5d_pct"] == pytest.approx((110 / 101 - 1) * 100)   # 5 trading days back
    assert out["asof"] == s.index[-1].strftime("%Y-%m-%d")


def test_unusual_flag_fires_only_on_a_big_move():
    calm = [100 * (1.001 if i % 2 else 0.999) ** 1 for i in range(30)]   # tiny wiggles
    s = series(calm + [110])                                              # then +10%
    assert stats.summarize(s)["unusual"] == "UNUSUAL"
    assert stats.summarize(series(calm))["unusual"] == ""


def test_short_history_returns_nones_not_errors():
    out = stats.summarize(series([100]))
    assert out["last"] == 100 and out["chg_1d_pct"] is None and out["vol20_ann_pct"] is None
    assert stats.summarize(pd.Series(dtype=float))["last"] is None


def test_pair_z_score_matches_hand_calculation():
    rng = np.random.default_rng(0)
    b = series(100 + rng.normal(0, 1, 40).cumsum())
    a = b * (1.5 + series(rng.normal(0, 0.01, 40)))
    out = stats.pair_stats(a, b, window=20)
    ratio = (a / b).iloc[-20:]
    expected = (ratio.iloc[-1] - ratio.mean()) / ratio.std()
    assert out["z"] == pytest.approx(expected)
    assert out["direction"] in ("converging", "diverging")


def test_pair_ignores_days_only_one_leg_traded():
    a = series([10.0] * 25)
    b = series([5.0] * 25)
    b.iloc[3] = math.nan                          # holiday in b's market
    assert stats.pair_stats(a, b, window=20)["ratio"] == 2.0


def test_crack_spread():
    # CL $80, RB $2.50/gal, HO $2.80/gal -> (2*105 + 117.6 - 240)/3 = 29.2
    assert stats.crack_321(80, 2.5, 2.8) == pytest.approx(29.2)
    assert stats.crack_321(None, 2.5, 2.8) is None


def test_watchlist_parsing():
    rows = ["xom", "  PSX / vlo ", "", "# comment", "XOM", None, "nan", "A/"]
    assert stats.parse_watchlist(rows) == [
        {"kind": "stock", "a": "XOM"},
        {"kind": "pair", "a": "PSX", "b": "VLO"},
    ]
