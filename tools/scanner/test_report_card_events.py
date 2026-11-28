"""Unit tests สำหรับ report_card_events.py — นิยามทุกข้อด้วยซีรีส์จำลอง (pytest หรือ `python test_report_card_events.py`)"""
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE.parents[1]), str(_HERE)]
import report_card_events as RC  # noqa: E402

N = 60
IDX = pd.bdate_range("2026-01-05", periods=N)
DATES = [d.strftime("%Y-%m-%d") for d in IDX]
RS7_FIELDS = ["ticker", "patterns", "close", "rs_rating", "pivot", "old_pivot", "new_base_high"]
LUK_FIELDS = ["symbol", "setup", "side", "close", "rs_rating", "entry_trigger", "stop_price", "ref_level"]


def frame(c, o=None, h=None, l=None):
    c = np.asarray(c, float)
    o = c.copy() if o is None else np.asarray(o, float)
    h = np.maximum(c, o) * 1.01 if h is None else np.asarray(h, float)
    l = np.minimum(c, o) * 0.99 if l is None else np.asarray(l, float)
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": 1e6}, index=IDX)


def prices(frames, set_close=None):
    s = np.full(N, 1000.0) if set_close is None else np.asarray(set_close, float)
    return RC.Prices(pd.DataFrame({"Close": s}, index=IDX), frames)


# ───────────────────────────────────────────── merge / sources
def test_live_wins_and_backfill_only_before_first_live():
    bf = {DATES[0]: {"rows": ["bf0"]}, DATES[1]: {"rows": ["bf1"]}, DATES[3]: {"rows": ["bf3"]}}
    live = {DATES[1]: {"rows": ["live1"]}, DATES[2]: {"rows": ["live2"]}}
    days, src = RC.merge_days(bf, live)
    assert list(days) == DATES[:3]
    assert days[DATES[1]]["rows"] == ["live1"] and src[DATES[1]] == "live"
    assert src[DATES[0]] == "backfill" and DATES[3] not in days          # หลัง live วันแรก = ไม่ใช้ backfill
    assert RC.source_ranges(src) == {"backfill": {"from": DATES[0], "to": DATES[0], "days": 1},
                                     "live": {"from": DATES[1], "to": DATES[2], "days": 2}}
    assert RC.merge_days({DATES[0]: {"rows": []}}, {})[1] == {DATES[0]: "backfill"}


# ───────────────────────────────────────────── RS-7 buy
def _rs7_days(hits: dict):
    return {DATES[k]: {"phase": "C", "rows": [["AAA", pats, 10.0, 90, 10.0, None, None]]} for k, pats in hits.items()}


def test_rs7_dedupe_10_trading_days():
    days = _rs7_days({0: [[3, "BREAKOUT"]], 5: [[3, "BREAKOUT"]], 10: [[3, "BREAKOUT"]], 11: [[3, "BREAKOUT"]],
                      12: [[7, "PIVOT_RECLAIM"]], 13: [[3, "SETUP"]]})
    P = prices({"AAA": frame(np.full(N, 10.0))})
    ev = RC.rs7_buy_events(days, RS7_FIELDS, P.cal_pos, {})
    assert [(e["date"], e["pattern"]) for e in ev] == [(DATES[0], 3), (DATES[11], 3), (DATES[12], 7)]


def test_rs7_entry_close_returns_pending_mfe_and_set():
    c = np.linspace(10, 16, N)
    s = np.linspace(1000, 1100, N)
    P = prices({"AAA": frame(c)}, s)
    e = RC.rs7_measure_buy({"date": DATES[10], "ticker": "AAA", "pattern": 3}, P)
    assert e["entry"] == round(c[10], 3)
    assert e["r20"] == round((c[30] / c[10] - 1) * 100, 2)
    assert e["set20"] == round((s[30] / s[10] - 1) * 100, 2)
    assert e["mfe"] == round((c[30] * 1.01 / c[10] - 1) * 100, 2)
    late = RC.rs7_measure_buy({"date": DATES[N - 8], "ticker": "AAA", "pattern": 3}, P)
    assert late["r5"] is not None and late["r10"] is None and late["r20"] is None and late["mfe"] is None
    g = RC.rs7_group([e, late])
    assert g["d20"]["n"] == 1 and g["d20"]["pending"] == 1 and g["d5"]["pending"] == 0
    assert g["small_sample"] is True and g["d20"]["win_pct"] == 100.0


