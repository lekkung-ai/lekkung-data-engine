"""Unit tests สำหรับ build_overview.py (Overview v2) + set_holidays.previous_trading_day
รันด้วย pytest หรือ `python test_build_overview.py`

ข้อมูลจำลองทั้งหมด: หุ้นสังเคราะห์ (ราคาคงที่ / ขึ้นเป็นเส้นตรง) เพื่อทดสอบเกณฑ์ทีละข้อ
ไฟสัญญาณใช้ SET จริงจาก fixtures/rs7/SET_INDEX_2026-11-27.csv (ช่วง A ณ 27 พ.ย.)
"""
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE.parents[1]), str(_HERE)]
import build_overview as B  # noqa: E402
import overview_config as C  # noqa: E402
from scan_rs7 import read_set_index  # noqa: E402
from tools.core.set_holidays import previous_trading_day  # noqa: E402

FIX_SET = _HERE / "fixtures" / "rs7" / "SET_INDEX_2026-11-27.csv"
AS_OF = pd.Timestamp("2026-11-27")
DAYS = pd.bdate_range(end=AS_OF, periods=300)


# ───────────────────────────────────────────── builders
def metrics(close=10.0, chg=1.0, ema10=9.5, val20=50.0, adtv50=50.0, perf5=2.0, vol=1e6, volx50=1.2, hi52=10.5):
    return {"close": close, "chg": chg, "ema10": ema10, "val20": val20, "adtv50": adtv50, "perf5": perf5,
            "vol": vol, "volx50": volx50, "hi52": hi52}


T2S = {
    **{f"E{i}": {"market": "SET", "sector": "Technology", "subsector": "Electronic Components"} for i in range(6)},
    **{f"B{i}": {"market": "SET", "sector": "Financials", "subsector": "Banking"} for i in range(5)},
    **{f"M{i}": {"market": "MAI", "sector": "Technology"} for i in range(5)},
    **{f"S{i}": {"market": "SET", "sector": "Services", "subsector": "Commerce"} for i in range(4)},
    "FUND1": {"market": "SET", "sector": "Property", "subsector": "Property Fund & REITs"},
    "LEAD": {"market": "SET", "sector": "Services", "subsector": "Commerce"},
}


def stock_csv(path: Path, close, vol=2_000_000.0, days=DAYS):
    close = np.asarray(close, dtype=float)
    df = pd.DataFrame({"Open": close, "High": close * 1.005, "Low": close * 0.995, "Close": close,
                       "Volume": vol}, index=days)
    df["52W_High"] = df["High"].rolling(252).max()
    df.index.name = "Date"
    df.to_csv(path)


