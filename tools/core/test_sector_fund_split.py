"""กองทุน / REIT ไม่นับในแถวระดับ sector ของ sector_flow แต่ยังมีแถว subsector ของตัวเอง

    python -m pytest tools/core/test_sector_fund_split.py
"""
import importlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.scanner.utils import FUND_SUBSECTOR, is_fund_ticker  # noqa: E402

flow = importlib.import_module("tools.core.calculate_sector_flow")

T2S = {
    "AAA": {"sector": "Property", "subsector": "Property Development", "market": "SET"},
    "FFF": {"sector": "Property", "subsector": FUND_SUBSECTOR, "market": "SET"},
}


def test_is_fund_ticker():
    assert is_fund_ticker("FFF", T2S)
    assert is_fund_ticker(" fff ", T2S)
    assert not is_fund_ticker("AAA", T2S)
    assert not is_fund_ticker("ZZZ", T2S)
    assert not is_fund_ticker("FFF", {"FFF": "not a dict"})


def _write_universe(tmp: Path, n_days: int = 70):
    """110 Property stocks (2 subsectors) + 5 funds + 100 Banking stocks, random walk prices."""
    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2026-01-05", periods=n_days)
    hist = tmp / "history"
    hist.mkdir()
    t2s, caps = {}, []
    groups = ([("Property", "Property Development")] * 60 + [("Property", "Construction Services")] * 50
              + [("Property", FUND_SUBSECTOR)] * 5 + [("Financials", "Banking")] * 100)
    for i, (sec, sub) in enumerate(groups):
        t = f"T{i:03d}"
        t2s[t] = {"sector": sec, "subsector": sub, "market": "SET"}
        close = 10 * np.exp(np.cumsum(rng.normal(0, 0.02, n_days)))
        vol = rng.integers(1_000, 100_000, n_days)
        pd.DataFrame({"Date": dates.strftime("%Y-%m-%d"), "Close": close, "Volume": vol}).to_csv(hist / f"{t}.csv", index=False)
        caps.append({"Ticker": t, "Market_Cap": float(rng.integers(1, 100)) * 1e9})
    daily = tmp / "daily_prices.csv"
    pd.DataFrame(caps).to_csv(daily, index=False)
    sm = tmp / "sector_map.json"
    sm.write_text(json.dumps({"ticker_to_sector": t2s}), encoding="utf-8")
    return hist, daily, sm, t2s


def test_sector_rows_exclude_funds(tmp_path, monkeypatch):
    monkeypatch.setattr(flow, "iter_stock_files", lambda d: sorted(Path(d).glob("*.csv")))
    hist, daily, sm, t2s = _write_universe(tmp_path)
    out = flow.build(hist, daily, sm, force_universe_benchmark=True)

    prop = next(r for r in out["sectors"] if r["sector"] == "Property")
    fund = next(r for r in out["subsectors"] if r["subsector"] == FUND_SUBSECTOR)
    stock_subs = [r for r in out["subsectors"] if r["sector"] == "Property" and r is not fund]
    assert fund["n"] == 5 and fund["sector"] == "Property"   # fund subsector row stays
    assert prop["n"] == 110                                   # sector row = stocks only
    assert prop["n"] == sum(r["n"] for r in stock_subs)
    for k in ("value_1d", "base_value_1d", "value_5d", "base_value_5d", "avg_value_20d"):
        assert abs(prop[k] - sum(r[k] for r in stock_subs)) < 0.05, k   # additive: funds' value not in it
    assert len(out["subsectors"]) == 4                        # Drop Guard count unchanged
    assert next(r for r in out["sectors"] if r["sector"] == "Financials")["n"] == 100
