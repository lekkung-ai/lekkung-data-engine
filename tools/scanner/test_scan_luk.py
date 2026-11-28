"""Unit tests สำหรับ scan_luk.py — series สังเคราะห์ (pytest หรือ `python test_scan_luk.py`)"""
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE.parents[1]), str(_HERE)]
import scan_luk as L  # noqa: E402
from luk_config import LUK_CONFIG as CFG  # noqa: E402
from vcp import thai_tick  # noqa: E402

N = 160


def path(points, n=N):
    xs, ys = zip(*points)
    return np.interp(np.arange(n), xs, ys)


def frame(close, vol=1_000_000.0, spread=0.01, opens=None, start="2025-01-01"):
    close = np.asarray(close, float)
    v = np.full(len(close), vol) if np.isscalar(vol) else np.asarray(vol, float)
    o = close.copy() if opens is None else np.asarray(opens, float)
    return pd.DataFrame({"Open": o, "High": np.maximum(close, o) * (1 + spread), "Low": np.minimum(close, o) * (1 - spread),
                         "Close": close, "Volume": v}, index=pd.bdate_range(start, periods=len(close)))


def arr(df):
    return L.stock_arrays(df, CFG), len(df) - 1


# ───────────────────────────────────────────── tick / stop
def test_long_stop_at_least_three_ticks_for_5_to_25_baht():
    for price in np.arange(5.0, 25.0, 0.35):
        r = L.long_risk(close=price, high=price * 1.005, low=price * 0.995, risk_pct=0.3)
        t = thai_tick(r["entry_trigger"])
        assert r["stop_ticks"] >= CFG["min_stop_ticks"], (price, r)
        assert r["entry_trigger"] - r["stop_price"] >= CFG["min_stop_ticks"] * t - 1e-9
        assert r["stop_pct"] >= CFG["intraday_stop_pct"]


def test_entry_trigger_is_high_plus_one_tick_on_tick_grid():
    r = L.long_risk(close=10.0, high=10.1, low=9.9, risk_pct=0.3)
    assert r["entry_trigger"] == 10.2                    # tick 0.10
    r2 = L.long_risk(close=108.0, high=108.255, low=107.0, risk_pct=0.3)
    assert r2["entry_trigger"] == 109.0                  # ราคาปรับปันผล 108.255 → ปัดขึ้น 108.5 + tick 0.5
    assert r["size_pct_intraday_stop"] <= CFG["max_position_pct"]


# ───────────────────────────────────────────── setups
def test_long_pullback_into_ema():
    c = path([(0, 40), (N - 6, 100), (N - 1, 97.5)])
    df = frame(c)
    x, i = arr(df)
    r = L.long_pullback(x, i, CFG)
    assert r is not None and r["setup"] == "LONG_PULLBACK" and r["side"] == "long"
    assert r["ref_name"] in ("EMA9", "EMA21", "AVWAP")
    # ไม่ได้ย่อ (ทำ high วันนี้) → ไม่ใช่
    x2, i2 = arr(frame(path([(0, 40), (N - 1, 100)])))
    assert L.long_pullback(x2, i2, CFG) is None


def test_episodic_pivot_gap_and_volume():
    c = np.full(N, 10.0)
    c[-1] = 11.2
    o = c.copy()
    o[-1] = 10.9                                         # gap +9%
    v = np.full(N, 1_000_000.0)
    v[-1] = 5_000_000
    x, i = arr(frame(c, v, opens=o))
    r = L.episodic_pivot(x, i, CFG)
    assert r and r["setup"] == "EPISODIC_PIVOT" and r["ref_name"] == "OPEN"
    v[-1] = 2_000_000                                    # วอลุ่ม 2 เท่า < 3
    x, i = arr(frame(c, v, opens=o))
    assert L.episodic_pivot(x, i, CFG) is None


def test_short_weak_bounce():
    # ผู้นำ (ขึ้นแรง 6 เดือน) → หลุด EMA21 พร้อมวอลุ่มเมื่อ 8 แท่งก่อน → ลงต่อถึง low 3 แท่งก่อน → เด้งเบาๆ
    c = path([(0, 40), (N - 9, 100)])
    c[N - 8:] = [93, 88, 85, 83, 81, 82.5, 83.5, 84.5]
    v = np.full(N, 1_000_000.0)
    v[N - 8] = 2_500_000
    v[N - 3:] = 600_000
    x, i = arr(frame(c, v, spread=0.02))
    r = L.short_weak_bounce(x, i, CFG)
    assert r and r["side"] == "weak" and r["setup"] == "SHORT_WEAK_BOUNCE"


def test_parabolic_short_uses_pre_run_adr_for_distance():
    c = np.full(N, 10.0)
    c[-7:] = [10.4, 10.9, 11.6, 12.5, 13.4, 14.4, 15.5]   # 7/7 แท่งขึ้น · +43% ใน 5 วัน
    x, i = arr(frame(c, spread=0.005))
    r = L.parabolic_short(x, i, CFG)
    assert r and r["side"] == "weak"
    pre = 0.005 * 2 / 0.995                              # base: High/Low − 1 = (1.005/0.995 − 1)
    expect = (15.5 - x["e9"][i]) / (pre * 15.5)
    assert abs(r["dist_ref_adr"] - round(expect, 2)) < 0.02
    assert f"{expect:.1f}" in r["note"]                  # note กับ dist_ref_adr ใช้ ADR ก่อนวิ่งตัวเดียวกัน


