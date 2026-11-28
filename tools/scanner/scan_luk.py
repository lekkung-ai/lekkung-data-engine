"""scan_luk.py — Martin Luk momentum watchlist (SET, กราฟรายวัน) → data/results/output/luk.json + luk_hits.json

กฎ: mockups/luk/scan_martin_luk.py (4 setup + regime) · เกณฑ์ทุกตัวใน luk_config.LUK_CONFIG
ต่างจากต้นแบบ (ผู้ใช้ตัดสิน 2026-11-28):
  - universe = หุ้นใน pipeline (data/history) ไม่รวมกองทุน/REIT · ไม่ดึง TradingView/Yahoo · ไม่มีตัวกรอง market cap
  - ผู้นำ = Top N ตาม RS Rating ของระบบ (rs_ranking.csv = tools/core/rs_score.py) แทน momentum score ของ Luk
  - regime ใช้ SET จาก SET_INDEX.csv
  - stop ฝั่ง long ขั้นต่ำ 3 tick (vcp.thai_tick) · ฝั่ง short = "หุ้นอ่อนแรง" (side weak) ไม่คิดขนาด position
  - PARABOLIC_SHORT: dist_ref_adr ใช้ ADR ก่อนวิ่งตัวเดียวกับ note
  - ใช้เฉพาะแท่งที่ตรงวันทำการของ SET_INDEX.csv (ตัดแถววันหยุดของ Yahoo ก่อนคิด gap / EMA)
ตัวชี้วัดทั้งหมดจาก tools/core/indicators.py

รันเป็น step แยกใน daily-scan.yml (continue-on-error) ไม่อยู่ใน run_all.py → ไม่เข้า failed_steps
ส่วนไหนพัง ส่วนนั้นว่าง + warnings[] · exit 0 เสมอ ยกเว้นเขียนไฟล์ไม่ได้ (exit 1)
"""
from __future__ import annotations

import argparse
import json
import math
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
from config import HISTORY_DIR, RESULTS_DIR, RS_FILE  # noqa: E402
from tools.core import indicators as ind  # noqa: E402
from luk_config import LUK_CONFIG  # noqa: E402
from scan_rs7 import read_set_index, read_stock  # noqa: E402
from utils import SECTOR_MAP_CANDIDATES, iter_stock_files, load_fund_tickers  # noqa: E402
from vcp import thai_tick  # noqa: E402

OUT_FILE = RESULTS_DIR / "output" / "luk.json"
HITS_FILE = RESULTS_DIR / "output" / "luk_hits.json"
BKK = timezone(timedelta(hours=7))
LONG_SETUPS = ("LONG_PULLBACK", "EPISODIC_PIVOT")
WEAK_SETUPS = ("SHORT_WEAK_BOUNCE", "PARABOLIC_SHORT")
REMINDER = ("Watchlist สำหรับพรุ่งนี้ ไม่ใช่สัญญาณซื้อ · จุดเข้าจริงมาจากกราฟ 1 นาที/5 นาที (ORB, VWAP) · "
            "ฝั่งหุ้นอ่อนแรงเป็นคำเตือน (อย่าซื้อ / ถืออยู่ระวัง) ไม่ใช่สัญญาณชอร์ต · "
            "Stop ไม่ครอบคลุมความเสี่ยงจาก gap และหุ้นธีมเดียวกันหลายตัว")


# ─────────────────────────────────────────────────────────── helpers
def _r(x, nd=2):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return round(x, nd) if math.isfinite(x) else None


def _d(ts) -> str | None:
    return None if ts is None or pd.isna(ts) else pd.Timestamp(ts).strftime("%Y-%m-%d")


def tick_up(price: float) -> float:
    """ราคาบนตาราง tick ที่ ≥ price (ราคาใน pipeline เป็นราคาปรับปันผล อาจไม่อยู่บน tick)"""
    t = thai_tick(price)
    return round(math.ceil(price / t - 1e-9) * t, 4)


def tick_down(price: float) -> float:
    t = thai_tick(price)
    return round(math.floor(price / t + 1e-9) * t, 4)


def min_stop_pct(price: float, pct: float, cfg: dict = LUK_CONFIG) -> float:
    """stop = max(pct, min_stop_ticks tick) เป็น % ของราคา"""
    return max(pct, cfg["min_stop_ticks"] * thai_tick(price) / price * 100)