def test_small_sample_flag_threshold():
    assert RC.horizon_stats([1.0] * 29)["small_sample"] is True
    assert RC.horizon_stats([1.0] * 30)["small_sample"] is False
    st = RC.horizon_stats([2.0, -1.0, 0.0], [1.0, 1.0, 1.0])
    assert st["win_pct"] == 33.3 and st["excess_avg"] == round(np.mean([1.0, -2.0, -1.0]), 2)


# ───────────────────────────────────────────── RS-7 watch
def test_rs7_watch_entry_after_gap_and_conversion():
    days = _rs7_days({0: [[6, "REST"]], 3: [[6, "REST"]], 15: [[6, "REST"]], 21: [[6, "BREAKOUT"]]})
    P = prices({"AAA": frame(np.linspace(10, 12, N))})
    ens = RC.rs7_watch_entries(days, RS7_FIELDS, P.cal_pos, {})
    assert [e["date"] for e in ens] == [DATES[0], DATES[15]]          # 3 = ยังอยู่ · 15 = หายไป 11 วัน → ใหม่
    trig = {"AAA": [21]}
    m0 = RC.rs7_measure_watch(ens[0], trig, P)
    m1 = RC.rs7_measure_watch(ens[1], trig, P)
    assert m0["converted"] is False and m1["converted"] is True      # 21 อยู่ใน 1–20 วันหลังวันที่ 15 แต่เกิน 20 วันหลังวันที่ 0
    pend = RC.rs7_measure_watch({"date": DATES[N - 5], "ticker": "AAA"}, {}, P)
    assert pend["converted"] is None and pend["r20"] is None


# ───────────────────────────────────────────── Luk long
def _luk(P, k, trig, stop, close_hits=None):
    return RC.luk_measure_long({"date": DATES[k], "symbol": "AAA", "setup": "LONG_PULLBACK", "regime": "LONG",
                                "close_hits": close_hits if close_hits is not None else P.px["AAA"]["c"][k],
                                "entry_trigger": trig, "stop_price": stop}, P)


def test_luk_not_triggered_and_gap_above_trigger():
    c = np.full(N, 10.0)
    o = c.copy(); h = c * 1.005; l = c * 0.995
    P = prices({"AAA": frame(c, o, h, l)})
    assert _luk(P, 10, 10.2, 9.9)["triggered"] is False              # high วันถัดไป 10.05 < 10.2
    o2 = o.copy(); o2[11] = 10.4; h2 = h.copy(); h2[11] = 10.5; c2 = c.copy(); c2[11] = 10.45
    P2 = prices({"AAA": frame(c2, o2, h2, l)})
    e = _luk(P2, 10, 10.2, 9.9)
    assert e["triggered"] and e["entry"] == 10.4 and e["gap_above_trigger"] is True


def test_luk_stopped_on_entry_day_is_conservative():
    c = np.full(N, 10.0); o = c.copy(); h = c * 1.005; l = c * 0.995
    h[11] = 10.3; l[11] = 9.8                                         # แท่งเดียวแตะทั้ง trigger 10.2 และ stop 9.9
    P = prices({"AAA": frame(c, o, h, l)})
    e = _luk(P, 10, 10.2, 9.9)
    assert e["triggered"] and e["stopped_entry_day"] is True
    assert e["r_stop"] == round((9.9 / 10.2 - 1) * 100, 2)
    assert e["r10"] == round((c[21] / 10.2 - 1) * 100, 2)            # ถือครบไม่สน stop


def test_luk_gap_below_stop_exits_at_open():
    c = np.full(N, 10.0); o = c.copy(); h = c * 1.005; l = c * 0.995
    h[11] = 10.3; c[11] = 10.25; l[11] = 10.1                         # เข้า 10.2 ไม่โดน stop วันเข้า
    o[14] = 9.6; l[14] = 9.5; c[14] = 9.7; h[14] = 9.8                # วันที่ 3 เปิด gap ต่ำกว่า stop 9.9
    P = prices({"AAA": frame(c, o, h, l)})
    e = _luk(P, 10, 10.2, 9.9)
    assert e["stopped_entry_day"] is False and e["r_stop"] == round((9.6 / 10.2 - 1) * 100, 2)


