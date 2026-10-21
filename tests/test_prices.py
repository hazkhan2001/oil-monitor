import pandas as pd
from monitor import prices


def test_combine_computes_spread_and_keeps_all_dates():
    b = pd.DataFrame({"date": pd.to_datetime(["2026-10-19", "2026-10-20"]), "brent": [105.0, 108.0]})
    w = pd.DataFrame({"date": pd.to_datetime(["2026-10-20"]), "wti": [101.5]})
    out = prices.combine(b, w)
    assert list(out["date"]) == ["2026-10-19", "2026-10-20"]       # outer join keeps the day WTI is missing
    assert out["brent_wti_spread"].iloc[1] == 6.5
    assert pd.isna(out["brent_wti_spread"].iloc[0])