def long_risk(close: float, high: float, low: float, risk_pct: float, cfg: dict = LUK_CONFIG) -> dict:
    """จุดเข้า (high วันนี้ + 1 tick) · stop intraday = max(1.5%, 3 tick) จากจุดเข้า ปัดลงตาม tick ·
    stop daily = max(ระยะถึง low วันนี้, 3 tick) จากราคาปิด · ขนาด = risk / stop (เพดาน max_position_pct)"""
    t_hi = thai_tick(high)
    entry = round(tick_up(high) + t_hi, 4)
    stop_pct = min_stop_pct(entry, cfg["intraday_stop_pct"], cfg)
    stop_price = tick_down(entry * (1 - stop_pct / 100))
    t_e = thai_tick(entry)
    daily = max((close - low) / close * 100, cfg["daily_stop_floor_pct"])
    daily = min_stop_pct(close, daily, cfg)
    size = lambda s: round(min(risk_pct / s * 100, cfg["max_position_pct"]), 1)  # noqa: E731
    return {
        "entry_trigger": _r(entry, 3),
        "stop_pct": _r(stop_pct), "stop_price": _r(stop_price, 3),
        "stop_ticks": int(round((entry - stop_price) / t_e)),
        "daily_stop_pct": _r(daily),
        "size_pct_daily_stop": size(daily), "size_pct_intraday_stop": size(stop_pct),
    }


# ─────────────────────────────────────────────────────────── per-stock arrays
def stock_arrays(df: pd.DataFrame, cfg: dict = LUK_CONFIG) -> dict:
    c, h, l, o, v = (df[k].astype(float) for k in ("Close", "High", "Low", "Open", "Volume"))
    return {
        "d": df.index, "c": c.to_numpy(), "h": h.to_numpy(), "l": l.to_numpy(), "o": o.to_numpy(), "v": v.to_numpy(),
        "e9": ind.ema(c, cfg["ema_fast"]).to_numpy(), "e21": ind.ema(c, cfg["ema_mid"]).to_numpy(),
        "e50": ind.ema(c, cfg["ema_slow"]).to_numpy(),
        "adr": ind.adr_pct(h, l, cfg["adr_win"]).to_numpy(),
        "value20": ind.value_avg(c, v, cfg["value_win"]).to_numpy(),
        "vol50": ind.volume_avg_prior(v, cfg["vol_avg_win"]).to_numpy(),
        "p1": ind.perf_pct(c, 21).to_numpy(), "p3": ind.perf_pct(c, 63).to_numpy(), "p6": ind.perf_pct(c, 126).to_numpy(),
    }


def _avwap(x: dict, a: int, i: int):
    return ind.avwap(x["h"], x["l"], x["c"], x["v"], a, i)


def _mom(x: dict, i: int) -> float:
    p = [0.0 if not np.isfinite(x[k][i]) else x[k][i] for k in ("p1", "p3", "p6")]
    return 0.2 * p[0] + 0.4 * p[1] + 0.4 * p[2]


def _base(x: dict, i: int, setup: str, side: str, score: float, ref: float | None, ref_name: str | None,
          note: str, adr_ref: float | None = None) -> dict:
    c = x["c"][i]
    adr_abs = (adr_ref if adr_ref is not None else x["adr"][i]) / 100 * c
    return {
        "setup": setup, "side": side, "score": _r(score, 1), "close": _r(c, 3), "adr_pct": _r(x["adr"][i]),
        "ema9": _r(x["e9"][i], 3), "ema21": _r(x["e21"][i], 3),
        "ref_level": _r(ref, 3) if ref else None, "ref_name": ref_name,
        "dist_ref_adr": _r((c - ref) / adr_abs) if ref and adr_abs else None,
        "perf_1m": _r(x["p1"][i], 1), "perf_3m": _r(x["p3"][i], 1), "perf_6m": _r(x["p6"][i], 1),
        "value20_mb": _r(x["value20"][i] / 1e6, 1), "note": note,
    }