def make_env(tmp: Path, with_prev=True, with_events=True, breadth_stage="Correction"):
    """history (CSV หุ้น + SET_INDEX) · today-dir (ไฟล์ scan ของวันนี้) · stockdesk (history วันก่อน + capital_events)"""
    hist, today, sd = tmp / "hist", tmp / "today", tmp / "sd"
    for p in (hist, today, sd / "data" / "history", sd / "public" / "data", sd / "data" / "scans"):
        p.mkdir(parents=True, exist_ok=True)
    rising = np.linspace(5, 10, len(DAYS))          # ใกล้ high · เหนือ EMA10
    for i in range(6):
        stock_csv(hist / f"E{i}.csv", rising * (1 + i / 100))
    for i in range(5):
        stock_csv(hist / f"B{i}.csv", np.full(len(DAYS), 20.0))
    stock_csv(hist / "FUND1.csv", rising)
    stock_csv(hist / "LEAD.csv", rising)
    s = read_set_index(FIX_SET)
    s = s[s.index <= AS_OF].reindex(DAYS).ffill()
    s.index.name = "Date"
    s.to_csv(hist / "SET_INDEX.csv")

    stage = [{"Ticker": t, "Stage": "S.Bull"} for t in ("E0", "E1", "LEAD", "FUND1")] + \
        [{"Ticker": f"B{i}", "Stage": "Bear"} for i in range(5)]
    rs = [{"Ticker": t, "RS_Rating": 90} for t in ("E0", "E1", "LEAD", "FUND1")]
    (today / "market_stage.json").write_text(json.dumps(stage))
    (today / "rs_ranking.json").write_text(json.dumps(rs))
    (today / "breadth.json").write_text(json.dumps({
        "market_stage": {"stage": breadth_stage, "dd_count_25d": 8},
        "breadth": [{"date": "2026-11-27", "pct_above_ma50": 18.0, "pct_above_ma200": 40.8}]}))
    (today / "sepa.json").write_text(json.dumps([{"Ticker": "E0"}, {"Ticker": "E1"}]))
    (today / "oliver_kell.json").write_text(json.dumps([{"ticker": "E0"}]))
    (today / "breakout.json").write_text(json.dumps([]))
    (today / "combined.json").write_text(json.dumps({"data": [{"ticker": "E0", "sepa": True, "kell": True},
                                                              {"ticker": "E1", "sepa": True, "kell": False}]}))
    (sd / "data" / "scans" / "sector_map.json").write_text(json.dumps({"ticker_to_sector": T2S}))
    if with_prev:
        prev = sd / "data" / "history" / "2026-11-26"
        prev.mkdir()
        (prev / "sepa.json").write_text(json.dumps([{"Ticker": "E0"}]))
        (prev / "oliver_kell.json").write_text(json.dumps([{"ticker": "E0"}, {"ticker": "E1"}]))
        (prev / "combined.json").write_text(json.dumps({"data": []}))
        (prev / "market_stage.json").write_text(json.dumps([{"Ticker": "E0", "Stage": "Bull"}]))
    if with_events:
        (sd / "public" / "data" / "capital_events.json").write_text(json.dumps({"events": [
            {"kind": "board", "ticker": "LEAD", "date": "2026-11-06", "tags": ["PP", "W"]},
            {"kind": "listing", "ticker": "LEAD", "date": "2026-11-23", "source_type": "PP", "price": 5.0},
            {"kind": "board", "ticker": "E0", "date": "2026-10-01", "tags": ["PP"]},   # เกิน 20 วันทำการ
            {"kind": "board", "ticker": "E1", "date": "2026-11-20", "tags": ["RO"]},   # ไม่ใช่ PP
        ]}))
    return hist, today, sd


def run_build(tmp, **kw):
    hist, today, sd = make_env(tmp, **kw)
    return B.build(hist, hist / "SET_INDEX.csv", today, sd, sector_map=sd / "data" / "scans" / "sector_map.json",
                   log=lambda *_: None)


# ───────────────────────────────────────────── วันทำการ
def test_previous_trading_day_weekend_and_long_holiday():
    assert previous_trading_day(date(2026, 11, 30)) == date(2026, 11, 27)     # จันทร์ → ศุกร์
    assert previous_trading_day(date(2026, 11, 27)) == date(2026, 11, 26)
    assert previous_trading_day(date(2026, 12, 8)) == date(2026, 12, 4)       # หลัง เสาร์–อาทิตย์ + ชดเชย 7 ธ.ค.
    assert previous_trading_day(date(2026, 4, 16)) == date(2026, 4, 10)       # หลังสงกรานต์ 13–15 เม.ย.
    assert previous_trading_day(date(2026, 10, 19)) == date(2026, 10, 15)     # หลังวันหยุดพิเศษ 16 ต.ค. (SPECIAL)


# ───────────────────────────────────────────── ผู้นำ
def leaders_of(m, stage=None, rs=None, pp=None):
    stage = stage or {t: "S.Bull" for t in m}
    rs = rs or {t: 90 for t in m}
    return B.build_leaders(m, stage, rs, T2S, pp or {})


def test_leader_tags_rest_below_ema10_up_none():
    out = leaders_of({
        "E0": metrics(chg=-1.5, close=10, ema10=9.8),     # ลง แต่ยังเหนือ EMA10 = rest
        "E1": metrics(chg=-1.5, close=10, ema10=10.2),    # ปิดใต้ EMA10 = below_ema10 (ยังอยู่ในตาราง)
        "E2": metrics(chg=3.0),                           # ≥ +3% = up
        "E3": metrics(chg=0.5),                           # ไม่เข้าเงื่อนไข = None
        "E4": metrics(chg=4.0, close=10, ema10=10.5),     # ขึ้นแรงแต่ใต้ EMA10 → below_ema10 มาก่อน
    })
    tags = {r["t"]: r["tag"] for r in out["rows"]}
    assert tags == {"E0": "rest", "E1": "below_ema10", "E2": "up", "E3": None, "E4": "below_ema10"}
    assert out["total"] == 5