# ───────────────────────────────────────────── filters / regime / themes
def test_filters_without_market_cap():
    x, i = arr(frame(np.full(N, 10.0), vol=10_000_000, spread=0.02))   # มูลค่า 100 ลบ. ADR ~4%
    assert L.passes_filters(x, i, CFG) == {"price": True, "value20": True, "adr": True}
    x, i = arr(frame(np.full(N, 10.0), vol=10_000_000, spread=0.005))  # ADR ~1%
    assert L.passes_filters(x, i, CFG)["adr"] is False
    x, i = arr(frame(np.full(N, 1.5), vol=100_000_000, spread=0.02))   # ราคา < 2
    assert L.passes_filters(x, i, CFG) == {"price": False, "value20": False, "adr": False}
    assert not any("mcap" in k or "market_cap" in k for k in CFG)


def test_regime_long_and_short():
    up = [arr(frame(path([(0, 50), (N - 1, 100)]))) for _ in range(5)]
    set_up = pd.Series(path([(0, 1300), (N - 1, 1600)]), index=pd.bdate_range("2025-01-01", periods=N))
    r = L.regime(up, set_up, CFG)
    assert r["mode"] == "LONG" and r["suggested_risk_pct"] == CFG["risk_pct"]
    dn = [arr(frame(path([(0, 100), (N - 1, 50)]))) for _ in range(5)]
    set_dn = pd.Series(path([(0, 1600), (N - 1, 1300)]), index=set_up.index)
    r = L.regime(dn, set_dn, CFG)
    assert r["mode"] == "SHORT" and r["set"]["above_ema21"] is False
    mixed = L.regime(up, set_dn, CFG)
    assert mixed["mode"] in ("MIXED", "DEFENSIVE") and mixed["suggested_risk_pct"] == round(CFG["risk_pct"] * CFG["reg_half_risk_mult"], 3)


def test_theme_alerts_subsector_threshold():
    rows = [{"symbol": s, "subsector": "Electronic Components", "sector": "Technology"} for s in ("A", "B", "C")]
    rows += [{"symbol": "D", "subsector": None, "sector": "Industrials"}, {"symbol": "E", "subsector": "Banking", "sector": "Financials"}]
    a = L.theme_alerts(rows, CFG)
    assert a == [{"subsector": "Electronic Components", "count": 3, "symbols": ["A", "B", "C"]}]
    assert L.theme_alerts(rows[:2], CFG) == []


# ───────────────────────────────────────────── build end-to-end (ไฟล์สังเคราะห์)
def _write_history(d: Path):
    idx = pd.bdate_range("2025-01-01", periods=N)
    pd.DataFrame({"Open": path([(0, 1300), (N - 1, 1600)]), "High": path([(0, 1310), (N - 1, 1610)]),
                  "Low": path([(0, 1290), (N - 1, 1590)]), "Close": path([(0, 1300), (N - 1, 1600)]),
                  "Volume": 1e10}, index=idx).to_csv(d / "SET_INDEX.csv")
    # LEAD: leader pulling back (long) · WEAKP: parabolic (weak) · THIN: illiquid
    lead = frame(path([(0, 40), (N - 6, 100), (N - 1, 97.5)]), vol=2_000_000, spread=0.02)
    cp = np.full(N, 10.0); cp[-7:] = [10.4, 10.9, 11.6, 12.5, 13.4, 14.4, 15.5]
    para = frame(cp, vol=10_000_000, spread=0.02)
    thin = frame(path([(0, 40), (N - 1, 100)]), vol=1_000, spread=0.02)
    for name, df in (("LEAD", lead), ("WEAKP", para), ("THIN", thin)):
        df.to_csv(d / f"{name}.csv")
    pd.DataFrame({"Ticker": ["LEAD", "WEAKP", "THIN"], "RS_Rating": [99, 50, 98]}).to_csv(d / "rs.csv", index=False)


def test_build_end_to_end(tmp_path=None):
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write_history(d)
        L.load_fund_tickers = lambda: {"FUNDX"}
        L.load_sector_map = lambda: {"LEAD": {"sector": "Technology", "subsector": "Electronic Components"}}
        out = L.build(d, d / "SET_INDEX.csv", d / "rs.csv", CFG, log=lambda *_: None)
        assert out["funnel"] == {"universe": 3, "price": 3, "value20": 2, "adr": 2}
        assert [l["symbol"] for l in out["leaders"]] == ["LEAD", "WEAKP"]
        assert [c["symbol"] for c in out["candidates"]] == ["LEAD"]
        assert [w["symbol"] for w in out["weak"]] == ["WEAKP"]
        assert not any(k.startswith("size") or k.startswith("stop") for k in out["weak"][0])     # ฝั่งอ่อนแรงไม่มีขนาด/stop
        c = out["candidates"][0]
        assert {"entry_trigger", "stop_price", "stop_ticks", "size_pct_daily_stop", "size_pct_intraday_stop"} <= set(c)
        assert out["regime"]["mode"] in ("LONG", "MIXED", "DEFENSIVE", "SHORT") and out["generated_at"]
        h = L.hits_from(out)
        assert h["generated_at"] and h["fields"][0] == "symbol" and len(h["rows"]) == 2
        L.write_json(out, d / "luk.json")
        assert json.load(open(d / "luk.json", encoding="utf-8"))["config"]["leader_top_n"] == CFG["leader_top_n"]


def test_build_never_crashes_without_set_index():
    with tempfile.TemporaryDirectory() as td:
        out = L.build(Path(td), Path(td) / "missing.csv", Path(td) / "rs.csv", CFG, log=lambda *_: None)
        assert out["candidates"] == [] and out["warnings"] and out["generated_at"]


if __name__ == "__main__":
    fns = [v for k, v in dict(globals()).items() if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"OK {len(fns)} tests")