# ─────────────────────────────────────────────────────────── setups (index i = วันนี้ · ใช้แท่ง ≤ i เท่านั้น)
def long_pullback(x: dict, i: int, cfg: dict = LUK_CONFIG) -> dict | None:
    c, h, l, v = x["c"], x["h"], x["l"], x["v"]
    rb = cfg["ema_rising_bars"]
    if i < rb or not (x["e9"][i] > x["e21"][i] > x["e50"][i] and x["e21"][i] > x["e21"][i - rb]):
        return None
    lo_w = max(0, i - cfg["pb_high_win"] + 1)
    hi = max(range(lo_w, i + 1), key=lambda k: h[k])
    bars_since = i - hi
    if not (cfg["pb_min_bars"] <= bars_since <= cfg["pb_max_bars"]) or c[i] < x["e50"][i]:
        return None
    anchor = max(range(max(0, i - cfg["pb_avwap_win"] + 1), i + 1), key=lambda k: v[k])
    vw = _avwap(x, anchor, i)
    levels = {"EMA9": x["e9"][i], "EMA21": x["e21"][i]}
    if vw and vw < c[i]:
        levels["AVWAP"] = vw
    adr_abs = c[i] * x["adr"][i] / 100
    zone = cfg["pb_zone_adr"] * adr_abs
    touched = {k: q for k, q in levels.items() if l[i] <= q + zone and c[i] >= q - zone}
    if not touched:
        return None
    name, lvl = min(touched.items(), key=lambda kv: abs(l[i] - kv[1]))
    pb_vol = float(np.mean(v[hi + 1:i + 1])) if i > hi else 0.0
    dryup = bool(x["vol50"][i]) and pb_vol < x["vol50"][i]
    depth = (h[hi] - c[i]) / adr_abs if adr_abs else 0.0
    score = _mom(x, i) / 10 + (10 if dryup else 0) + (5 if name != "EMA9" else 0) - max(0.0, depth - 4) * 3
    note = (f"ย่อ {bars_since} แท่งเข้าหา {name}{' · วอลุ่มแห้ง' if dryup else ''} · "
            f"AVWAP ยึดจากวันวอลุ่มสูงสุด {_d(x['d'][anchor])}")
    return _base(x, i, "LONG_PULLBACK", "long", score, lvl, name, note)


def episodic_pivot(x: dict, i: int, cfg: dict = LUK_CONFIG) -> dict | None:
    c, o, v = x["c"], x["o"], x["v"]
    if i < 1 or not c[i - 1]:
        return None
    gap = (o[i] / c[i - 1] - 1) * 100
    rvol = v[i] / x["vol50"][i] if x["vol50"][i] else 0.0
    if gap < cfg["ep_min_gap_pct"] or rvol < cfg["ep_min_rvol"]:
        return None
    prev = v[max(0, i - cfg["ep_record_win"] + 1):i]
    record = len(prev) > 0 and v[i] >= prev.max()
    held = c[i] >= o[i]
    score = gap + rvol * 2 + (10 if record else 0) + (5 if held else -5)
    note = (f"gap {gap:.1f}% วอลุ่ม {rvol:.1f} เท่า{' · วอลุ่มสูงสุดในรอบปี' if record else ''}"
            f"{' · ปิดเหนือราคาเปิด' if held else ' · ปิดต่ำกว่าราคาเปิด'}")
    return _base(x, i, "EPISODIC_PIVOT", "long", score, o[i], "OPEN", note)