def test_leader_filters_price_adtv_stage_rs_high_fund():
    m = {
        "E0": metrics(),                                  # ผ่าน
        "E1": metrics(close=1.9, ema10=1.8, hi52=2.0),    # ราคา < 2
        "E2": metrics(adtv50=9.99),                       # ADTV50 < 10 ลบ.
        "E3": metrics(close=7.4, ema10=7.0, hi52=10.0),   # ห่าง high 26%
        "E4": metrics(),                                  # Stage Bear
        "E5": metrics(),                                  # RS 79
        "FUND1": metrics(),                               # กองทุน/REIT
    }
    stage = {t: "Bull" for t in m} | {"E4": "Bear"}
    rs = {t: 95 for t in m} | {"E5": 79}
    out = leaders_of(m, stage, rs)
    assert [r["t"] for r in out["rows"]] == ["E0"]
    assert out["criteria"]["adtv50_min_mb"] == 10.0


def test_leader_sort_rs_then_near_high():
    m = {"E0": metrics(close=9.0, hi52=10.0), "E1": metrics(close=9.9, hi52=10.0), "E2": metrics()}
    out = leaders_of(m, rs={"E0": 95, "E1": 95, "E2": 99})
    assert [r["t"] for r in out["rows"]] == ["E2", "E1", "E0"]


# ───────────────────────────────────────────── กลุ่ม
def group_metrics(chgs, prefix, **kw):
    return {f"{prefix}{i}": metrics(chg=c, **kw) for i, c in enumerate(chgs)}


def test_groups_strength_weakness_skipped_and_mai_name():
    m = {**group_metrics([3, 2.5, 2, 1.8, -0.5, 4], "E"),          # med 2.25 − SET 0.5 = +1.75, ขึ้น 5/6
         **group_metrics([-1, -1.2, -0.8, -2, 0.1], "B"),           # med −1.0 − 0.5 = −1.5, ลง 4/5
         **group_metrics([0.5, 0.4, 0.6, 0.5, 0.3], "M"),           # mai · Technology: ไม่ผ่าน
         **group_metrics([1, 1, 1, 1], "S")}                        # Commerce เหลือ 4 → skipped
    g = B.build_groups(m, T2S, set_chg=0.5, set_perf5=1.0, leader_set={"E5"})
    st = {r["g"]: r["status"] for r in g["rows"]}
    assert st == {"Electronic Components": "strength", "Banking": "weakness", "mai · Technology": None}
    assert g["counts"] == {"strength": 1, "weakness": 1, "ranked": 3}
    assert g["skipped"] == [{"g": "Commerce", "market": "SET", "n": 4}]
    ec = next(r for r in g["rows"] if r["g"] == "Electronic Components")
    assert ec["med_vs_set"] == 1.75 and ec["up"] == 5 and ec["down"] == 1 and ec["med5_vs_set"] == 1.0
    assert ec["top4"][0] == {"t": "E5", "chg": 4.0, "leader": True}
    assert ec["bot4"][0]["t"] == "E4"
    assert len(ec["bot4"]) == 2 and not {c["t"] for c in ec["bot4"]} & {c["t"] for c in ec["top4"]}  # 6 ตัว: ไม่ซ้ำ
    assert next(r for r in g["rows"] if r["g"] == "mai · Technology")["market"] == "mai"


def test_groups_zero_weakness_not_padded():
    m = {**group_metrics([1, 1, 1, 1, 1], "B"), **group_metrics([1.2, 1.1, 0.9, 1, 1], "E")}
    g = B.build_groups(m, T2S, set_chg=1.0, set_perf5=0.0, leader_set=set())
    assert g["counts"]["strength"] == 0 and g["counts"]["weakness"] == 0
    assert all(r["status"] is None for r in g["rows"])


