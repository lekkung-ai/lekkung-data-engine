"""report_card_events.py — Report Card แบบ "เหตุการณ์" ของ RS-7 และ Martin Luk → output/report_card_events.json

แยกจาก Report Card เดิม (stockdesk scripts/calculate_report_card.py ไม่แตะ) · ราคาจาก OHLCV + SET_INDEX.csv ของ pipeline
(ไม่ใช้ Yahoo / breadth.json) · รายชื่อเหตุการณ์จากไฟล์ hits:
  live     = stockdesk data/history/<วัน>/{rs7,luk}_hits.json + ไฟล์ hits ของวันนี้ใน output/ (ยังไม่ถูก copy)
  backfill = stockdesk data/backfill/{rs7,luk}_hits_backfill.json — ใช้เฉพาะวันก่อน live วันแรก (ของจริงชนะเสมอ)

นิยาม (ผู้ใช้ตัดสิน 2026-11-28) — ข้อความไทยเดียวกันอยู่ใน DEFINITIONS (หน้าเว็บแสดง):
  RS-7 ฝั่งซื้อ  : สถานะ BREAKOUT / PIVOT_RECLAIM / BASE_BREAKOUT · เข้า = ปิดวันนั้น · ตัวเดิม+รูปแบบเดิมภายใน 10 วันทำการ
                   หลังครั้งที่นับ = ไม่นับซ้ำ · วัด 5/10/20 วันทำการ · ชนะ = 20 วัน > 0 · เทียบ SET · MFE/MAE 20 วัน
  RS-7 ฝั่งเฝ้า  : เข้า list = วันแรกที่มีสถานะเฝ้า (หลังหายจากรูปแบบนั้น ≥ 10 วันทำการ) · % ที่เกิดฝั่งซื้อ (ทุกรูปแบบ)
                   ภายใน 20 วัน · ผล 20 วันจากปิดวันเข้า list
  Luk ฝั่ง long : เกิดจุดเข้าเมื่อวันถัดไป high ≥ entry_trigger · เข้า = max(open, entry_trigger) · แท่งเข้า low ≤ stop
                   = โดน stop วันเข้า (ระวังสุด) · ถือครบ: ปิดวันเข้า / +5 / +10 วัน · ใช้ stop: ออกที่ stop ถ้าแตะภายใน 10 วัน
                   (เปิด gap ต่ำกว่า stop = ออกที่ open) · MFE/MAE 10 วันหลังวันเข้า
  Luk หุ้นอ่อนแรง: จากปิดวันเกิด → 1/5/10 วัน · เทียบ SET · % ที่แพ้ SET ใน 5 วัน
  ข้อมูลข้างหน้ายังไม่ครบ = pending (นับจำนวน ไม่เข้าสถิติระยะนั้น) · กลุ่ม n < 30 = small_sample
ราคาใน hits เป็นราคา ณ วันนั้น ส่วน OHLCV เป็นราคาปรับปันผลล่าสุด → entry_trigger / stop ถูกปรับด้วยอัตราส่วน
close(OHLCV) / close(hits) ของวันเกิด · ใช้เฉพาะแท่งที่ตรงวันทำการของ SET_INDEX.csv

รันเป็น step แยกใน daily-scan.yml (continue-on-error) · ส่วนไหนพัง ส่วนนั้นว่าง + warnings[] · exit 0 ยกเว้นเขียนไฟล์ไม่ได้
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
sys.path[:0] = [str(_ROOT), str(_HERE)]
from config import HISTORY_DIR, RESULTS_DIR  # noqa: E402
from scan_rs7 import BKK, read_set_index, read_stock, write_json  # noqa: E402

OUT_FILE = RESULTS_DIR / "output" / "report_card_events.json"
STOCKDESK_CANDIDATES = [_ROOT / "stockdesk_repo", _ROOT.parent.parent / "Claude" / "dashboard" / "stockdesk"]

RS7_TRIGGERS = ("BREAKOUT", "PIVOT_RECLAIM", "BASE_BREAKOUT")
RS7_WATCH = ("SETUP", "WATCH", "REST", "RUNNING", "FIRST_PULLBACK", "PIVOT_RESET", "BASE_RESET")
RS7_DEDUPE_DAYS = 10
RS7_WATCH_GAP_DAYS = 10
RS7_HORIZONS = (5, 10, 20)
RS7_CONVERT_DAYS = 20
LUK_HOLD = (5, 10)
LUK_STOP_DAYS = 10
WEAK_HORIZONS = (1, 5, 10)
SMALL_SAMPLE = 30
RECENT_N = 30
PATTERN_NAMES = {1: "Predictive", 2: "Coincident", 3: "Confirming", 4: "Sore thumbs", 5: "Fast rebound",
                 6: "Lead and rest", 7: "Failure reset"}

DEFINITIONS = {
    "rs7_buy": ("สัญญาณซื้อ = สถานะ BREAKOUT / PIVOT_RECLAIM / BASE_BREAKOUT · ราคาเข้า = ราคาปิดวันนั้น · "
                f"หุ้นตัวเดิมรูปแบบเดิมซ้ำภายใน {RS7_DEDUPE_DAYS} วันทำการ นับครั้งแรก · วัดผล 5 / 10 / 20 วันทำการ "
                "(ถือครบ ไม่มีกฎขาย) · ชนะ = ผล 20 วัน > 0 · เทียบ SET ช่วงเดียวกัน · MFE/MAE = high/low สูงสุด/ต่ำสุดภายใน 20 วัน"),
    "rs7_watch": (f"เข้า list = วันแรกที่ขึ้นสถานะเฝ้า (SETUP / WATCH / REST / RUNNING / FIRST_PULLBACK / PIVOT_RESET / "
                  f"BASE_RESET) หลังหายจากรูปแบบนั้น ≥ {RS7_WATCH_GAP_DAYS} วันทำการ · วัดว่าเกิดสัญญาณซื้อ (รูปแบบใดก็ได้) "
                  f"ภายใน {RS7_CONVERT_DAYS} วันไหม และผล 20 วันจากราคาปิดวันเข้า list"),
    "luk_long": ("จุดเข้า = วันถัดไปราคาสูงสุดแตะ entry_trigger (high วันสัญญาณ + 1 tick) · ราคาเข้า = ค่าที่มากกว่าระหว่างราคาเปิดกับ "
                 "entry_trigger · แท่งวันเข้าแตะ stop ด้วย = นับว่าโดน stop วันเข้า (ระวังสุด เพราะกราฟรายวันไม่รู้ว่าอะไรก่อน) · "
                 "ถือครบ: ผลปิดวันเข้า / 5 / 10 วัน (ไม่สน stop) · ใช้ stop: ออกที่ stop ถ้าแตะภายใน 10 วัน ถ้าเปิด gap ต่ำกว่า stop "
                 "ออกที่ราคาเปิด ไม่แตะ = ปิดวันที่ 10 · MFE/MAE ภายใน 10 วันหลังวันเข้า"),
    "luk_weak": "หุ้นอ่อนแรง: ผลจากราคาปิดวันที่ขึ้นรายการ → 1 / 5 / 10 วัน เทียบ SET ช่วงเดียวกัน · แพ้ SET = ผล 5 วันต่ำกว่า SET",
    "common": (f"เหตุการณ์ที่ข้อมูลข้างหน้ายังไม่ครบ = pending (นับจำนวนแต่ไม่เข้าสถิติของระยะนั้น) · กลุ่มที่มีผลครบน้อยกว่า "
               f"{SMALL_SAMPLE} ครั้ง = ตัวอย่างน้อย · ใช้ราคาและ SET จาก pipeline · ย้อนหลังสร้างด้วยกติกาปัจจุบันแบบ point-in-time "
               "(ไม่ใช่สิ่งที่หน้าเว็บแสดงในวันนั้น)"),
}


# ─────────────────────────────────────────────────────────── helpers
def _r(x, nd=2):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return round(x, nd) if math.isfinite(x) else None


def _load(p: Path):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def find_stockdesk(explicit: Path | None) -> Path | None:
    if explicit:
        return explicit
    return next((p for p in STOCKDESK_CANDIDATES if (p / "data").exists()), None)


# ─────────────────────────────────────────────────────────── hits loading / merging
def load_live(stockdesk: Path | None, today_dir: Path | None, name: str) -> dict:
    """{date: hits_dict} จาก data/history/<วัน>/<name>.json + ไฟล์วันนี้ใน output/ · วัน = as_of ในไฟล์ (ไม่มี = ชื่อโฟลเดอร์)"""
    out = {}
    paths = []
    if stockdesk is not None:
        paths += sorted((stockdesk / "data" / "history").glob(f"*/{name}.json"))
    if today_dir is not None and (today_dir / f"{name}.json").exists():
        paths.append(today_dir / f"{name}.json")
    for p in paths:
        h = _load(p)
        if not isinstance(h, dict) or not isinstance(h.get("rows"), list):
            continue
        d = h.get("as_of") or p.parent.name
        if isinstance(d, str) and d[:2] == "20":
            out[d] = h
    return out


def merge_days(backfill_days: dict, live: dict) -> tuple[dict, dict]:
    """(days, source) · backfill ใช้เฉพาะวันก่อน live วันแรก · live ชนะทุกวันที่ซ้ำ"""
    first_live = min(live) if live else None
    days, source = {}, {}
    for d, v in (backfill_days or {}).items():
        if first_live is None or d < first_live:
            days[d], source[d] = v, "backfill"
    for d, v in live.items():
        days[d], source[d] = v, "live"
    return dict(sorted(days.items())), source


def source_ranges(source: dict) -> dict:
    out = {}
    for kind in ("backfill", "live"):
        ds = sorted(d for d, s in source.items() if s == kind)
        out[kind] = {"from": ds[0], "to": ds[-1], "days": len(ds)} if ds else None
    return out


def row_dict(fields: list, row: list) -> dict:
    return dict(zip(fields, row))


# ─────────────────────────────────────────────────────────── prices
class Prices:
    """OHLC ต่อหุ้นบนวันทำการของ SET + SET close · pos(t, date) → index ของแท่งวันนั้น"""

    def __init__(self, set_df: pd.DataFrame, frames: dict):
        self.cal = list(set_df.index.strftime("%Y-%m-%d"))
        self.cal_pos = {d: k for k, d in enumerate(self.cal)}
        self.set_c = set_df["Close"].to_numpy(float)
        self.px = {}
        for t, df in frames.items():
            df = df[df.index.isin(set_df.index)]
            self.px[t] = {"d": {d: k for k, d in enumerate(df.index.strftime("%Y-%m-%d"))},
                          "o": df["Open"].to_numpy(float), "h": df["High"].to_numpy(float),
                          "l": df["Low"].to_numpy(float), "c": df["Close"].to_numpy(float)}

    def get(self, t: str, d: str):
        x = self.px.get(t)
        if x is None or d not in x["d"]:
            return None, None
        return x, x["d"][d]

    def set_ret(self, d: str, n: int, start_offset: int = 0):
        k = self.cal_pos.get(d)
        if k is None or k + start_offset + n >= len(self.set_c):
            return None
        return (self.set_c[k + start_offset + n] / self.set_c[k + start_offset] - 1) * 100


def load_prices(history_dir: Path, set_index_file: Path, tickers: set) -> Prices:
    set_df = read_set_index(set_index_file)
    frames = {}
    for t in sorted(tickers):
        fp = history_dir / f"{t}.csv"
        if not fp.exists():
            continue
        try:
            df, _ = read_stock(fp)
        except Exception:  # noqa: BLE001
            df = None
        if df is not None and len(df):
            frames[t] = df
    return Prices(set_df, frames)


# ─────────────────────────────────────────────────────────── statistics
def horizon_stats(vals: list, sets: list | None = None, pending: int = 0, win_gt: float = 0.0) -> dict:
    """vals = ผลที่ครบแล้ว (%) · sets = ผล SET คู่กัน (None ได้) · pending = จำนวนที่ยังไม่ครบ"""
    v = np.array([x for x in vals if x is not None], dtype=float)
    out = {"n": int(len(v)), "pending": int(pending), "small_sample": bool(len(v) < SMALL_SAMPLE),
           "avg": _r(v.mean()) if len(v) else None, "median": _r(np.median(v)) if len(v) else None,
           "win_pct": _r((v > win_gt).mean() * 100, 1) if len(v) else None}
    if sets is not None:
        pairs = [(a, b) for a, b in zip(vals, sets) if a is not None and b is not None]
        if pairs:
            a, b = np.array(pairs, dtype=float).T
            out.update(set_avg=_r(b.mean()), excess_avg=_r((a - b).mean()), beat_set_pct=_r((a > b).mean() * 100, 1))
        else:
            out.update(set_avg=None, excess_avg=None, beat_set_pct=None)
    return out


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return _r(np.mean(xs)) if xs else None


# ─────────────────────────────────────────────────────────── RS-7
def rs7_buy_events(days: dict, fields: list, cal_pos: dict, source: dict) -> list[dict]:
    """เหตุการณ์ซื้อ (dedupe ตัวเดิม+รูปแบบเดิมภายใน RS7_DEDUPE_DAYS วันทำการหลังครั้งที่นับ)"""
    last: dict = {}
    events = []
    for d, h in days.items():
        k = cal_pos.get(d)
        if k is None:
            continue
        for row in h.get("rows", []):
            r = row_dict(fields, row)
            for pid, st in r.get("patterns") or []:
                if st not in RS7_TRIGGERS:
                    continue
                key = (r["ticker"], pid)
                if key in last and k - last[key] <= RS7_DEDUPE_DAYS:
                    continue
                last[key] = k
                events.append({"date": d, "ticker": r["ticker"], "pattern": pid, "status": st,
                               "phase": h.get("phase"), "close_hits": r.get("close"), "source": source.get(d)})
    return events


def rs7_measure_buy(ev: dict, P: Prices) -> dict:
    x, k = P.get(ev["ticker"], ev["date"])
    out = dict(ev)
    if x is None:
        out["no_price"] = True
        return out
    c, h, l = x["c"], x["h"], x["l"]
    e = c[k]
    out["entry"] = _r(e, 3)
    for n in RS7_HORIZONS:
        out[f"r{n}"] = _r((c[k + n] / e - 1) * 100) if k + n < len(c) else None
        out[f"set{n}"] = _r(P.set_ret(ev["date"], n)) if out[f"r{n}"] is not None else None
    w = max(RS7_HORIZONS)
    if k + w < len(c):
        out["mfe"] = _r((h[k + 1:k + w + 1].max() / e - 1) * 100)
        out["mae"] = _r((l[k + 1:k + w + 1].min() / e - 1) * 100)
    else:
        out["mfe"] = out["mae"] = None
    return out


def rs7_group(evs: list[dict]) -> dict:
    out = {"n_events": len(evs)}
    for n in RS7_HORIZONS:
        done = [e for e in evs if e.get(f"r{n}") is not None]
        out[f"d{n}"] = horizon_stats([e[f"r{n}"] for e in done], [e.get(f"set{n}") for e in done],
                                     pending=len(evs) - len(done))
    out["mfe_avg"] = _avg([e.get("mfe") for e in evs])
    out["mae_avg"] = _avg([e.get("mae") for e in evs])
    out["small_sample"] = out[f"d{max(RS7_HORIZONS)}"]["n"] < SMALL_SAMPLE
    return out


def rs7_watch_entries(days: dict, fields: list, cal_pos: dict, source: dict) -> list[dict]:
    """เข้า list: สถานะเฝ้าครั้งแรกของ (หุ้น, รูปแบบ) หลังหายไป ≥ RS7_WATCH_GAP_DAYS วันทำการ"""
    seen: dict = {}
    out = []
    for d, h in days.items():
        k = cal_pos.get(d)
        if k is None:
            continue
        for row in h.get("rows", []):
            r = row_dict(fields, row)
            for pid, st in r.get("patterns") or []:
                key = (r["ticker"], pid)
                prev = seen.get(key)
                seen[key] = k
                if st in RS7_WATCH and (prev is None or k - prev - 1 >= RS7_WATCH_GAP_DAYS):
                    out.append({"date": d, "ticker": r["ticker"], "pattern": pid, "status": st,
                                "phase": h.get("phase"), "source": source.get(d)})
    return out


def rs7_measure_watch(en: dict, trigger_days: dict, P: Prices) -> dict:
    """trigger_days = {ticker: [cal index ของวันที่มีสถานะซื้อ (ก่อน dedupe)]}"""
    out = dict(en)
    k0 = P.cal_pos.get(en["date"])
    last_k = len(P.cal) - 1
    hits = [k for k in trigger_days.get(en["ticker"], []) if k0 < k <= k0 + RS7_CONVERT_DAYS]
    if hits:
        out["converted"] = True
    elif k0 + RS7_CONVERT_DAYS <= last_k:
        out["converted"] = False
    else:
        out["converted"] = None                                     # ยังไม่ครบ 20 วัน
    x, k = P.get(en["ticker"], en["date"])
    n = RS7_CONVERT_DAYS
    out["r20"] = _r((x["c"][k + n] / x["c"][k] - 1) * 100) if x is not None and k + n < len(x["c"]) else None
    out["set20"] = _r(P.set_ret(en["date"], n)) if out["r20"] is not None else None
    return out


def rs7_watch_group(ens: list[dict]) -> dict:
    done = [e for e in ens if e["converted"] is not None]
    rdone = [e for e in ens if e.get("r20") is not None]
    return {"n_entries": len(ens), "converted_pending": len(ens) - len(done),
            "converted_pct": _r(sum(1 for e in done if e["converted"]) / len(done) * 100, 1) if done else None,
            "converted_n": len(done),
            "d20": horizon_stats([e["r20"] for e in rdone], [e.get("set20") for e in rdone], pending=len(ens) - len(rdone)),
            "small_sample": len(done) < SMALL_SAMPLE}


# ─────────────────────────────────────────────────────────── Luk
def luk_measure_long(ev: dict, P: Prices) -> dict:
    """ev: date, symbol, setup, regime, close_hits, entry_trigger, stop_price"""
    out = dict(ev)
    x, k = P.get(ev["symbol"], ev["date"])
    if x is None:
        out["no_price"] = True
        return out
    o, h, l, c = x["o"], x["h"], x["l"], x["c"]
    if k + 1 >= len(c):
        out["triggered"] = None                                     # ยังไม่มีวันถัดไป
        return out
    f = c[k] / ev["close_hits"] if ev.get("close_hits") else 1.0     # ปรับ trigger/stop ตามราคาปรับปันผล
    trig, stop = ev["entry_trigger"] * f, ev["stop_price"] * f
    e_i = k + 1
    trig_hit = h[e_i] >= trig - 1e-9
    out["triggered"] = bool(trig_hit)
    if not trig_hit:
        return out
    entry = max(o[e_i], trig)
    out["entry"] = _r(entry, 3)
    out["gap_above_trigger"] = bool(o[e_i] > trig)
    out["stopped_entry_day"] = bool(l[e_i] <= stop)
    out["r_close"] = _r((c[e_i] / entry - 1) * 100)
    for n in LUK_HOLD:
        out[f"r{n}"] = _r((c[e_i + n] / entry - 1) * 100) if e_i + n < len(c) else None
    # ใช้ stop
    exit_px = None
    if out["stopped_entry_day"]:
        exit_px = stop
    else:
        for j in range(e_i + 1, min(e_i + LUK_STOP_DAYS, len(c) - 1) + 1):
            if o[j] < stop:
                exit_px = o[j]
                break
            if l[j] <= stop:
                exit_px = stop
                break
        if exit_px is None and e_i + LUK_STOP_DAYS < len(c):
            exit_px = c[e_i + LUK_STOP_DAYS]
    out["r_stop"] = _r((exit_px / entry - 1) * 100) if exit_px is not None else None
    w = LUK_STOP_DAYS
    if e_i + w < len(c):
        out["mfe"] = _r((h[e_i + 1:e_i + w + 1].max() / entry - 1) * 100)
        out["mae"] = _r((l[e_i + 1:e_i + w + 1].min() / entry - 1) * 100)
    else:
        out["mfe"] = out["mae"] = None
    return out


def luk_long_group(evs: list[dict]) -> dict:
    known = [e for e in evs if e.get("triggered") is not None]
    trig = [e for e in known if e["triggered"]]
    out = {"n_events": len(evs), "pending": len(evs) - len(known), "triggered": len(trig),
           "trigger_pct": _r(len(trig) / len(known) * 100, 1) if known else None,
           "stopped_entry_day": sum(1 for e in trig if e.get("stopped_entry_day")),
           "stopped_entry_day_pct": _r(sum(1 for e in trig if e.get("stopped_entry_day")) / len(trig) * 100, 1) if trig else None}
    for key in ("r_close", "r5", "r10", "r_stop"):
        done = [e[key] for e in trig if e.get(key) is not None]
        out[key] = horizon_stats(done, pending=len(trig) - len(done))
    out["mfe_avg"] = _avg([e.get("mfe") for e in trig])
    out["mae_avg"] = _avg([e.get("mae") for e in trig])
    out["small_sample"] = out["r10"]["n"] < SMALL_SAMPLE
    return out


def luk_measure_weak(ev: dict, P: Prices) -> dict:
    out = dict(ev)
    x, k = P.get(ev["symbol"], ev["date"])
    if x is None:
        out["no_price"] = True
        return out
    c = x["c"]
    for n in WEAK_HORIZONS:
        out[f"r{n}"] = _r((c[k + n] / c[k] - 1) * 100) if k + n < len(c) else None
        out[f"set{n}"] = _r(P.set_ret(ev["date"], n)) if out[f"r{n}"] is not None else None
    return out


def luk_weak_group(evs: list[dict]) -> dict:
    out = {"n_events": len(evs)}
    for n in WEAK_HORIZONS:
        done = [e for e in evs if e.get(f"r{n}") is not None]
        out[f"d{n}"] = horizon_stats([e[f"r{n}"] for e in done], [e.get(f"set{n}") for e in done],
                                     pending=len(evs) - len(done))
    d5 = [e for e in evs if e.get("r5") is not None and e.get("set5") is not None]
    out["underperform_5_pct"] = _r(sum(1 for e in d5 if e["r5"] < e["set5"]) / len(d5) * 100, 1) if d5 else None
    out["small_sample"] = out["d5"]["n"] < SMALL_SAMPLE
    return out


def _groups(evs: list[dict], key, fn, order=None) -> list[dict]:
    keys = order if order is not None else sorted({e[key] for e in evs if e.get(key) is not None}, key=str)
    return [{key: k, **fn([e for e in evs if e.get(key) == k])} for k in keys if any(e.get(key) == k for e in evs)]


# ─────────────────────────────────────────────────────────── build
def build(history_dir: Path, set_index_file: Path, stockdesk: Path | None, today_dir: Path | None, log=print) -> dict:
    t0 = time.time()
    warnings: list[str] = []
    out = {"generated_at": datetime.now(BKK).isoformat(timespec="seconds"), "as_of": None, "sources": {},
           "definitions": DEFINITIONS, "small_sample_n": SMALL_SAMPLE, "rs7": {}, "luk": {}, "warnings": warnings}
    if stockdesk is None:
        warnings.append("ไม่พบ stockdesk (data/history, data/backfill) — ใช้ได้แค่ไฟล์ hits ของวันนี้")
    bf_dir = stockdesk / "data" / "backfill" if stockdesk else None
    scans = {}
    for name, hits, bf in (("rs7", "rs7_hits", "rs7_hits_backfill.json"), ("luk", "luk_hits", "luk_hits_backfill.json")):
        live = load_live(stockdesk, today_dir, hits)
        b = _load(bf_dir / bf) if bf_dir else None
        if b is None:
            warnings.append(f"{name}: ไม่มีไฟล์ backfill ({bf}) — ใช้เฉพาะของจริง")
        fields = (b or {}).get("fields") or next((h.get("fields") for h in live.values() if h.get("fields")), None)
        days, source = merge_days((b or {}).get("days") or {}, live)
        scans[name] = (days, source, fields)
        out["sources"][name] = source_ranges(source)
    tickers = set()
    for name, (days, _, fields) in scans.items():
        if not fields:
            continue
        ti = fields.index("ticker" if name == "rs7" else "symbol")
        for h in days.values():
            tickers.update(r[ti] for r in h.get("rows", []))
    try:
        P = load_prices(history_dir, set_index_file, tickers)
    except Exception as e:  # noqa: BLE001
        warnings.append(f"อ่านราคาไม่ได้ ({type(e).__name__}: {e}) — ไม่มีผล")
        return out
    out["as_of"] = P.cal[-1] if P.cal else None

    # ── RS-7
    try:
        days, source, fields = scans["rs7"]
        if fields:
            buys = [rs7_measure_buy(e, P) for e in rs7_buy_events(days, fields, P.cal_pos, source)]
            buys = [e for e in buys if not e.get("no_price")]
            trig_days: dict = {}
            for d, h in days.items():
                k = P.cal_pos.get(d)
                for row in h.get("rows", []):
                    r = row_dict(fields, row)
                    if k is not None and any(st in RS7_TRIGGERS for _, st in r.get("patterns") or []):
                        trig_days.setdefault(r["ticker"], []).append(k)
            watch = [rs7_measure_watch(e, trig_days, P) for e in rs7_watch_entries(days, fields, P.cal_pos, source)]
            out["rs7"] = {
                "buy": {"all": rs7_group(buys),
                        "by_pattern": [{"name": PATTERN_NAMES.get(g["pattern"]), **g} for g in _groups(buys, "pattern", rs7_group)],
                        "by_phase": _groups(buys, "phase", rs7_group, order=["A", "B", "C", "neutral"])},
                "watch": {"all": rs7_watch_group(watch),
                          "by_status": _groups(watch, "status", rs7_watch_group, order=list(RS7_WATCH)),
                          "by_pattern": [{"name": PATTERN_NAMES.get(g["pattern"]), **g} for g in _groups(watch, "pattern", rs7_watch_group)]},
                "recent": [{k: e.get(k) for k in ("date", "ticker", "pattern", "status", "phase", "entry", "r5", "r10", "r20", "source")}
                           for e in sorted(buys, key=lambda e: (e["date"], e["ticker"]), reverse=True)[:RECENT_N]],
            }
    except Exception as e:  # noqa: BLE001
        warnings.append(f"rs7: ({type(e).__name__}: {e})")
        out["rs7"] = {}

    # ── Luk
    try:
        days, source, fields = scans["luk"]
        if fields:
            longs, weaks = [], []
            for d, h in days.items():
                mode = (h.get("regime") or {}).get("mode")
                for row in h.get("rows", []):
                    r = row_dict(fields, row)
                    base = {"date": d, "symbol": r["symbol"], "setup": r["setup"], "regime": mode,
                            "close_hits": r.get("close"), "source": source.get(d)}
                    if r.get("side") == "long" and r.get("entry_trigger") and r.get("stop_price"):
                        longs.append(luk_measure_long({**base, "entry_trigger": r["entry_trigger"], "stop_price": r["stop_price"]}, P))
                    elif r.get("side") == "weak":
                        weaks.append(luk_measure_weak(base, P))
            longs = [e for e in longs if not e.get("no_price")]
            weaks = [e for e in weaks if not e.get("no_price")]
            out["luk"] = {
                "long": {"all": luk_long_group(longs),
                         "by_setup": _groups(longs, "setup", luk_long_group, order=["LONG_PULLBACK", "EPISODIC_PIVOT"]),
                         "by_regime": _groups(longs, "regime", luk_long_group, order=["LONG", "MIXED", "DEFENSIVE", "SHORT"])},
                "weak": {"by_setup": _groups(weaks, "setup", luk_weak_group, order=["SHORT_WEAK_BOUNCE", "PARABOLIC_SHORT"])},
                "recent": [{k: e.get(k) for k in ("date", "symbol", "setup", "regime", "triggered", "entry", "stopped_entry_day",
                                                  "r_close", "r5", "r10", "r_stop", "source")}
                           for e in sorted(longs, key=lambda e: (e["date"], e["symbol"]), reverse=True)[:RECENT_N]],
            }
    except Exception as e:  # noqa: BLE001
        warnings.append(f"luk: ({type(e).__name__}: {e})")
        out["luk"] = {}
    log(f"  RC events: as_of {out['as_of']} · rs7 buy {((out['rs7'].get('buy') or {}).get('all') or {}).get('n_events')} · "
        f"luk long {((out['luk'].get('long') or {}).get('all') or {}).get('n_events')} · {time.time() - t0:.0f}s")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Report card (events) for RS-7 + Martin Luk → report_card_events.json")
    ap.add_argument("--history-dir", type=Path, default=HISTORY_DIR)
    ap.add_argument("--set-index", type=Path, default=None, help="default <history-dir>/SET_INDEX.csv")
    ap.add_argument("--stockdesk-dir", type=Path, default=None, help="default stockdesk_repo (CI) / sibling checkout")
    ap.add_argument("--today-dir", type=Path, default=RESULTS_DIR / "output", help="ไฟล์ hits ของวันนี้ (ยังไม่ copy)")
    ap.add_argument("--out", type=Path, default=OUT_FILE)
    a = ap.parse_args(argv)
    print("📒 [RC events] คำนวณ Report Card แบบเหตุการณ์ (RS-7 + Martin Luk)...")
    try:
        out = build(a.history_dir, a.set_index or a.history_dir / "SET_INDEX.csv", find_stockdesk(a.stockdesk_dir), a.today_dir)
    except Exception as e:  # noqa: BLE001
        out = {"generated_at": datetime.now(BKK).isoformat(timespec="seconds"), "as_of": None, "sources": {},
               "definitions": DEFINITIONS, "small_sample_n": SMALL_SAMPLE, "rs7": {}, "luk": {},
               "warnings": [f"report_card_events ล้ม: {type(e).__name__}: {e}"]}
    for w in out["warnings"]:
        print(f"  ⚠️ {w}")
    try:
        write_json(out, a.out)
    except Exception as e:  # noqa: BLE001
        print(f"❌ [RC events] เขียน {a.out} ไม่ได้: {e}")
        return 1
    print(f"✅ [RC events] {a.out} ({a.out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