def short_weak_bounce(x: dict, i: int, cfg: dict = LUK_CONFIG) -> dict | None:
    c, h, l, v = x["c"], x["h"], x["l"], x["v"]
    rb = cfg["ema_rising_bars"]
    if i < max(rb, cfg["sb_breakdown_lookback"]):
        return None
    yr = h[max(0, i - cfg["sb_year_win"] + 1):i + 1].max()
    near = h[max(0, i - cfg["sb_leader_high_win"] + 1):i + 1].max()
    p6 = x["p6"][i] if np.isfinite(x["p6"][i]) else 0.0
    if not (p6 > cfg["sb_leader_perf6m"] or near >= cfg["sb_leader_near_high"] * yr):
        return None
    if not (x["e9"][i] < x["e21"][i] and x["e21"][i] < x["e21"][i - rb] and c[i] < x["e21"][i]):
        return None
    bd = None
    for k in range(i - cfg["sb_breakdown_lookback"] + 1, i):
        if (c[k - 1] >= x["e21"][k - 1] and c[k] < x["e21"][k]
                and v[k] > cfg["sb_breakdown_vol_mult"] * x["vol50"][i]):
            bd = k
    if bd is None:
        return None
    lo = min(range(bd, i + 1), key=lambda k: l[k])
    bounce = i - lo
    if not (cfg["sb_bounce_min"] <= bounce <= cfg["sb_bounce_max"]):
        return None
    vw = _avwap(x, bd, i)
    levels = {"EMA9": x["e9"][i], "EMA21": x["e21"][i]}
    if vw and vw > c[i]:
        levels["AVWAP(วันหลุด)"] = vw
    above = {k: q for k, q in levels.items() if q >= c[i]}
    if not above:
        return None
    name, lvl = min(above.items(), key=lambda kv: kv[1] - c[i])
    adr_abs = c[i] * x["adr"][i] / 100
    if (lvl - h[i]) > cfg["sb_zone_adr"] * adr_abs:
        return None
    bvol = float(np.mean(v[lo + 1:i + 1])) if i > lo else 0.0
    light = bool(x["vol50"][i]) and bvol < cfg["sb_light_vol"] * x["vol50"][i]
    p1 = x["p1"][i] if np.isfinite(x["p1"][i]) else 0.0
    score = abs(p1) / 2 + (10 if light else 0) + (5 if h[i] >= lvl else 0)
    note = (f"หลุด EMA21 พร้อมวอลุ่มเมื่อ {_d(x['d'][bd])} · เด้ง {bounce} แท่งเข้าหา {name}"
            f"{' · วอลุ่มเบา' if light else ''}")
    return _base(x, i, "SHORT_WEAK_BOUNCE", "weak", score, lvl, name, note)


def parabolic_short(x: dict, i: int, cfg: dict = LUK_CONFIG) -> dict | None:
    c, h, l, o = x["c"], x["h"], x["l"], x["o"]
    bf, bt, uw = cfg["para_base_from"], cfg["para_base_to"], cfg["para_up_win"]
    if i < bf:
        return None
    ups = sum(1 for k in range(i - uw + 1, i + 1) if c[k] > c[k - 1])
    gain5 = (c[i] / c[i - 5] - 1) * 100
    bh, bl = h[i - bf + 1:i - bt + 1], l[i - bf + 1:i - bt + 1]
    ok = bl > 0
    pre = float(np.mean(bh[ok] / bl[ok] - 1)) if ok.any() else 0.0      # ADR ก่อนวิ่ง (สัดส่วน)
    pre_abs = pre * c[i]
    ext = (c[i] - x["e9"][i]) / pre_abs if pre_abs else 0.0
    if ups < cfg["para_min_up_bars"] or gain5 < cfg["para_min_5d_gain"] or ext < cfg["para_min_ext_adr"]:
        return None
    gap_up = o[i] > h[i - 1]
    score = ext * 5 + gain5 / 5 + (5 if gap_up else 0)
    note = (f"ขึ้น {ups}/{uw} แท่ง · +{gain5:.0f}% ใน 5 วัน · ห่างเหนือ EMA9 {ext:.1f} เท่าของ ADR ก่อนวิ่ง"
            f"{' · gap ขึ้น' if gap_up else ''}")
    # dist_ref_adr ใช้ ADR ก่อนวิ่งตัวเดียวกับ note (ต้นแบบใช้ ADR ปัจจุบัน ทำให้ 2 ค่าไม่เท่ากัน)
    return _base(x, i, "PARABOLIC_SHORT", "weak", score, x["e9"][i], "EMA9", note, adr_ref=pre * 100)


SETUP_FUNCS = {"LONG_PULLBACK": long_pullback, "EPISODIC_PIVOT": episodic_pivot,
               "SHORT_WEAK_BOUNCE": short_weak_bounce, "PARABOLIC_SHORT": parabolic_short}