def test_group_member_filter_val20_and_volume():
    m = group_metrics([2, 2, 2, 2, 2], "E")
    m["E0"]["val20"] = 1.99                                        # มูลค่า 20 วัน < 2 ลบ.
    m["E1"]["vol"] = 0                                             # วันนี้ไม่มีซื้อขาย
    g = B.build_groups(m, T2S, 0.0, 0.0, set())
    assert g["rows"] == [] and g["skipped"] == [{"g": "Electronic Components", "market": "SET", "n": 3}]


def test_near_groups_report_missing_condition():
    m = {**group_metrics([2, 2, 2, -1, -1, 2], "E"),               # vs +2 ผ่าน แต่ขึ้น 4/6 = 0.667 ≥ 0.6 → strength
         **group_metrics([1.6, 1.6, 1.6, -1, -1], "B")}            # vs +1.6−... ขึ้น 3/5 = 0.6 → strength
    g = B.build_groups(m, T2S, 0.0, 0.0, set())
    assert g["counts"]["strength"] == 2
    m2 = group_metrics([2, 2, 2, -1, -1, -1], "E")                 # ค่ากลาง 0.5 → vs 0.5 ไม่ผ่าน, ขึ้น 50%
    m2 |= group_metrics([1.7, 1.7, 1.7, -1, -1], "B")              # vs 1.7 ผ่าน, ขึ้น 60% → strength
    m2 |= group_metrics([0.7, 0.7, 0.7, 0.7, 0.7], "M")            # ขึ้น 100% ผ่าน, vs 0.7 ขาด 0.3 → near
    g2 = B.build_groups(m2, T2S, 0.0, 0.0, set())
    near = [n for n in g2["near"] if n["side"] == "strength"]
    assert near and near[0]["g"] == "mai · Technology" and near[0]["missing"] == "med_vs_set"
    assert len(near) <= C.GROUP["near_max"]


# ───────────────────────────────────────────── ไฟสัญญาณ
def light_with(stage, ma50):
    set_df = read_set_index(FIX_SET)
    breadth = {"market_stage": {"stage": stage, "dd_count_25d": 8},
               "breadth": [{"date": "2026-11-27", "pct_above_ma50": ma50, "pct_above_ma200": 40.8}]}
    w = []
    return B.build_light(set_df, AS_OF, breadth, w), w


def test_light_red_on_2026_11_27():
    lt, w = light_with("Correction", 18.03)
    assert lt["color"] == "red" and lt["n_pass"] == 0 and w == []
    ph = lt["checks"][1]["value"]
    assert ph["phase"] == "A"
    assert lt["sentence"] == "ดัชนี +1.83% แต่หุ้นส่วนใหญ่ยังอยู่ในขาลง — หุ้นเหนือ SMA50 มีแค่ 18%"
    assert lt["action"] == C.ACTION["red"]


def test_light_yellow_when_some_checks_pass():
    lt, _ = light_with("Uptrend", 45.0)                            # (a) ผ่าน · (c) ผ่านจาก SMA50 ≥ 40 · (b) ช่วง A
    assert lt["color"] == "yellow" and lt["n_pass"] == 2
    assert "ยังขาด: ดัชนียังไม่อยู่ช่วง C (A)" in lt["sentence"]


def test_light_warns_when_breadth_date_differs():
    set_df = read_set_index(FIX_SET)
    w = []
    B.build_light(set_df, AS_OF, {"market_stage": {"stage": "Correction"},
                                  "breadth": [{"date": "2026-11-26", "pct_above_ma50": 20}]}, w)
    assert w and "breadth.json" in w[0]


def test_every_sentence_template_formats():
    v = {"set_chg": "+1.00%", "ma50": "40", "ma200": "50", "stage": "Uptrend", "stage_th": "ขาขึ้น", "dd": 2,
         "phase": "C", "phase_th": "C", "n_pass": 2, "missing": "x", "last_ftd_th": "1 ม.ค.", "idx_dd": "-1.0",
         "rally": "+5.0", "ftd_days": 25, "ma50_min": "40"}
    for (color, _), t in C.SENTENCE.items():
        assert color in C.ACTION and t.format(**v)
    for t in list(C.MISSING.values()) + list(C.HINT.values()):
        assert t.format(**v)


