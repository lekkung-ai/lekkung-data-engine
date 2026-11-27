"""scan_rs7.py — RS Leaders (Minervini 7 รูปแบบหุ้นผู้นำ) → data/results/output/rs7.json

กฎ: mockups/rs7/rs7-spec.txt (stockdesk repo ฝั่ง dashboard) · เกณฑ์ทุกตัวอยู่ใน rs7_config.RS7_CONFIG
ใช้ของเดิมของระบบ (import ไม่เขียนซ้ำ):
  FTD            calculate_breadth.compute_ftd_dd_detail (≥1.5% วันที่ 4–10 · rally หมดอายุหลังวันที่ 10)
  RS Rating      tools/core/rs_score.py (สูตรเดียวกับ 3_calculate_rs → วันล่าสุดตรง rs_ranking.csv)
  Trend Template scan_sepa.trend_template (ข้อ 1–7) + RS Rating ≥ 70 (ข้อ 8)
  RS line        tools/core/rs_line.py
  สภาพคล่อง      ADTV 50 วัน (utils.calculate_adtv) ≥ config.ADTV_MIN_MB · กองทุน/REIT ไม่สแกน (load_fund_tickers)
  Stage          pine_stages.csv (Stage เดียวกับ combined.json)

รันเป็น step แยกใน daily-scan.yml (continue-on-error) ไม่อยู่ใน run_all.py → ไม่เข้า failed_steps
ส่วนไหนคำนวณพัง ส่วนนั้นว่าง + warnings[] · exit 0 เสมอ ยกเว้นเขียนไฟล์ไม่ได้ (exit 1)
อ่านไฟล์ในเครื่องเท่านั้น ไม่ดึงข้อมูลภายนอก
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE.parents[1]), str(_HERE)]
from config import HISTORY_DIR, PINE_STAGES_FILE, RESULTS_DIR, RS_FILE  # noqa: E402
from tools.core import rs_line as rsl  # noqa: E402
from tools.core.rs_score import load_close_series, rs_raw, rs_ratings  # noqa: E402
from calculate_breadth import compute_ftd_dd_detail  # noqa: E402
from rs7_config import RS7_CONFIG  # noqa: E402
from scan_sepa import trend_template  # noqa: E402
from utils import (SECTOR_MAP_CANDIDATES, calculate_adtv, iter_stock_files,  # noqa: E402
                   load_full_df, load_fund_tickers)

OUT_FILE = RESULTS_DIR / "output" / "rs7.json"
BKK = timezone(timedelta(hours=7))
PATTERNS = {1: "PREDICTIVE", 2: "COINCIDENT", 3: "CONFIRMING", 4: "SORE_THUMB",
            5: "FAST_REBOUND", 6: "LEAD_REST", 7: "FAILURE_RESET"}
PATTERN_TH = {1: "Predictive", 2: "Coincident", 3: "Confirming", 4: "Sore thumbs",
              5: "Fast rebound", 6: "Lead and rest", 7: "Failure reset"}


# ─────────────────────────────────────────────────────────── helpers
def bars_since_min(s: pd.Series, w: int) -> pd.Series:
    return s.rolling(w, min_periods=w // 2).apply(lambda a: len(a) - 1 - int(np.argmin(a)), raw=True)


def bars_since_true(flag) -> np.ndarray:
    out = np.full(len(flag), np.inf)
    last = -1
    for i, f in enumerate(np.asarray(flag, dtype=bool)):
        if f:
            last = i
        if last >= 0:
            out[i] = i - last
    return out


def tight(h: pd.Series, l: pd.Series, n: int) -> pd.Series:
    """ความกว้างฐาน n วัน = High สูงสุด / Low ต่ำสุด − 1"""
    return h.rolling(n).max() / l.rolling(n).min() - 1


def _r(x, nd=4):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(x) else round(x, nd)


def _d(ts) -> str | None:
    return None if ts is None or pd.isna(ts) else pd.Timestamp(ts).strftime("%Y-%m-%d")


# ─────────────────────────────────────────────────────────── loading
def read_set_index(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = df.rename(columns={df.columns[0]: "Date"})
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date", "Close"]).set_index("Date").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df[["Open", "High", "Low", "Close", "Volume"]].apply(pd.to_numeric, errors="coerce")


def read_stock(file_path: Path) -> tuple[pd.DataFrame | None, pd.Series | None]:
    """(OHLCV ตาม prepare_stock_data: dropna Close/High/Low/Volume, index = วันที่) และ Close ตาม rs_score
    (dropna Close อย่างเดียว เหมือน 3_calculate_rs) — แยกกันเพื่อให้ RS ตรงของเดิมทุกตัว"""
    full = load_full_df(file_path)
    dates = pd.to_datetime(full.iloc[:, 0], errors="coerce")
    close_rs = load_close_series(file_path)
    close_rs.index = dates.reindex(close_rs.index).values
    df = full.copy()
    for col in ("Open", "Close", "High", "Low", "Volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if not all(c in df.columns for c in ("Close", "High", "Low", "Volume")):
        return None, close_rs
    df.dropna(subset=["Close", "High", "Low", "Volume"], inplace=True)
    df.index = dates.reindex(df.index).values
    df = df[~pd.isna(df.index)]
    return df, close_rs


def load_sector_map() -> dict:
    path = next((p for p in SECTOR_MAP_CANDIDATES if p.exists()), None)
    if path is None:
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("ticker_to_sector", {})


# ─────────────────────────────────────────────────────────── index phase
def index_features(set_df: pd.DataFrame, cfg: dict = RS7_CONFIG) -> pd.DataFrame:
    c, v = set_df["Close"], set_df["Volume"]
    f = pd.DataFrame(index=set_df.index)
    f["idx"] = c
    f["idx_hi"] = c.rolling(cfg["idx_high_win"], min_periods=120).max()
    f["idx_dd"] = c / f["idx_hi"] - 1
    f["idx_low"] = c.rolling(cfg["idx_low_win"], min_periods=60).min()
    f["bsl"] = bars_since_min(c, cfg["idx_low_win"])
    f["rally"] = c / f["idx_low"] - 1
    sma50 = c.rolling(50).mean()
    f["idx_sma50"] = sma50
    marker, rally_day = compute_ftd_dd_detail(set_df)
    f["ftd"] = [m == "FTD" for m in marker]
    f["rally_day"] = rally_day
    f["bars_since_ftd"] = bars_since_true(f["ftd"])
    f["corr_recent"] = f["idx_dd"].rolling(cfg["corr_recent_win"], min_periods=20).min() <= -cfg["corr_th"]
    phase_a = (f["idx_dd"] <= -cfg["corr_th"]) & (c <= f["idx_low"] * (1 + cfg["near_low"]))
    b0, b1 = cfg["phase_b_bsl"]
    phase_b = (f["corr_recent"] & f["bsl"].between(b0, b1)
               & (f["bars_since_ftd"] <= cfg["phase_b_ftd_within"]) & (f["bars_since_ftd"] < f["bsl"])
               & (f["idx_dd"] <= -cfg["b_max_near_high"]))
    phase_c = (f["corr_recent"] & (c > sma50) & (sma50 > sma50.shift(cfg["phase_c_sma50_rising_bars"]))
               & ((f["rally"] >= cfg["phase_c_rally"]) | (f["bsl"] > cfg["phase_c_bsl_gt"])))
    # ลำดับตามสเปก A > B > C · B ต้องยังห่าง high (b_max_near_high) → ดัชนีกลับไปที่ high แล้วไม่ค้างเป็น B
    f["phase"] = np.where(phase_a, "A", np.where(phase_b, "B", np.where(phase_c, "C", "-")))
    f["idx_weak"] = (c < sma50) & (c.pct_change(63) <= cfg["sore_idx_ret63"])
    return f


def phase_summary(set_df: pd.DataFrame, f: pd.DataFrame, cfg: dict = RS7_CONFIG) -> dict:
    d = f.index[-1]
    row = f.iloc[-1]
    c = set_df["Close"]
    hi_win = c.iloc[-cfg["idx_high_win"]:]
    lo_win = c.iloc[-cfg["idx_low_win"]:]
    ph = f["phase"].to_numpy()
    k = len(ph) - 1
    while k > 0 and ph[k - 1] == ph[-1]:
        k -= 1
    ftd_idx = np.where(f["ftd"].to_numpy())[0]
    last_ftd = None
    if len(ftd_idx):
        j = int(ftd_idx[-1])
        last_ftd = {"date": _d(f.index[j]), "rally_day": int(f["rally_day"].iloc[j]),
                    "bars_ago": len(f) - 1 - j, "pct": _r(c.iloc[j] / c.iloc[j - 1] - 1)}
    return {
        "today": {"A": "A", "B": "B", "C": "C"}.get(row["phase"], "neutral"),
        "since": _d(f.index[k]),
        "date": _d(d),
        "idx_close": _r(row["idx"], 2),
        "idx_dd": _r(row["idx_dd"]),
        "idx_high": _r(hi_win.max(), 2), "idx_high_date": _d(hi_win.idxmax()),
        "idx_low": _r(lo_win.min(), 2), "idx_low_date": _d(lo_win.idxmin()),
        "above_low": _r(row["rally"]),
        "bars_since_low": None if pd.isna(row["bsl"]) else int(row["bsl"]),
        "corr_recent": bool(row["corr_recent"]),
        "idx_above_sma50": bool(row["idx"] > row["idx_sma50"]),
        "rally_day_today": int(row["rally_day"]),
        "last_ftd": last_ftd,
    }


# ─────────────────────────────────────────────────────────── RS rating history
def rs_rating_history(closes: dict[str, pd.Series], funds: set, dates) -> pd.DataFrame:
    """RS Rating ของทุกวันใน dates — rs_raw + rs_ratings ตัวเดียวกับ 3_calculate_rs บน series ที่ตัดถึงวันนั้น"""
    tickers = list(closes)
    is_fund = pd.Series([t.upper() in funds for t in tickers], index=tickers)
    out = {}
    cut = {t: s.index.to_numpy() for t, s in closes.items()}
    for d in dates:
        raw = {}
        d64 = np.datetime64(d)
        for t in tickers:
            n = int(np.searchsorted(cut[t], d64, side="right"))
            if n:
                r = rs_raw(closes[t].iloc[:n])
                if r is not None:
                    raw[t] = r
        if raw:
            s = pd.Series(raw)
            out[d] = rs_ratings(s, is_fund.reindex(s.index))
    return pd.DataFrame(out).T.reindex(dates)


# ─────────────────────────────────────────────────────────── per stock
def stock_frame(df: pd.DataFrame, f: pd.DataFrame, rsr: pd.Series, cfg: dict = RS7_CONFIG,
                eval_from: int = 0) -> pd.DataFrame:
    """ตัวแปร + สถานะ 7 รูปแบบรายแท่ง · eval_from = แท่งแรกที่ต้องตัดสินรูปแบบ (ก่อนหน้านั้นว่าง)"""
    f = f.reindex(df.index.union(f.index)).sort_index().ffill().reindex(df.index)
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    idx = f["idx"]
    n = len(df)
    o = pd.DataFrame(index=df.index)
    sma20, sma50, sma150, sma200 = (c.rolling(k).mean() for k in (20, 50, 150, 200))
    h252 = h.rolling(252, min_periods=120).max()
    dist = c / h252 - 1
    rs = rsl.rs_line(c, idx)
    rs_hi = rsl.rs_high(rs, cfg["rs_win"])
    rs_nh = rsl.rs_nh(rs, cfg["rs_win"])
    rs_near = rsl.rs_near(rs, cfg["rs_near"], cfg["rs_win"])
    rs_gap = rsl.rs_gap(rs, cfg["rs_win"])
    t10, t15, t20, t30 = (tight(h, l, k) for k in (10, 15, 20, 30))
    pivot = h.shift(1).rolling(cfg["pivot_win"]).max()
    vavg = v.rolling(cfg["vol_avg_win"]).mean()
    volx = v / vavg
    bo = (c > pivot) & (volx >= cfg["vol_mult"])
    ants = (c > c.shift(1)).rolling(cfg["ants_win"]).sum()
    rel20 = c.pct_change(20) - idx.pct_change(20)
    ret63, iret63 = c.pct_change(63), idx.pct_change(63)
    stage2 = (c > sma50) & (sma50 > sma200)
    rr = pd.to_numeric(rsr.reindex(df.index), errors="coerce").astype(float)   # Int64 <NA> → NaN
    adtv_mb = (c * v).rolling(50).mean() / 1_000_000          # = utils.calculate_adtv(window=50) ณ แท่งนั้น
    liquid = (adtv_mb >= cfg["min_adtv_mb"]) & (c >= cfg["min_price"])
    enough = pd.Series(np.arange(n) >= cfg["min_bars"] - 1, index=df.index)
    active = enough & liquid & pd.Series(np.arange(n) >= eval_from, index=df.index)
    sbsl = bars_since_min(c, cfg["idx_low_win"])
    phase = f["phase"].fillna("-")

    o["close"], o["dist_hi"], o["rs"], o["rs_nh"], o["rs_near"], o["rs_gap"] = c, dist, rs, rs_nh, rs_near, rs_gap
    o["tight10"], o["ants15"], o["pivot"], o["volx"], o["bo"], o["adtv_mb"] = t10, ants, pivot, volx, bo, adtv_mb
    o["rs_rating"], o["liquid"] = rr, liquid
    st = {}

    # 1 Predictive
    pred = ((phase == "A") & stage2 & (dist >= -cfg["pred_dist"]) & rs_near
            & (rel20 >= cfg["pred_rel20"]) & (t10.shift(1) <= cfg["pred_tight10"]))
    st[1] = np.where(pred & bo, "BREAKOUT", np.where(pred, "SETUP", ""))
    # 2 Coincident
    coin = ((phase == "B") & bo & (dist >= -cfg["coin_dist"]) & rs_near
            & (sbsl >= f["bsl"] - cfg["coin_low_lag"]))
    st[2] = np.where(coin, "BREAKOUT", "")
    # 3 Confirming — Trend Template (scan_sepa.trend_template + RS ≥ 70) เฉพาะแท่งที่ผ่านเงื่อนไขอื่นแล้ว
    base_ok = (t30.shift(1) <= cfg["conf_tight30"]) & (t10.shift(1) <= cfg["conf_tight10"])
    pre = (phase == "C") & base_ok & rs_near & active & (rr >= cfg["rs_rating_min"])
    tt_ok = pd.Series(False, index=df.index)
    for i in np.where(pre.to_numpy())[0]:
        if i + 1 >= 252:
            tt_ok.iloc[i] = all(trend_template(df.iloc[:i + 1])["conds"])
    core = pre & tt_ok
    setup = core & ~bo & (c >= pivot * (1 - cfg["conf_setup_to_pivot"]))
    st[3] = np.where(core & bo, "BREAKOUT", np.where(setup, "SETUP", ""))
    # 4 Sore thumbs
    sore = (f["idx_weak"].fillna(False).astype(bool) & (c > sma50) & (sma50 > sma150) & (sma150 > sma200)
            & (sma50 > sma50.shift(cfg["sore_sma50_rising_bars"])) & (ret63 >= cfg["sore_ret63"])
            & ((ret63 - iret63) >= cfg["sore_spread63"]) & (dist >= -cfg["sore_dist"])
            & (rs_nh.astype(float).rolling(cfg["sore_rsnh_within"]).max() == 1))
    st[4] = np.where(sore, "WATCH", "")
    # 6 Lead and rest
    bsl_i = f["bsl"].fillna(0).astype(int).to_numpy()
    pos = np.arange(n) - bsl_i
    rs_a, rshi_a = rs.to_numpy(), rs_hi.to_numpy()
    pc = np.clip(pos, 0, None)
    at_low = np.where(pos >= 0, rs_a[pc] >= cfg["rest_rs_at_low"] * rshi_a[pc], False)
    r0, r1 = cfg["rest_bsl"]
    g0, g1 = cfg["rest_rsgap"]
    rest = (f["corr_recent"].fillna(False).astype(bool) & (f["rally"] >= cfg["rest_rally"])
            & f["bsl"].between(r0, r1) & pd.Series(at_low, index=df.index) & (t20 <= cfg["rest_tight20"])
            & (dist >= -cfg["rest_dist"]) & (c > sma50) & rs_gap.between(g0, g1))
    st[6] = np.where(rest & bo, "BREAKOUT", np.where(rest, "REST", ""))

    # 5 Fast rebound + 7 Failure reset (look-back แปรผัน)
    fast_st = np.full(n, "", dtype=object)
    fail_st = np.full(n, "", dtype=object)
    info7 = [None] * n
    C, H, L, V = c.to_numpy(), h.to_numpy(), l.to_numpy(), v.to_numpy()
    H252, VAVG, BO, PIV, VOLX = h252.to_numpy(), vavg.to_numpy(), bo.to_numpy(), pivot.to_numpy(), volx.to_numpy()
    BSL, RALLY, SMA20, SMA50 = f["bsl"].to_numpy(), f["rally"].to_numpy(), sma20.to_numpy(), sma50.to_numpy()
    T10, T15, RS_ = t10.to_numpy(), t15.to_numpy(), rs.to_numpy()
    RS20 = rs.shift(cfg["rs_up_bars"]).to_numpy()
    DIST, RR, ACT = dist.to_numpy(), rr.to_numpy(), active.to_numpy()
    fb0, fb1 = cfg["fast_bsl"]
    pa0, pa1 = cfg["piv_bo_age"]
    bw = cfg["piv_base_win"]
    for i in (int(x) for x in np.where(ACT)[0]):
        b = BSL[i]
        if not np.isnan(b) and fb0 <= b <= fb1:
            j0 = max(0, i - int(b) - cfg["fast_low_pad"])
            sl = j0 + int(np.nanargmin(L[j0:i + 1]))
            s_rally = C[i] / L[sl] - 1
            reach = np.where(C[sl:i + 1] >= (1 - cfg["fast_reach_pct"]) * H252[sl:i + 1])[0]
            if (s_rally >= max(cfg["fast_min"], cfg["fast_mult"] * RALLY[i]) and len(reach)
                    and reach[0] <= cfg["fast_days"]):
                pb = C[i] / np.nanmax(H[sl:i + 1]) - 1
                if cfg["fast_pb"][0] <= pb <= cfg["fast_pb"][1] and V[i] < VAVG[i]:
                    fast_st[i] = "FIRST_PULLBACK"
                elif C[i] > SMA20[i] * (1 + cfg["fast_ext_sma20"]):
                    fast_st[i] = "EXTENDED"
                else:
                    fast_st[i] = "RUNNING"
        lo = max(bw, i - cfg["base_bo_lookback"])
        hi = i - pa0
        if hi < lo:
            continue
        bo_idx = np.where(BO[lo:hi + 1])[0]
        if not len(bo_idx):
            continue
        k = lo + int(bo_idx[-1])
        age = i - k
        piv = PIV[k]
        base_low = float(np.nanmin(L[k - bw:k]))
        post_low = float(np.nanmin(L[k:i + 1]))
        rs_up = RS_[i] > RS20[i]
        common = {"bo_bar": k, "bo_age": age, "bo_volx": _r(VOLX[k], 2), "old_pivot": _r(piv, 3),
                  "base_low": _r(base_low, 3)}
        if post_low >= base_low:
            squat = np.nanmin(C[k:i + 1]) <= piv * (1 - cfg["piv_squat"])
            if (age <= pa1 and squat and T10[i] <= cfg["piv_tight10"] and C[i] >= cfg["piv_floor"] * piv
                    and C[i] > SMA50[i] and rs_up
                    and RR[i] >= cfg["piv_min_rs_rating"] and DIST[i] >= -cfg["piv_max_dist"]):
                reclaim = C[i] > piv and V[i] >= cfg["vol_mult"] * VAVG[i]
                fail_st[i] = "PIVOT_RECLAIM" if reclaim else "PIVOT_RESET"
                info7[i] = {"type": "pivot", **common,
                            "new_low": _r(np.nanmin(L[i - 9:i + 1]), 3)}
        else:
            m = k + int(np.nanargmin(L[k:i + 1]))
            if (i - m >= cfg["base_min_days"] and T15[i] <= cfg["base_tight15"] and rs_up
                    and RR[i] >= cfg["base_min_rs_rating"] and DIST[i] >= -cfg["base_max_dist"]):
                nb_hi = float(np.nanmax(H[i - cfg["base_win"]:i]))
                brk = C[i] > nb_hi and V[i] >= cfg["vol_mult"] * VAVG[i]
                fail_st[i] = "BASE_BREAKOUT" if brk else "BASE_RESET"
                info7[i] = {"type": "base", **common, "broken_low": _r(L[m], 3),
                            "broken_low_bars_ago": i - m, "new_base_high": _r(nb_hi, 3),
                            "new_base_low": _r(np.nanmin(L[i - cfg["base_win"] + 1:i + 1]), 3)}
    st[5], st[7] = fast_st, fail_st
    o["info7"] = info7
    for pid in PATTERNS:
        o[f"p{pid}"] = np.where(active.to_numpy(), st[pid], "")
    return o


# ─────────────────────────────────────────────────────────── output builders
def stock_record(t: str, df: pd.DataFrame, o: pd.DataFrame, cfg: dict, sector: str | None,
                 stage: str | None) -> dict:
    last = o.iloc[-1]
    i = len(o) - 1
    pats = [{"id": pid, "key": PATTERNS[pid], "status": last[f"p{pid}"]} for pid in PATTERNS if last[f"p{pid}"]]
    tt = trend_template(df)
    rr = last["rs_rating"]
    tt_pass = sum(bool(x) for x in tt["conds"]) + (1 if pd.notna(rr) and rr >= cfg["rs_rating_min"] else 0)
    bo_flags = o["bo"].to_numpy()
    lb = np.where(bo_flags[max(0, i - cfg["base_bo_lookback"]):i + 1])[0]
    last_bo = None
    if len(lb):
        k = max(0, i - cfg["base_bo_lookback"]) + int(lb[-1])
        last_bo = {"date": _d(o.index[k]), "bars_ago": i - k, "volx": _r(o["volx"].iloc[k], 2),
                   "pivot": _r(o["pivot"].iloc[k], 3)}
    reset = None
    if isinstance(last["info7"], dict):
        reset = {kk: vv for kk, vv in last["info7"].items() if kk != "bo_bar"}
        reset["bo_date"] = _d(o.index[last["info7"]["bo_bar"]])
    piv = last["pivot"]
    return {
        "ticker": t, "sector": sector, "stage": stage,
        "patterns": pats,
        "close": _r(last["close"], 3),
        "rs_rating": None if pd.isna(rr) else int(rr),
        "dist_hi": _r(last["dist_hi"]), "rs_gap": _r(last["rs_gap"]), "rs_nh": bool(last["rs_nh"]),
        "tight10": _r(last["tight10"]), "ants15": None if pd.isna(last["ants15"]) else int(last["ants15"]),
        "tt": {"pass": int(tt_pass), "total": 8},
        "pivot": _r(piv, 3), "to_pivot": _r(last["close"] / piv - 1) if piv else None,
        "volx": _r(last["volx"], 2), "adtv_mb": _r(last["adtv_mb"], 1),
        "base_low10": _r(df["Low"].iloc[-10:].min(), 3),
        "last_bo": last_bo, "reset": reset,
    }


def build(history_dir: Path, set_index_file: Path, rs_file: Path, stages_file: Path,
          cfg: dict = RS7_CONFIG, log=print) -> dict:
    t0 = time.time()
    warnings: list[str] = []
    out = {"generated_at": datetime.now(BKK).isoformat(timespec="seconds"), "as_of": None,
           "config": cfg, "patterns": {str(k): {"key": v, "name": PATTERN_TH[k]} for k, v in PATTERNS.items()},
           "phase": {}, "stocks": [], "series": [],
           "rs_line_fields": ["rs_nh", "rs_near", "rs_gap"], "rs_line": {}, "warnings": warnings}

    try:
        set_df = read_set_index(set_index_file)
    except Exception as e:  # noqa: BLE001
        warnings.append(f"SET_INDEX.csv อ่านไม่ได้ ({type(e).__name__}: {e}) — ไม่มีช่วงดัชนี/รายชื่อ/ซีรีส์")
        return out
    as_of = set_df.index[-1]

    # ── stocks
    funds = load_fund_tickers()
    if not funds:
        warnings.append("sector_map ไม่พบหรือไม่มีกองทุน — ไม่ได้ตัดกองทุน/REIT")
    t2s = load_sector_map()
    stage_map = {}
    try:
        st = pd.read_csv(stages_file, encoding="utf-8-sig")
        stage_map = dict(zip(st["Ticker"].astype(str).str.upper(), st["Stage"]))
    except Exception as e:  # noqa: BLE001
        warnings.append(f"stage: อ่าน {Path(stages_file).name} ไม่ได้ ({type(e).__name__})")

    frames, closes, errors = {}, {}, []
    for fp in iter_stock_files(history_dir):
        t = fp.stem
        try:
            df, cl = read_stock(fp)
            closes[t] = cl.dropna()[~pd.isna(cl.dropna().index)] if cl is not None else None
            if t.upper() in funds or df is None or len(df) < cfg["min_bars"]:
                continue
            frames[t] = df
        except Exception as e:  # noqa: BLE001
            errors.append(f"{t}:{type(e).__name__}")
    closes = {t: s for t, s in closes.items() if s is not None and len(s)}
    last_dates = pd.Series({t: d.index[-1] for t, d in frames.items()})
    if len(last_dates):
        stock_as_of = last_dates.mode().iloc[0]
        if stock_as_of != as_of:
            warnings.append(f"SET_INDEX.csv วันล่าสุด {_d(as_of)} ≠ ข้อมูลหุ้น {_d(stock_as_of)}")
            as_of = min(as_of, stock_as_of)
    out["as_of"] = _d(as_of)

    # ── SET index + phase (ตัดถึง as_of — ไม่ใช้แท่งดัชนีที่หุ้นยังไม่มี)
    try:
        set_df = set_df[set_df.index <= as_of]
        f = index_features(set_df, cfg)
        out["phase"] = phase_summary(set_df, f, cfg)
    except Exception as e:  # noqa: BLE001
        warnings.append(f"phase: คำนวณไม่ได้ ({type(e).__name__}: {e}) — ไม่มีรายชื่อ/ซีรีส์")
        return out

    series_dates = [d for d in set_df.index if d <= as_of][-cfg["series_days"]:]
    # RS Rating ย้อนหลัง (สูตรเดียวกับ 3_calculate_rs) + เทียบวันล่าสุดกับ rs_ranking.csv
    try:
        rsr = rs_rating_history(closes, funds, series_dates)
        if Path(rs_file).exists():
            ref = pd.read_csv(rs_file)
            ref = dict(zip(ref["Ticker"].astype(str), ref["RS_Rating"]))
            mine = rsr.iloc[-1]
            bad = [t for t, r in ref.items() if pd.notna(r) and t in mine.index and mine[t] != r]
            if bad:
                warnings.append(f"RS Rating วันล่าสุดไม่ตรง rs_ranking.csv {len(bad)} ตัว: {', '.join(bad[:10])}")
        else:
            warnings.append("ไม่พบ rs_ranking.csv — ไม่ได้เทียบ RS Rating")
    except Exception as e:  # noqa: BLE001
        warnings.append(f"RS Rating ย้อนหลังคำนวณไม่ได้ ({type(e).__name__}: {e}) — Confirming/Base reset ไม่ผ่าน")
        rsr = pd.DataFrame(index=series_dates)

    first_eval = series_dates[0] if series_dates else as_of
    results = {}
    for t, df in frames.items():
        try:
            df = df[df.index <= as_of]
            eval_from = int(np.searchsorted(df.index.to_numpy(), np.datetime64(first_eval)))
            rr = rsr[t] if t in rsr.columns else pd.Series(dtype=float)
            results[t] = (df, stock_frame(df, f, rr, cfg, eval_from=eval_from))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{t}:{type(e).__name__}:{str(e)[:60]}")
    if errors:
        warnings.append(f"คำนวณไม่ได้ {len(errors)} ตัว: {', '.join(errors[:10])}")

    # ── stocks today + rs_line
    try:
        for t, (df, o) in results.items():
            if o.index[-1] != as_of:
                continue
            last = o.iloc[-1]
            if pd.notna(last["rs_gap"]):
                out["rs_line"][t] = [int(bool(last["rs_nh"])), int(bool(last["rs_near"])), _r(last["rs_gap"])]
            if any(last[f"p{pid}"] for pid in PATTERNS):
                out["stocks"].append(stock_record(t, df, o, cfg, (t2s.get(t) or {}).get("sector"),
                                                  stage_map.get(t.upper())))
        out["stocks"].sort(key=lambda r: (min(p["id"] for p in r["patterns"]), -(r["rs_rating"] or 0)))
    except Exception as e:  # noqa: BLE001
        warnings.append(f"stocks: ({type(e).__name__}: {e})")
        out["stocks"] = []

    # ── series
    try:
        cols = {pid: pd.DataFrame({t: o[f"p{pid}"] for t, (_, o) in results.items()}).reindex(series_dates)
                for pid in PATTERNS}
        sec = pd.Series({t: (t2s.get(t) or {}).get("sector") or "?" for t in results})
        for d in series_dates:
            hit = {pid: cols[pid].loc[d].fillna("") != "" for pid in PATTERNS}
            h123 = hit[1] | hit[2] | hit[3]
            hall = h123 | hit[4] | hit[5] | hit[6] | hit[7]
            out["series"].append({
                "date": _d(d), "set_close": _r(set_df.loc[d, "Close"], 2), "phase": f.loc[d, "phase"],
                "n1": int(hit[1].sum()), "n2": int(hit[2].sum()), "n3": int(hit[3].sum()),
                "n_sectors_123": int(sec[h123[h123].index].nunique()), "n_all": int(hall.sum())})
    except Exception as e:  # noqa: BLE001
        warnings.append(f"series: ({type(e).__name__}: {e})")
        out["series"] = []
    log(f"  RS-7: as_of {out['as_of']} phase {out['phase'].get('today')} · scanned {len(results)} · "
        f"hits {len(out['stocks'])} · {time.time() - t0:.0f}s")
    return out


def _json_default(o):
    if isinstance(o, np.generic):
        return o.item()
    raise TypeError(f"not JSON serializable: {type(o).__name__}")


def write_json(out: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"), allow_nan=False, default=_json_default)
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="RS-7 (Minervini 7 leader patterns) → rs7.json")
    ap.add_argument("--history-dir", type=Path, default=HISTORY_DIR)
    ap.add_argument("--set-index", type=Path, default=None, help="default <history-dir>/SET_INDEX.csv")
    ap.add_argument("--rs-file", type=Path, default=RS_FILE)
    ap.add_argument("--stages-file", type=Path, default=PINE_STAGES_FILE)
    ap.add_argument("--out", type=Path, default=OUT_FILE)
    a = ap.parse_args(argv)
    print("📈 [RS-7] กำลังสแกน 7 รูปแบบหุ้นผู้นำ...")
    try:
        out = build(a.history_dir, a.set_index or a.history_dir / "SET_INDEX.csv", a.rs_file, a.stages_file)
    except Exception as e:  # noqa: BLE001 — กันทุกกรณี: ยังเขียนไฟล์ว่าง + warning
        out = {"generated_at": datetime.now(BKK).isoformat(timespec="seconds"), "as_of": None,
               "config": RS7_CONFIG, "phase": {}, "stocks": [], "series": [], "rs_line": {},
               "warnings": [f"scan_rs7 ล้ม: {type(e).__name__}: {e}"]}
    for w in out["warnings"]:
        print(f"  ⚠️ {w}")
    try:
        write_json(out, a.out)
    except Exception as e:  # noqa: BLE001
        print(f"❌ [RS-7] เขียน {a.out} ไม่ได้: {e}")
        return 1
    print(f"✅ [RS-7] {a.out} ({a.out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