# ─────────────────────────────────────────────────────────── regime / filters / themes
def regime(leaders: list[tuple[dict, int]], set_close: pd.Series, cfg: dict = LUK_CONFIG) -> dict:
    """leaders = [(arrays, i)] · set_close = SET ถึงวันนี้"""
    n = len(leaders) or 1
    pct = lambda f: round(sum(1 for x, i in leaders if f(x, i)) / n * 100, 1)  # noqa: E731
    above9 = pct(lambda x, i: x["c"][i] > x["e9"][i])
    above21 = pct(lambda x, i: x["c"][i] > x["e21"][i])
    stacked = pct(lambda x, i: x["e9"][i] > x["e21"][i])
    gapdn = pct(lambda x, i: i >= 1 and x["o"][i] < x["l"][i - 1])
    ws, wl = cfg["fail_high_win_short"], cfg["fail_high_win_long"]
    failed = pct(lambda x, i: x["h"][max(0, i - ws + 1):i + 1].max() >= x["h"][max(0, i - wl + 1):i + 1].max()
                 and x["c"][i] < x["e9"][i])
    e9, e21 = ind.ema(set_close, cfg["ema_fast"]), ind.ema(set_close, cfg["ema_mid"])
    rb = cfg["ema_rising_bars"]
    s = {"close": _r(set_close.iloc[-1]), "above_ema9": bool(set_close.iloc[-1] > e9.iloc[-1]),
         "above_ema21": bool(set_close.iloc[-1] > e21.iloc[-1]),
         "ema21_rising": bool(len(e21) > rb and e21.iloc[-1] > e21.iloc[-1 - rb])}
    up = s["above_ema21"] and s["ema21_rising"]
    dn = (not s["above_ema21"]) and (not s["ema21_rising"])
    if above21 >= cfg["reg_long_above21"] and stacked >= cfg["reg_long_stacked"] and up:
        mode, mult = "LONG", 1.0
    elif above21 <= cfg["reg_short_above21"] and dn:
        mode, mult = "SHORT", 1.0
    elif above21 <= cfg["reg_def_above21"] or failed >= cfg["reg_def_failed"]:
        mode, mult = "DEFENSIVE", cfg["reg_half_risk_mult"]
    else:
        mode, mult = "MIXED", cfg["reg_half_risk_mult"]
    return {
        "mode": mode, "suggested_risk_pct": round(cfg["risk_pct"] * mult, 3), "leaders_counted": len(leaders),
        "pct_above_ema9": above9, "pct_above_ema21": above21, "pct_ema9_over_ema21": stacked,
        "pct_gap_down_today": gapdn, "pct_failed_breakout": failed, "set": s,
        "rule": (f"LONG: ผู้นำ ≥ {cfg['reg_long_above21']:g}% เหนือ EMA21 และ ≥ {cfg['reg_long_stacked']:g}% มี EMA9 > EMA21 "
                 f"และ SET เหนือ EMA21 ที่กำลังขึ้น · SHORT: ผู้นำ ≤ {cfg['reg_short_above21']:g}% เหนือ EMA21 และ SET ต่ำกว่า EMA21 "
                 f"ที่กำลังลง · DEFENSIVE: ผู้นำ ≤ {cfg['reg_def_above21']:g}% เหนือ EMA21 หรือเบรกล้ม ≥ {cfg['reg_def_failed']:g}% · "
                 f"นอกนั้น MIXED · DEFENSIVE/MIXED ใช้ risk × {cfg['reg_half_risk_mult']:g} · "
                 "ถ้าโดน stop ติดกันหลายไม้ ลด risk เองด้วย (สัญญาณของ Luk เอง)"),
    }


def passes_filters(x: dict, i: int, cfg: dict = LUK_CONFIG) -> dict:
    p = x["c"][i] >= cfg["min_price"]
    v = p and x["value20"][i] >= cfg["min_value20_mb"] * 1e6
    a = v and x["adr"][i] >= cfg["min_adr_pct"]
    return {"price": bool(p), "value20": bool(v), "adr": bool(a)}


def theme_alerts(rows: list[dict], cfg: dict = LUK_CONFIG) -> list[dict]:
    """ฝั่ง long วันเดียวกัน subsector เดียวกัน ≥ theme_min_count (หุ้น mai ไม่มี subsector → ใช้ sector)"""
    groups: dict[str, list[str]] = {}
    for r in rows:
        key = r.get("subsector") or r.get("sector")
        if key and r["symbol"] not in groups.setdefault(key, []):
            groups[key].append(r["symbol"])
    return sorted(({"subsector": k, "count": len(v), "symbols": v}
                   for k, v in groups.items() if len(v) >= cfg["theme_min_count"]), key=lambda a: -a["count"])


def load_sector_map() -> dict:
    path = next((p for p in SECTOR_MAP_CANDIDATES if p.exists()), None)
    if path is None:
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("ticker_to_sector", {})


def load_rs_ratings(rs_file: Path) -> dict:
    df = pd.read_csv(rs_file)
    return {str(t): int(r) for t, r in zip(df["Ticker"], df["RS_Rating"]) if pd.notna(r)}