def test_luk_pending_next_day_and_dividend_scaling():
    c = np.full(N, 10.0); P = prices({"AAA": frame(c)})
    assert _luk(P, N - 1, 10.2, 9.9)["triggered"] is None            # ยังไม่มีวันถัดไป
    g = RC.luk_long_group([_luk(P, N - 1, 10.2, 9.9)])
    assert g["pending"] == 1 and g["trigger_pct"] is None
    # hits บันทึก close 20 ตอนนั้น (ก่อนปรับราคา) → trigger 20.4 ถูกปรับเป็น 10.2
    c2 = np.full(N, 10.0); h2 = c2 * 1.005; h2[11] = 10.25
    P2 = prices({"AAA": frame(c2, h=h2)})
    e = _luk(P2, 10, 20.4, 19.8, close_hits=20.0)
    assert e["triggered"] is True and e["entry"] == 10.2


# ───────────────────────────────────────────── Luk weak
def test_luk_weak_vs_set():
    c = np.linspace(10, 9, N); s = np.linspace(1000, 1100, N)
    P = prices({"AAA": frame(c)}, s)
    e = RC.luk_measure_weak({"date": DATES[5], "symbol": "AAA", "setup": "SHORT_WEAK_BOUNCE"}, P)
    assert e["r5"] < 0 < e["set5"]
    g = RC.luk_weak_group([e])
    assert g["underperform_5_pct"] == 100.0 and g["small_sample"] is True


# ───────────────────────────────────────────── end-to-end files
def test_build_end_to_end_live_today_and_backfill():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        hist, sd, today = d / "hist", d / "sd", d / "today"
        for p in (hist, sd / "data" / "backfill", sd / "data" / "history" / DATES[40], today):
            p.mkdir(parents=True)
        pd.DataFrame({"Open": 1000.0, "High": 1001.0, "Low": 999.0, "Close": np.linspace(1000, 1100, N), "Volume": 1e9},
                     index=IDX).to_csv(hist / "SET_INDEX.csv")
        frame(np.linspace(10, 14, N)).to_csv(hist / "AAA.csv")
        bf = {"fields": RS7_FIELDS, "days": {DATES[k]: {"phase": "C", "rows": [["AAA", [[3, "BREAKOUT"]], 10.0, 90, 10.0, None, None]]}
                                             for k in (5, 41)}}
        json.dump(bf, open(sd / "data" / "backfill" / "rs7_hits_backfill.json", "w"))
        live = {"generated_at": "x", "as_of": DATES[40], "phase": "C", "fields": RS7_FIELDS,
                "rows": [["AAA", [[7, "PIVOT_RECLAIM"]], 12.0, 95, 12.0, 11.5, None]]}
        json.dump(live, open(sd / "data" / "history" / DATES[40] / "rs7_hits.json", "w"))
        luk_today = {"generated_at": "x", "as_of": DATES[45], "regime": {"mode": "LONG"}, "fields": LUK_FIELDS,
                     "rows": [["AAA", "LONG_PULLBACK", "long", 13.0, 99, 13.2, 12.9, 12.8]]}
        json.dump(luk_today, open(today / "luk_hits.json", "w"))
        out = RC.build(hist, hist / "SET_INDEX.csv", sd, today, log=lambda *_: None)
        assert out["generated_at"] and out["as_of"] == DATES[-1]
        assert out["sources"]["rs7"] == {"backfill": {"from": DATES[5], "to": DATES[5], "days": 1},
                                         "live": {"from": DATES[40], "to": DATES[40], "days": 1}}
        assert [(r["date"], r["source"]) for r in out["rs7"]["recent"]] == [(DATES[40], "live"), (DATES[5], "backfill")]
        assert out["luk"]["long"]["all"]["n_events"] == 1 and out["sources"]["luk"]["live"]["from"] == DATES[45]
        assert any("luk" in w for w in out["warnings"])                 # ไม่มี backfill ของ luk → เตือน
        RC.write_json(out, d / "rc.json")
        assert json.load(open(d / "rc.json", encoding="utf-8"))["small_sample_n"] == RC.SMALL_SAMPLE


if __name__ == "__main__":
    fns = [v for k, v in dict(globals()).items() if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"OK {len(fns)} tests")
