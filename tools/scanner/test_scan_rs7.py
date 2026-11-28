"""Unit tests สำหรับ RS-7 (scan_rs7.py) + rs_line.py + rs_score.py — รันด้วย pytest หรือ `python test_scan_rs7.py`

- phase ใช้ SET จริง (fixtures/rs7/SET_INDEX_2026-11-27.csv: OHLC จาก TradingView ผ่าน breadth.json /
  SET_INDEX.csv · Volume = มูลค่าซื้อขายรวม universe ตามนิยามเดิม แต่ก่อน 2026-06-05 คำนวณใหม่จากไฟล์หุ้นในเครื่อง)
- รูปแบบ 1–7 ใช้ series สังเคราะห์ + ตารางดัชนี (f) ที่กำหนดเอง เพื่อทดสอบกฎทีละข้อ
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE.parents[1]), str(_HERE)]
from tools.core import rs_line as rsl  # noqa: E402
from tools.core.rs_score import rs_ratings, rs_raw  # noqa: E402
import scan_rs7 as R  # noqa: E402
from rs7_config import RS7_CONFIG  # noqa: E402
from utils import calculate_adtv  # noqa: E402

FIX = _HERE / "fixtures" / "rs7" / "SET_INDEX_2026-11-27.csv"
N = 340


# ───────────────────────────────────────────── builders
def path(points, n=N):
    """เส้นราคาปิดเชื่อมจุด [(bar, price), ...]"""
    xs, ys = zip(*points)
    return np.interp(np.arange(n), xs, ys)


def stock(close, vol=1_000_000.0, spread=0.004, start="2025-01-01"):
    close = np.asarray(close, dtype=float)
    idx = pd.bdate_range(start, periods=len(close))
    v = np.full(len(close), vol) if np.isscalar(vol) else np.asarray(vol, dtype=float)
    return pd.DataFrame({"Open": close, "High": close * (1 + spread), "Low": close * (1 - spread),
                         "Close": close, "Volume": v}, index=idx)


def index_frame(df, idx_close, phase="-", bsl=200.0, rally=0.0, corr=True, weak=False):
    f = pd.DataFrame(index=df.index)
    f["idx"] = np.asarray(idx_close, dtype=float)
    for k, v in (("phase", phase), ("bsl", bsl), ("rally", rally), ("corr_recent", corr), ("idx_weak", weak)):
        f[k] = v
    return f


def rr(df, val=90):
    return pd.Series(float(val), index=df.index)


def status(df, f, rating=90, pid=None, cfg=RS7_CONFIG):
    o = R.stock_frame(df, f, rr(df, rating), cfg, eval_from=len(df) - 60)
    last = o.iloc[-1]
    if pid is None:
        return {p: last[f"p{p}"] for p in R.PATTERNS if last[f"p{p}"]}
    return last[f"p{pid}"]


def tight_base(n=N, top=100.0, base_len=15, lo=0.985):
    """ขาขึ้นยาวไปถึง top แล้วพักแคบ base_len แท่ง (ช่วงราคา lo×top–top)"""
    c = path([(0, 40), (n - base_len - 1, top), (n - 1, top)], n)
    c[n - base_len:] = top * np.where(np.arange(base_len) % 2 == 0, 1.0, lo)
    return c


# ───────────────────────────────────────────── rs_line / rs_score
def test_rs_line_ffill_on_stock_holiday_row():
    d = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"])
    close = pd.Series([10.0, 11.0, 12.0], index=d)
    idx = pd.Series([100.0, 110.0], index=d[[0, 2]])     # ดัชนีไม่มีแท่ง 6 ม.ค.
    assert rsl.rs_line(close, idx).round(4).tolist() == [0.1, 0.11, round(12 / 110, 4)]


def test_rs_nh_near_gap():
    rs = pd.Series(np.r_[np.linspace(1, 2, 200), [1.95, 2.01, 1.80]])
    assert bool(rsl.rs_nh(rs).iloc[-2]) and not bool(rsl.rs_nh(rs).iloc[-3])
    assert bool(rsl.rs_near(rs).iloc[-3]) and not bool(rsl.rs_near(rs).iloc[-1])
    assert round(float(rsl.rs_gap(rs).iloc[-1]), 4) == round(1.80 / 2.01 - 1, 4)


def test_rs_raw_and_ratings_formula():
    s = pd.Series(np.linspace(100, 200, 300))
    expect = sum(w * (200 / s.iloc[-p] - 1) for p, w in zip([63, 126, 189, 252], [0.4, 0.2, 0.2, 0.2]))
    assert abs(rs_raw(s) - expect) < 1e-12
    assert rs_raw(s.iloc[:125]) is None
    raw = pd.Series({"A": 0.5, "B": 0.1, "F": 9.0})
    r = rs_ratings(raw, pd.Series({"A": False, "B": False, "F": True}))
    assert int(r["A"]) == 99 and pd.isna(r["F"])


def test_rs_rating_history_last_day_equals_rs_raw_ranking():
    d = pd.bdate_range("2025-01-01", periods=300)
    closes = {t: pd.Series(np.linspace(10, 10 + k, 300), index=d) for k, t in enumerate(["A", "B", "C"], start=1)}
    h = R.rs_rating_history(closes, set(), list(d[-3:]))
    expect = rs_ratings(pd.Series({t: rs_raw(s) for t, s in closes.items()}), pd.Series(False, index=list(closes)))
    assert h.iloc[-1].astype(int).to_dict() == expect.astype(int).to_dict()


# ───────────────────────────────────────────── phase (SET จริง)
def _real_phase(upto=None):
    s = R.read_set_index(FIX)
    if upto:
        s = s[s.index <= upto]
    f = R.index_features(s)
    return s, f


def test_phase_today_is_A_with_spec_numbers():
    s, f = _real_phase()
    p = R.phase_summary(s, f)
    assert p["today"] == "A" and p["date"] == "2026-11-27"
    assert (p["idx_high"], p["idx_high_date"]) == (1652.97, "2026-07-21")
    assert (p["idx_low"], p["idx_low_date"]) == (1414.44, "2026-11-26")
    assert round(p["idx_dd"], 3) == -0.129 and round(p["above_low"], 3) == 0.018
    assert p["bars_since_low"] == 1 and p["since"] == "2026-11-02"
    assert p["last_ftd"]["date"] == "2026-06-29" and p["last_ftd"]["rally_day"] == 4


def test_phase_b_after_system_ftd_and_c_in_uptrend():
    _, f = _real_phase()
    assert f.loc["2025-07-17", "phase"] == "B" and bool(f.loc["2025-07-17", "ftd"])
    assert f.loc["2026-05-06", "phase"] == "C"
    assert f.loc["2025-02-28", "phase"] == "A"


def test_phase_b_needs_index_away_from_high():
    # ลำดับ A > B > C (สเปก) + B ต้องห่าง high > b_max_near_high
    # 19 ก.พ. 2026: FTD ของระบบ + เคยปรับฐานใน 126 วัน แต่ดัชนีอยู่ที่ high → ไม่ใช่ B (เป็น C)
    _, f = _real_phase()
    assert f.loc["2026-02-19", "phase"] == "C" and bool(f.loc["2026-02-19", "ftd"])
    assert f.loc["2026-02-19", "idx_dd"] == 0.0
    b = f["phase"] == "B"
    assert (f.loc[b, "idx_dd"] <= -RS7_CONFIG["b_max_near_high"]).all()
    jul25 = b[(b.index >= "2025-07-01") & (b.index <= "2025-08-31")]
    assert int(jul25.sum()) >= 8                                     # รอบฟื้นจริง ก.ค. 2025 (FTD 17 ก.ค.)
    assert f.loc["2025-07-18", "phase"] == "B" and f.loc["2025-07-25", "phase"] == "B"
    assert f["phase"].iloc[-1] == "A"                                # 27 พ.ย. 2026


# ───────────────────────────────────────────── 1 Predictive
def _pred_setup():
    c = tight_base()
    idx = path([(0, 100), (N - 45, 100), (N - 1, 82)])     # ดัชนีลง 18% ช่วงท้าย
    df = stock(c)
    return df, index_frame(df, idx, phase="A", bsl=0)


def test_predictive_setup_and_breakout():
    df, f = _pred_setup()
    assert status(df, f, pid=1) == "SETUP"
    df2 = df.copy()
    df2.iloc[-1, df2.columns.get_loc("Close")] = 102.0
    df2.iloc[-1, df2.columns.get_loc("High")] = 102.5
    df2.iloc[-1, df2.columns.get_loc("Volume")] = 3_000_000
    assert status(df2, f, pid=1) == "BREAKOUT"


def test_predictive_only_in_phase_A():
    df, f = _pred_setup()
    f["phase"] = "C"
    assert status(df, f, pid=1) == ""


def test_liquidity_filters():
    df, f = _pred_setup()
    thin = df.copy()
    thin["Volume"] = 50_000                    # มูลค่า ~5 ลบ./วัน < 10
    assert status(thin, f) == {}
    cheap = df.copy()
    cheap[["Open", "High", "Low", "Close"]] /= 60   # ราคา < 2 บาท (มูลค่ายังพอ: วอลุ่มคูณเพิ่ม)
    cheap["Volume"] *= 60
    assert status(cheap, f) == {}
    assert abs(R.stock_frame(df, f, rr(df), eval_from=len(df) - 5)["adtv_mb"].iloc[-1]
               - calculate_adtv(df["Close"], df["Volume"], 50)) < 1e-9


# ───────────────────────────────────────────── 2 Coincident
def test_coincident_breakout_in_phase_B():
    c = tight_base()
    c[-1] = 102.0
    v = np.full(N, 1_000_000.0)
    v[-1] = 3_000_000
    df = stock(c, v)
    f = index_frame(df, path([(0, 100), (N - 12, 90), (N - 1, 91)]), phase="B", bsl=11)
    assert status(df, f, pid=2) == "BREAKOUT"
    df.iloc[-1, df.columns.get_loc("Volume")] = 1_000_000          # วอลุ่มไม่พอ = ไม่ใช่
    assert status(df, f, pid=2) == ""


# ───────────────────────────────────────────── 3 Confirming
def test_confirming_setup_breakout_and_rs_rating_gate():
    c = tight_base()
    df = stock(c)
    f = index_frame(df, np.full(N, 100.0), phase="C", bsl=60, rally=0.15)
    assert status(df, f, pid=3) == "SETUP"
    assert status(df, f, rating=60, pid=3) == ""                     # TT ข้อ 8 (RS ≥ 70)
    df.iloc[-1, df.columns.get_loc("Close")] = 102.0
    df.iloc[-1, df.columns.get_loc("High")] = 102.5
    df.iloc[-1, df.columns.get_loc("Volume")] = 3_000_000
    assert status(df, f, pid=3) == "BREAKOUT"


# ───────────────────────────────────────────── 4 Sore thumbs
def test_sore_thumb_watch():
    c = path([(0, 40), (N - 1, 100)])
    df = stock(c)
    f = index_frame(df, path([(0, 100), (N - 70, 100), (N - 1, 85)]), phase="A", weak=True)
    assert status(df, f, pid=4) == "WATCH"
    f["idx_weak"] = False
    assert status(df, f, pid=4) == ""


# ───────────────────────────────────────────── 5 Fast rebound
def test_fast_rebound_running():
    c = path([(0, 40), (N - 50, 100), (N - 25, 72), (N - 1, 99)])
    df = stock(c)
    f = index_frame(df, path([(0, 100), (N - 25, 85), (N - 1, 88)]), phase="B", bsl=24, rally=0.035)
    assert status(df, f, pid=5) in ("RUNNING", "EXTENDED")
    f["bsl"] = 60                                                    # นอก 3–40 แท่ง
    assert status(df, f, pid=5) == ""


# ───────────────────────────────────────────── 6 Lead and rest
def test_lead_and_rest():
    n_low = N - 30
    c = path([(0, 40), (n_low, 100), (N - 1, 100)])
    c[n_low:] = 100 * np.where(np.arange(N - n_low) % 2 == 0, 1.0, 0.98)
    c[-1] = 100.0
    idx = path([(0, 100), (n_low - 60, 100), (n_low, 80), (N - 1, 90)])
    bsl = np.maximum(0, np.arange(N) - n_low).astype(float)
    df = stock(c)
    f = index_frame(df, idx, phase="C", bsl=bsl, rally=idx / 80 - 1)
    assert status(df, f, pid=6) == "REST"


# ───────────────────────────────────────────── 7 Failure reset
def _breakout_then(after, n=N, k=None, vol_k=3_000_000):
    """ฐาน 20 แท่ง 95–100 → เบรกแท่ง k ที่ 103 (วอลุ่ม 3×) → ราคาหลังเบรกตาม after (list)"""
    k = k or n - len(after) - 1
    c = path([(0, 40), (k - 25, 97), (k - 1, 99)], n)
    c[k - 20:k] = np.where(np.arange(20) % 2 == 0, 100.0, 96.0)
    c[k] = 103.0
    c[k + 1:] = after
    v = np.full(n, 1_000_000.0)
    v[k] = vol_k
    return c, v, k


def _pivot_reset_case():
    after = [101, 98, 96, 96.5] + [97.0, 98.0] * 8               # ตบกลับใต้ pivot ≥ 3% แต่ไม่หลุดฐาน (96)
    c, v, k = _breakout_then(after)
    df = stock(c, v, spread=0.002)
    df["Low"] = np.minimum(df["Low"], df["Close"])
    df.iloc[k + 1:, df.columns.get_loc("Low")] = df["Close"].iloc[k + 1:] * 0.999
    f = index_frame(df, np.full(N, 100.0), phase="A", bsl=0)
    f["idx"] = path([(0, 100), (N - 30, 100), (N - 1, 90)])       # RS ขึ้นเทียบ 20 วันก่อน
    return df, f


def test_pivot_reset_then_reclaim():
    df, f = _pivot_reset_case()
    assert status(df, f, pid=7) == "PIVOT_RESET"
    df2 = df.copy()
    df2.iloc[-1, df2.columns.get_loc("Close")] = 101.0
    df2.iloc[-1, df2.columns.get_loc("High")] = 101.3
    df2.iloc[-1, df2.columns.get_loc("Volume")] = 3_000_000
    assert status(df2, f, pid=7) == "PIVOT_RECLAIM"


def test_pivot_reset_filtered_by_rs_rating_and_distance():
    df, f = _pivot_reset_case()
    assert status(df, f, rating=RS7_CONFIG["piv_min_rs_rating"] - 1, pid=7) == ""   # กรณี PR9/PTG/BDMS
    assert status(df, f, rating=RS7_CONFIG["piv_min_rs_rating"], pid=7) == "PIVOT_RESET"
    far = df.copy()
    far.iloc[-100, far.columns.get_loc("High")] = 140.0              # high 52 สัปดาห์ไกล → ห่าง high > 25%
    assert status(far, f, pid=7) == ""


def _base_reset_case():
    after = [99, 95, 90, 86, 84] + [86.5, 88.0] * 10              # หลุดฐาน (96) ต่ำสุด 84 แล้วตั้งฐานใหม่ 86–88
    c, v, k = _breakout_then(after)
    df = stock(c, v, spread=0.002)
    f = index_frame(df, path([(0, 100), (N - 40, 100), (N - 1, 90)]), phase="A", bsl=0)
    return df, f


def test_base_reset_then_base_breakout():
    df, f = _base_reset_case()
    assert status(df, f, pid=7) == "BASE_RESET"
    df2 = df.copy()
    df2.iloc[-1, df2.columns.get_loc("Close")] = 90.0
    df2.iloc[-1, df2.columns.get_loc("High")] = 90.2
    df2.iloc[-1, df2.columns.get_loc("Volume")] = 3_000_000
    assert status(df2, f, pid=7) == "BASE_BREAKOUT"


def test_base_reset_filtered_by_rs_rating_and_distance():
    df, f = _base_reset_case()
    assert status(df, f, rating=24, pid=7) == ""                     # กรณี MTC: RS Rating < 70
    far = df.copy()
    far.iloc[-100, far.columns.get_loc("High")] = 130.0              # high 52 สัปดาห์ไกล → ห่าง high > 25%
    assert status(far, f, pid=7) == ""


def test_build_never_crashes_on_missing_set_index(tmp_path):
    out = R.build(tmp_path, tmp_path / "missing.csv", tmp_path / "rs.csv", tmp_path / "st.csv")
    assert out["stocks"] == [] and out["warnings"]
    R.write_json(out, tmp_path / "rs7.json")
    assert (tmp_path / "rs7.json").exists()


def test_rs7_hits_compact_rows():
    out = {"generated_at": "2026-11-27T18:05:00+07:00", "as_of": "2026-11-27", "phase": {"today": "A"},
           "stocks": [{"ticker": "BBL", "patterns": [{"id": 7, "key": "FAILURE_RESET", "status": "PIVOT_RESET"}],
                       "close": 187.0, "rs_rating": 78, "pivot": 204.0, "reset": {"type": "pivot", "old_pivot": 193.0}},
                      {"ticker": "PRM", "patterns": [{"id": 3, "key": "CONFIRMING", "status": "BREAKOUT"}],
                       "close": 9.8, "rs_rating": 87, "pivot": 9.55, "reset": None}]}
    h = R.hits_from(out)
    assert h["generated_at"] and h["as_of"] == "2026-11-27" and h["phase"] == "A"
    assert h["fields"] == ["ticker", "patterns", "close", "rs_rating", "pivot", "old_pivot", "new_base_high"]
    assert h["rows"][0] == ["BBL", [[7, "PIVOT_RESET"]], 187.0, 78, 204.0, 193.0, None]
    assert h["rows"][1] == ["PRM", [[3, "BREAKOUT"]], 9.8, 87, 9.55, None, None]
    assert R.hits_from({"generated_at": "x", "as_of": None, "phase": {}, "stocks": []})["rows"] == []


if __name__ == "__main__":
    import tempfile
    fns = [v for k, v in dict(globals()).items() if k.startswith("test_") and callable(v)]
    for fn in fns:
        if "tmp_path" in fn.__code__.co_varnames[:fn.__code__.co_argcount]:
            with tempfile.TemporaryDirectory() as d:
                fn(Path(d))
        else:
            fn()
    print(f"OK {len(fns)} tests")