# ───────────────────────────────────────────── ทั้งไฟล์ (ข้อมูลจำลองใน tmp)
def test_build_end_to_end(tmp_path):
    out = run_build(tmp_path)
    assert out["as_of"] == "2026-11-27" and out["prev_trading_day"] == "2026-11-26"
    assert out["errors"] == []
    assert out["light"]["color"] == "red"
    L = out["leaders"]
    assert [r["t"] for r in L["rows"]] == ["E0", "E1", "LEAD"]          # RS เท่ากัน ห่าง high เท่ากัน → ตามชื่อ
    pp = {r["t"]: r["pp"] for r in L["rows"]}
    assert pp["LEAD"] == {"date": "2026-11-23", "kind": "listing", "price": 5.0, "discount_pct": -50.0}
    assert pp["E0"] is None and pp["E1"] is None                   # E0 เกิน 20 วันทำการ · E1 เป็น RO
    assert "FUND1" not in pp
    assert out["scanner_delta"] == {"sepa": [2, 1], "kell": [1, -1], "breakout": [0, None],
                                    "dual": [1, 1], "stage2": [4, 3]}
    assert out["groups"]["skipped"] and out["volume"]["source"] == "universe"


def test_build_without_capital_events_or_prev_history(tmp_path):
    out = run_build(tmp_path, with_prev=False, with_events=False)
    assert out["errors"] == []
    assert all(r["pp"] is None for r in out["leaders"]["rows"])
    assert any("capital_events" in w for w in out["warnings"])
    assert all(v[1] is None for v in out["scanner_delta"].values())
    assert out["scanner_delta"]["sepa"][0] == 2


def test_section_failure_is_null_not_crash(tmp_path):
    hist, today, sd = make_env(tmp_path)
    (today / "breadth.json").unlink()
    (today / "rs_ranking.json").write_text("{broken")
    out = B.build(hist, hist / "SET_INDEX.csv", today, sd, sector_map=sd / "data" / "scans" / "sector_map.json",
                  log=lambda *_: None)
    assert out["light"] is None and out["leaders"] is None
    assert out["groups"] is not None and out["scanner_delta"] is not None and out["volume"] is not None
    assert any(e.startswith("light:") for e in out["errors"]) and any(e.startswith("leaders:") for e in out["errors"])


def test_main_writes_valid_json_atomically(tmp_path):
    hist, today, sd = make_env(tmp_path)
    dest = tmp_path / "out" / "overview.json"
    rc = B.main(["--history-dir", str(hist), "--today-dir", str(today), "--stockdesk-dir", str(sd),
                 "--sector-map", str(sd / "data" / "scans" / "sector_map.json"), "--out", str(dest)])
    assert rc == 0
    d = json.loads(dest.read_text(encoding="utf-8"))
    assert d["generated_at"] and d["rule_version"] == "v1"
    assert not (dest.parent / "overview.json.tmp").exists()
    for k in ("light", "volume", "groups", "leaders", "scanner_delta", "errors", "warnings"):
        assert k in d


def test_main_missing_set_index_still_writes_file(tmp_path):
    dest = tmp_path / "overview.json"
    rc = B.main(["--history-dir", str(tmp_path), "--today-dir", str(tmp_path), "--stockdesk-dir", str(tmp_path),
                 "--out", str(dest)])
    d = json.loads(dest.read_text(encoding="utf-8"))
    assert rc == 0 and d["generated_at"] and d["light"] is None and d["errors"]


def test_as_of_point_in_time(tmp_path):
    hist, today, sd = make_env(tmp_path)
    out = B.build(hist, hist / "SET_INDEX.csv", today, sd, sector_map=sd / "data" / "scans" / "sector_map.json",
                  as_of="2026-11-23", log=lambda *_: None)
    assert out["as_of"] == "2026-11-23" and out["prev_trading_day"] == "2026-11-20"   # จันทร์ → ศุกร์


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
