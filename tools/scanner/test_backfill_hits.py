"""Unit tests สำหรับ backfill_hits.py — point-in-time: วันย้อนหลังต้องเท่ากับสแกนจริงที่รันบนข้อมูลที่ตัดถึงวันนั้น"""
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE.parents[1]), str(_HERE)]
import backfill_hits as BF  # noqa: E402
import scan_luk as L  # noqa: E402
import scan_rs7 as R  # noqa: E402
from tools.core.rs_score import rs_raw, rs_ratings  # noqa: E402

N = 330
IDX = pd.bdate_range("2024-01-01", periods=N)
T2S = {"LEAD": {"sector": "Technology", "subsector": "Electronic Components"}}


def path(points):
    xs, ys = zip(*points)
    return np.interp(np.arange(N), xs, ys)


def frame(c, vol=5_000_000.0, spread=0.02):
    c = np.asarray(c, float)
    return pd.DataFrame({"Open": c, "High": c * (1 + spread), "Low": c * (1 - spread), "Close": c, "Volume": vol}, index=IDX)


def _universe():
    rng = np.random.default_rng(7)
    out = {
        "LEAD": frame(path([(0, 40), (250, 100), (256, 97.5), (300, 120), (N - 1, 130)])),   # ย่อเข้า EMA ช่วงแท่ง ~256
        "PARA": frame(np.r_[np.full(N - 20, 20.0), [20.8, 21.8, 23.2, 25.0, 26.8, 28.8, 31.0], np.full(13, 31.0)]),
        "FLAT": frame(np.full(N, 30.0) * (1 + 0.01 * np.sin(np.arange(N) / 5))),
    }
    for k in range(4):
        out[f"RW{k}"] = frame(50 * np.exp(np.cumsum(rng.normal(0.0008, 0.02, N))))
    return out


def _write(d: Path, frames: dict, upto=None):
    d.mkdir(parents=True, exist_ok=True)
    s = pd.DataFrame({"Open": path([(0, 1300), (N - 1, 1500)]), "High": path([(0, 1310), (N - 1, 1510)]),
                      "Low": path([(0, 1290), (N - 1, 1490)]), "Close": path([(0, 1300), (N - 1, 1500)]),
                      "Volume": 1e10}, index=IDX)
    cut = (lambda df: df) if upto is None else (lambda df: df[df.index <= upto])
    cut(s).to_csv(d / "SET_INDEX.csv")
    for t, df in frames.items():
        cut(df).to_csv(d / f"{t}.csv")
    # rs_ranking ของวันนั้น = สูตรเดียวกับ 3_calculate_rs
    raw = pd.Series({t: rs_raw(cut(df)["Close"]) for t, df in frames.items()}).dropna()
    pd.DataFrame({"Ticker": raw.index, "RS_Rating": rs_ratings(raw, pd.Series(False, index=raw.index)).to_numpy()}) \
        .to_csv(d / "rs.csv", index=False)


def _patch():
    BF.load_fund_tickers = lambda: {"FUNDX"}
    L.load_fund_tickers = lambda: {"FUNDX"}
    R.load_fund_tickers = lambda: {"FUNDX"}
    L.load_sector_map = lambda: T2S
    R.load_sector_map = lambda: T2S


def test_backfill_day_equals_live_scan_on_cut_data():
    _patch()
    frames = _universe()
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(d / "full", frames)
        res = BF.run(d / "full", d / "full" / "SET_INDEX.csv", d / "out", log=lambda *_: None)
        assert (d / "out" / BF.RS7_FILE).exists() and (d / "out" / BF.LUK_FILE).exists()
        luk_days, rs7_days = res["luk"]["days"], res["rs7"]["days"]
        assert res["luk"]["fields"] == L.hits_from({"generated_at": None, "as_of": None})["fields"]
        assert res["rs7"]["fields"] == R.HITS_FIELDS and res["rs7"]["generated_at"] and res["rs7"]["from"]
        with_rows = [k for k, v in luk_days.items() if v["rows"]]
        assert with_rows, "synthetic universe should produce Luk rows"
        checks = [with_rows[0], with_rows[len(with_rows) // 2], sorted(rs7_days)[len(rs7_days) // 2]]
        for day in checks:
            _write(d / day, frames, upto=day)
            live_l = L.hits_from(L.build(d / day, d / day / "SET_INDEX.csv", d / day / "rs.csv", log=lambda *_: None))
            assert luk_days[day]["rows"] == live_l["rows"], day
            assert luk_days[day]["regime"] == live_l["regime"], day
            if day in rs7_days:
                live_r = R.hits_from(R.build(d / day, d / day / "SET_INDEX.csv", d / day / "rs.csv", d / "none.csv",
                                             log=lambda *_: None))
                assert rs7_days[day]["rows"] == live_r["rows"], day
                assert rs7_days[day]["phase"] == live_r["phase"], day


def test_rerun_overwrites_and_date_range():
    _patch()
    frames = _universe()
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _write(d / "full", frames)
        mid = IDX[300].strftime("%Y-%m-%d")
        r1 = BF.run(d / "full", d / "full" / "SET_INDEX.csv", d / "out", date_to=mid, only="luk", log=lambda *_: None)
        assert r1["luk"]["to"] == mid and "rs7" not in r1
        r2 = BF.run(d / "full", d / "full" / "SET_INDEX.csv", d / "out", only="luk", log=lambda *_: None)
        assert r2["luk"]["to"] == IDX[-1].strftime("%Y-%m-%d")
        assert r2["luk"]["days"][mid] == r1["luk"]["days"][mid]                     # ข้อมูลหลังวันนั้นไม่กระทบ


if __name__ == "__main__":
    fns = [v for k, v in dict(globals()).items() if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"OK {len(fns)} tests")