# ─────────────────────────────────────────────────────────── build
def build(history_dir: Path, set_index_file: Path, rs_file: Path, cfg: dict = LUK_CONFIG, log=print) -> dict:
    t0 = time.time()
    warnings: list[str] = []
    now = datetime.now(BKK).isoformat(timespec="seconds")
    out = {"generated_at": now, "as_of": None, "config": cfg, "regime": {}, "funnel": {}, "counts": {},
           "leaders": [], "candidates": [], "weak": [], "theme_alerts": [], "warnings": warnings,
           "reminder": REMINDER}
    try:
        set_df = read_set_index(set_index_file)
    except Exception as e:  # noqa: BLE001
        warnings.append(f"SET_INDEX.csv อ่านไม่ได้ ({type(e).__name__}: {e}) — ไม่มีผล")
        return out
    funds = load_fund_tickers()
    if not funds:
        warnings.append("sector_map ไม่พบหรือไม่มีกองทุน — ไม่ได้ตัดกองทุน/REIT")
    t2s = load_sector_map()
    try:
        rs = load_rs_ratings(rs_file)
    except Exception as e:  # noqa: BLE001
        rs = {}
        warnings.append(f"อ่าน {Path(rs_file).name} ไม่ได้ ({type(e).__name__}) — ไม่มีผู้นำ/LONG_PULLBACK")

    cal = set_df.index
    data, last_dates, errors = {}, {}, []
    for fp in iter_stock_files(history_dir):
        t = fp.stem
        if t.upper() in funds:
            continue
        try:
            df, _ = read_stock(fp)
            if df is None:
                continue
            df = df[df.index.isin(cal)]
            if len(df) < cfg["min_bars"]:
                continue
            data[t] = df
            last_dates[t] = df.index[-1]
        except Exception as e:  # noqa: BLE001
            errors.append(f"{t}:{type(e).__name__}")
    if not data:
        warnings.append("ไม่มีหุ้นที่อ่านได้")
        return out
    as_of = cal[-1]
    stock_as_of = pd.Series(last_dates).mode().iloc[0]
    if stock_as_of != as_of:
        warnings.append(f"SET_INDEX.csv วันล่าสุด {_d(as_of)} ≠ ข้อมูลหุ้น {_d(stock_as_of)}")
        as_of = min(as_of, stock_as_of)
    out["as_of"] = _d(as_of)

    # ── funnel + arrays ของหุ้นที่มีแท่งวันนี้
    arrays, passed = {}, []
    funnel = {"universe": 0, "price": 0, "value20": 0, "adr": 0}
    for t, df in data.items():
        try:
            df = df[df.index <= as_of]
            if df.index[-1] != as_of or len(df) < cfg["min_bars"]:
                continue
            x = stock_arrays(df, cfg)
            i = len(df) - 1
            funnel["universe"] += 1
            f = passes_filters(x, i, cfg)
            for k in ("price", "value20", "adr"):
                funnel[k] += int(f[k])
            arrays[t] = (x, i)
            if f["adr"]:
                passed.append(t)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{t}:{type(e).__name__}")
    out["funnel"] = funnel

    # ── ผู้นำ Top N ตาม RS Rating ของระบบ
    ranked = sorted((t for t in passed if t in rs), key=lambda t: (-rs[t], t))
    leaders = ranked[:cfg["leader_top_n"]]
    rank = {t: k + 1 for k, t in enumerate(leaders)}
    out["leaders"] = [{"symbol": t, "rank": rank[t], "rs_rating": rs[t], "close": _r(arrays[t][0]["c"][arrays[t][1]], 3),
                       "above_ema21": bool(arrays[t][0]["c"][arrays[t][1]] > arrays[t][0]["e21"][arrays[t][1]])}
                      for t in leaders]

    try:
        reg = regime([arrays[t] for t in leaders], set_df["Close"][set_df.index <= as_of], cfg)
    except Exception as e:  # noqa: BLE001
        reg = {}
        warnings.append(f"regime: ({type(e).__name__}: {e})")
    out["regime"] = reg
    risk = reg.get("suggested_risk_pct", cfg["risk_pct"] * cfg["reg_half_risk_mult"])

    cands, weak = [], []
    for t in passed:
        x, i = arrays[t]
        for name, fn in SETUP_FUNCS.items():
            if name == "LONG_PULLBACK" and t not in rank:
                continue
            try:
                r = fn(x, i, cfg)
            except (IndexError, ZeroDivisionError, ValueError) as e:
                errors.append(f"{t}/{name}:{type(e).__name__}")
                r = None
            if not r:
                continue
            info = t2s.get(t) or {}
            r = {"symbol": t, **r, "rs_rating": rs.get(t), "leader_rank": rank.get(t),
                 "sector": info.get("sector"), "subsector": info.get("subsector") or None}
            if r["side"] == "long":
                r.update(long_risk(x["c"][i], x["h"][i], x["l"][i], risk, cfg))
                cands.append(r)
            else:
                weak.append({k: r[k] for k in ("symbol", "setup", "score", "close", "ref_level", "ref_name",
                                               "dist_ref_adr", "perf_1m", "rs_rating", "sector", "subsector", "note")})
    cands.sort(key=lambda r: (LONG_SETUPS.index(r["setup"]), -(r["score"] or 0)))
    weak.sort(key=lambda r: (WEAK_SETUPS.index(r["setup"]), -(r["score"] or 0)))
    out["candidates"], out["weak"] = cands, weak
    out["counts"] = {s: sum(1 for r in cands + weak if r["setup"] == s) for s in LONG_SETUPS + WEAK_SETUPS}
    out["theme_alerts"] = theme_alerts(cands, cfg)
    if errors:
        warnings.append(f"คำนวณไม่ได้ {len(errors)} รายการ: {', '.join(errors[:10])}")
    log(f"  Luk: as_of {out['as_of']} · regime {reg.get('mode')} · funnel {funnel} · leaders {len(leaders)} · "
        f"counts {out['counts']} · {time.time() - t0:.0f}s")
    return out


