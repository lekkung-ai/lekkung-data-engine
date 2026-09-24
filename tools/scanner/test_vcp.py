"""Unit tests สำหรับ vcp.py (series สังเคราะห์) — รันด้วย `python test_vcp.py` หรือ pytest."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vcp import MIN_LEG_BARS, adaptive_swings, swing_threshold_pct, thai_tick, vcp_metrics  # noqa: E402


def make_df(waypoints, pre_bars=60, pre_price=None, vol=None, spread=0.002):
    """waypoints = [(bar_index_from_base_start, price), ...] เชื่อมเส้นตรง; ก่อนฐานมี pre_bars แท่งขาขึ้น."""
    idx = [b for b, _ in waypoints]
    px = [p for _, p in waypoints]
    n = idx[-1] + 1
    base = np.interp(np.arange(n), idx, px)
    p0 = pre_price or px[0] * 0.6
    pre = np.linspace(p0, px[0] * 0.995, pre_bars)
    close = np.concatenate([pre, base])
    high = close * (1 + spread)
    low = close * (1 - spread)
    high[pre_bars] = px[0]  # ยอดฐานคือ High สูงสุดของช่วง
    volume = np.full(len(close), 1000.0) if vol is None else vol(len(close), pre_bars)
    return pd.DataFrame({"High": high, "Low": low, "Close": close, "Volume": volume})


def test_thai_tick_table():
    assert thai_tick(1.5) == 0.01
    assert thai_tick(4.99) == 0.02
    assert thai_tick(9.9) == 0.05
    assert thai_tick(24.9) == 0.10
    assert thai_tick(99.75) == 0.25
    assert thai_tick(150) == 0.50
    assert thai_tick(399) == 1
    assert thai_tick(400) == 2


def test_classic_vcp_25_15_8():
    # input เดิมของรอบแรก (right-side high ไต่ 95→97) — ห้ามแก้ input
    wp = [(0, 100), (20, 75), (35, 95), (50, 80.75), (60, 97), (70, 89.2), (80, 96)]

    def vol(n, pre):
        v = np.full(n, 1000.0)
        v[-25:] = 400.0  # วอลุ่มหดช่วง leg สุดท้าย
        return v

    r = vcp_metrics(make_df(wp, vol=vol))
    assert r["VCP_T"] == 3, r
    assert r["VCP_Contracting"] is True, r
    assert r["VCP_VolRatio"] < 1, r
    assert r["VCP_Footprint"].endswith("3T"), r
    assert 24 <= r["VCP_MaxDepth"] <= 27, r
    assert 7 <= r["VCP_FinalDepth"] <= 10, r
    assert 97.0 <= r["VCP_Pivot"] <= 97.5, r
    assert r["VCP_ToPivot"] >= 0, r


def test_25_20_18_not_contracting():
    # หดไม่ถึงเกณฑ์ (20/25=0.8, 18/20=0.9 > 0.75) → Contracting=False
    wp = [(0, 100), (15, 75), (30, 95), (45, 76), (58, 92), (70, 75.5), (80, 85)]
    r = vcp_metrics(make_df(wp))
    assert r["VCP_T"] == 3, r
    assert r["VCP_Contracting"] is False, r


def test_new_high_today_is_no_base():
    n = 120
    close = np.linspace(50, 100, n)
    df = pd.DataFrame({"High": close * 1.002, "Low": close * 0.998, "Close": close, "Volume": np.full(n, 1000.0)})
    r = vcp_metrics(df)
    assert r["VCP_T"] == 0 and r["VCP_Footprint"] == "no base", r
    assert r["VCP_Contracting"] is None and r["VCP_Pivot"] is None, r


def test_flat_10pct_base_not_contracting():
    wp = [(0, 100), (10, 90), (20, 99), (30, 89.5), (40, 99), (50, 90), (60, 98)]
    r = vcp_metrics(make_df(wp))
    assert r["VCP_T"] <= 1 or r["VCP_Contracting"] is False, r
    assert r["VCP_Contracting"] is False, r


def test_low_price_tick_guard():
    # 1.50 บาท แกว่ง 0.02 (2 ช่อง tick) ~1.3% ต้องไม่ถูกนับเป็น swing
    wp = [(0, 1.50)] + [(k, 1.48 if k % 2 else 1.50) for k in range(1, 40)]
    r = vcp_metrics(make_df(wp, pre_price=0.9, spread=0.0))
    assert r["VCP_T"] == 0, r


def test_sub_dollar_tick_guard_scales_threshold():
    # ราคา 0.50 → 3 ticks = 6% ; แกว่ง 4% (ผ่าน 3% แต่ไม่ผ่าน 3 ticks) ต้องไม่นับ
    wp = [(0, 0.50)] + [(k, 0.48 if k % 2 else 0.50) for k in range(1, 40)]
    r = vcp_metrics(make_df(wp, pre_price=0.3, spread=0.0))
    assert r["VCP_T"] == 0, r


def test_long_uptrend_choppy_below_old_peak_low_T():
    # ยอดเก่า 100 → ลงไป 70 → uptrend 300 แท่ง (~60 สัปดาห์) แกว่ง ~3.5% ถี่ๆ ใต้ peak เก่า
    n = 300
    trend = np.linspace(70, 97, n)
    wig = np.array([1 + 0.0175 * (-1) ** (k // 3) for k in range(n)])
    px = np.concatenate([np.linspace(100, 70, 20), trend * wig])
    df = pd.DataFrame(
        {"High": px * 1.002, "Low": px * 0.998, "Close": px, "Volume": np.full(len(px), 1000.0)}
    )
    df.loc[0, "High"] = 100.0
    r = vcp_metrics(df)
    assert r["VCP_Weeks"] >= 55, r
    assert r["VCP_T"] <= 5, r


def test_deepest_pullback_on_right_reported_as_is():
    # เคส 8 ต้นฉบับ: 8% แล้ว 25% — รายงานผลตามจริง ไม่บังคับค่า (ตรวจแค่ field สมเหตุสมผล)
    wp = [(0, 100), (15, 92), (30, 99), (45, 75), (55, 90), (65, 80)]
    r = vcp_metrics(make_df(wp))
    print("   case8 actual:", r)
    assert r["VCP_T"] >= 1, r


def _random_frames():
    rng = np.random.default_rng(7)
    for _ in range(40):
        n = int(rng.integers(120, 340))
        px = 50 * np.exp(np.cumsum(rng.normal(0.0004, 0.02, n)))
        hi = px * (1 + rng.uniform(0, 0.02, n))
        lo = px * (1 - rng.uniform(0, 0.02, n))
        yield pd.DataFrame({"High": hi, "Low": lo, "Close": px, "Volume": rng.uniform(500, 1500, n)})


def test_property_negative_topivot_only_when_close_above_pivot():
    frames = list(_random_frames())
    frames.append(make_df([(0, 100), (20, 75), (35, 95), (50, 80.75), (60, 97), (70, 89.2), (80, 96)]))
    frames.append(make_df([(0, 100), (10, 90), (20, 99), (30, 89.5), (40, 99), (50, 90), (60, 98)]))
    seen = neg = 0
    for df in frames:
        r = vcp_metrics(df)
        if r["VCP_ToPivot"] is None:
            continue
        seen += 1
        close, pivot = float(df["Close"].iloc[-1]), r["VCP_Pivot"]
        if r["VCP_ToPivot"] < 0:
            neg += 1
            assert close > pivot, (close, r)
        if close > pivot * 1.001:
            assert r["VCP_ToPivot"] < 0, (close, r)
    assert seen >= 10, seen


def test_three_week_choppy_base_has_no_short_legs():
    # ฐาน 3 สัปดาห์ (15 แท่ง) แกว่ง ~4% ทุก 2 แท่ง → leg ที่ไม่ใช่ leg สุดท้ายต้องยาว ≥ 5 แท่ง
    px = [100.0] + [96.0 if k % 2 else 100.0 for k in range(1, 17)]
    n = len(px)
    high = np.array(px)
    low = np.array(px) * 0.999
    thr = swing_threshold_pct(px[-1])
    legs, _ = adaptive_swings(high, low, thr)
    for lg in legs:
        assert lg["final"] or lg["bars"] >= MIN_LEG_BARS, legs
    assert all(lg["final"] or lg["bars"] >= MIN_LEG_BARS for lg in legs)
    # และผ่าน API หลักได้โดยไม่ error
    df = pd.DataFrame({"High": high, "Low": low, "Close": high, "Volume": np.full(n, 1000.0)})
    assert vcp_metrics(df)["VCP_T"] is not None


def test_final_depth_measured_separately_narrow_right_side():
    # ฐาน 28% แล้วพักแคบ ~4% ฝั่งขวา → FinalDepth ≈ 4 (ไม่ใช่ 28), T=2
    wp = [(0, 100), (25, 72), (45, 90), (60, 86.4)]
    r = vcp_metrics(make_df(wp))
    assert 3.5 <= r["VCP_FinalDepth"] <= 5.5, r
    assert r["VCP_MaxDepth"] >= 27, r
    assert r["VCP_T"] == 2, r
    assert r["VCP_ToPivot"] >= 0, r


BREAKOUT_WP = [(0, 100), (25, 60), (45, 90), (60, 76.5), (75, 94.7)]


def test_breakout_5pct_above_pivot_gives_negative_topivot():
    r = vcp_metrics(make_df(BREAKOUT_WP))
    assert -6.0 < r["VCP_ToPivot"] < -4.0, r
    assert 90.0 <= r["VCP_Pivot"] <= 90.5, r


def test_volratio_uses_bars_since_start_of_last_T():
    pre = 60

    def vol(n, p):
        v = np.full(n, 1000.0)
        v[p + 45 :] = 600.0  # ตั้งแต่ high ของ T สุดท้าย (bar 45 ของฐาน)
        v[-2:] = 200.0
        return v

    df = make_df(BREAKOUT_WP, pre_bars=pre, vol=vol)
    r = vcp_metrics(df)
    V = df["Volume"].to_numpy()
    expected = V[pre + 45 :].mean() / V[-51:-1].mean()
    last2 = V[-2:].mean() / V[-51:-1].mean()
    assert abs(r["VCP_VolRatio"] - round(expected, 2)) <= 0.01, (r, expected)
    assert abs(r["VCP_VolRatio"] - last2) > 0.2, (r, last2)


def test_bad_inputs_do_not_crash():
    wp = [(0, 100), (25, 72), (45, 90), (60, 85)]
    base = make_df(wp)
    for vol in (np.zeros(len(base)), np.full(len(base), np.nan)):
        d = base.copy()
        d["Volume"] = vol
        r = vcp_metrics(d)  # ต้องไม่ crash
        assert r["VCP_VolRatio"] is None, r
        assert r["VCP_Footprint"] is not None, r
    short = base.iloc[:10]
    r = vcp_metrics(short)
    assert r["VCP_Footprint"] is None and r["VCP_T"] is None, r
    assert vcp_metrics(base.iloc[-30:])["VCP_Footprint"] is not None  # สั้นกว่า LOOKBACK แต่พอคำนวณ
    assert vcp_metrics(None)["VCP_T"] is None


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {name}: {e}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