def hits_from(out: dict) -> dict:
    """luk_hits.json (~1 KB) — รายวันสำหรับ Report Card: แถว = fields ตามลำดับ"""
    fields = ["symbol", "setup", "side", "close", "rs_rating", "entry_trigger", "stop_price", "ref_level"]
    rows = [[r.get(f) for f in fields] for r in out.get("candidates", [])]
    rows += [[r["symbol"], r["setup"], "weak", r["close"], r.get("rs_rating"), None, None, r.get("ref_level")]
             for r in out.get("weak", [])]
    reg = out.get("regime") or {}
    return {"generated_at": out["generated_at"], "as_of": out["as_of"],
            "regime": {"mode": reg.get("mode"), "pct_above_ema21": reg.get("pct_above_ema21")},
            "fields": fields, "rows": rows}


def _json_default(o):
    if isinstance(o, np.generic):
        return o.item()
    raise TypeError(f"not JSON serializable: {type(o).__name__}")


def write_json(obj: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"), allow_nan=False, default=_json_default)
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Martin Luk watchlist (SET) → luk.json + luk_hits.json")
    ap.add_argument("--history-dir", type=Path, default=HISTORY_DIR)
    ap.add_argument("--set-index", type=Path, default=None, help="default <history-dir>/SET_INDEX.csv")
    ap.add_argument("--rs-file", type=Path, default=RS_FILE)
    ap.add_argument("--out", type=Path, default=OUT_FILE)
    ap.add_argument("--hits-out", type=Path, default=HITS_FILE)
    a = ap.parse_args(argv)
    print("📈 [Luk] กำลังสแกน Martin Luk (SET)...")
    try:
        out = build(a.history_dir, a.set_index or a.history_dir / "SET_INDEX.csv", a.rs_file)
    except Exception as e:  # noqa: BLE001
        out = {"generated_at": datetime.now(BKK).isoformat(timespec="seconds"), "as_of": None, "config": LUK_CONFIG,
               "regime": {}, "funnel": {}, "counts": {}, "leaders": [], "candidates": [], "weak": [],
               "theme_alerts": [], "warnings": [f"scan_luk ล้ม: {type(e).__name__}: {e}"], "reminder": REMINDER}
    for w in out["warnings"]:
        print(f"  ⚠️ {w}")
    try:
        write_json(out, a.out)
        write_json(hits_from(out), a.hits_out)
    except Exception as e:  # noqa: BLE001
        print(f"❌ [Luk] เขียนไฟล์ไม่ได้: {e}")
        return 1
    print(f"✅ [Luk] {a.out} ({a.out.stat().st_size / 1024:.0f} KB) + {a.hits_out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
