"""backfill_hits.py — rs7_hits / luk_hits ย้อนหลังแบบ point-in-time (ทำครั้งเดียวผ่าน workflow backfill-hits.yml)

เขียน 2 ไฟล์ (ไม่แตะ data/history / data/scans ของ stockdesk):
  <out-dir>/rs7_hits_backfill.json   {generated_at, kind, config, from, to, n_days, fields, days: {date: {phase, rows}}}
  <out-dir>/luk_hits_backfill.json   {generated_at, kind, config, from, to, n_days, fields, days: {date: {regime, rows}}}
แถวมีรูปเดียวกับ rs7_hits.json / luk_hits.json รายวัน (fields เดียวกัน)

point-in-time: ทุกวันใช้แท่ง ≤ วันนั้นเท่านั้น —
  RS-7: stock_frame / index_features เป็น rolling/shift (เหตุ-ผลไปข้างหน้า) · FTD state machine เดินไปข้างหน้า ·
        RS Rating รายวัน = rs_rating_history (rs_score ตัวเดียวกับ 3_calculate_rs บน series ที่ตัดถึงวันนั้น)
  Luk:  stock_arrays (indicators) เป็น EMA/rolling/shift · ทีละวันเรียก scan_luk.evaluate_day ตัวเดียวกับสแกนจริง ·
        ผู้นำใช้ RS Rating ของวันนั้นจาก rs_rating_history
ข้อจำกัด: ราคาเป็นราคาปรับปันผล ณ วันที่รัน · universe = ไฟล์ราคาที่มีตอนรัน (หุ้นที่หายไปก่อนหน้าไม่ถูกนับ)
ไม่ดึงข้อมูลภายนอก · รันซ้ำได้ (เขียนทับ)
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE.parents[1]), str(_HERE)]
from config import HISTORY_DIR, RESULTS_DIR  # noqa: E402
import scan_luk as L  # noqa: E402
import scan_rs7 as R  # noqa: E402
from luk_config import LUK_CONFIG  # noqa: E402
from rs7_config import RS7_CONFIG  # noqa: E402
from utils import iter_stock_files, load_fund_tickers  # noqa: E402

OUT_DIR = RESULTS_DIR / "backfill"
RS7_FILE = "rs7_hits_backfill.json"
LUK_FILE = "luk_hits_backfill.json"
NOTE = ("สร้างย้อนหลังด้วยกติกาปัจจุบันแบบ point-in-time (ทุกวันใช้ข้อมูลถึงวันนั้น) ไม่ใช่สิ่งที่หน้าเว็บแสดงในวันนั้น · "
        "ราคาเป็นราคาปรับปันผล ณ วันที่สร้าง · universe = หุ้นที่มีไฟล์ราคาตอนสร้าง")


def _d(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def load_inputs(history_dir: Path, set_index_file: Path, funds: set) -> dict:
    """อ่านครั้งเดียวสำหรับทั้ง 2 สแกน: SET · ไฟล์หุ้นแบบ scan_rs7 (read_stock) · Close แบบ rs_score (รวมกองทุน)"""
    set_df = R.read_set_index(set_index_file)
    frames, closes = {}, {}
    for fp in iter_stock_files(history_dir):
        t = fp.stem
        try:
            df, cl = R.read_stock(fp)
        except Exception:  # noqa: BLE001
            continue
        if cl is not None:
            c2 = cl.dropna()
            c2 = c2[~pd.isna(c2.index)]
            if len(c2):
                closes[t] = c2
        if df is not None and t.upper() not in funds:
            frames[t] = df
    return {"set_df": set_df, "frames": frames, "closes": closes}


def backfill_rs7(inp: dict, funds: set, dates: list, rsr: pd.DataFrame, cfg: dict = RS7_CONFIG, log=print) -> dict:
    set_df = inp["set_df"]
    f = R.index_features(set_df, cfg)
    days = {_d(d): [] for d in dates}
    first = dates[0]
    for t, df in inp["frames"].items():
        if len(df) < cfg["min_bars"]:
            continue
        try:
            ev = int(np.searchsorted(df.index.to_numpy(), np.datetime64(first)))
            rr = rsr[t] if t in rsr.columns else pd.Series(dtype=float)
            o = R.stock_frame(df, f, rr, cfg, eval_from=ev)
        except Exception as e:  # noqa: BLE001
            log(f"  rs7 {t}: {type(e).__name__}")
            continue
        pos = {d: k for k, d in enumerate(o.index)}
        for d in dates:
            k = pos.get(d)
            if k is None:
                continue
            row = R.hit_row(t, o, k)
            if row:
                days[_d(d)].append(row)
    phase = f["phase"]
    return {_d(d): {"phase": {"A": "A", "B": "B", "C": "C"}.get(phase.get(d), "neutral"),
                    "rows": R.hit_rows_sorted(days[_d(d)])} for d in dates}


def backfill_luk(inp: dict, funds: set, dates: list, rsr: pd.DataFrame, t2s: dict, cfg: dict = LUK_CONFIG) -> dict:
    set_df = inp["set_df"]
    cal = set_df.index
    arrays, pos = {}, {}
    for t, df in inp["frames"].items():
        df = df[df.index.isin(cal)]                         # เหมือน scan_luk.build: เฉพาะวันทำการของ SET
        if len(df) < cfg["min_bars"]:
            continue
        arrays[t] = L.stock_arrays(df, cfg)
        pos[t] = {d: k for k, d in enumerate(df.index)}
    out = {}
    sc = set_df["Close"]
    for d in dates:
        day_arrays = {t: (x, pos[t][d]) for t, x in arrays.items()
                      if d in pos[t] and pos[t][d] + 1 >= cfg["min_bars"]}
        row = rsr.loc[d] if d in rsr.index else pd.Series(dtype=float)
        rs = {t: int(v) for t, v in row.items() if pd.notna(v)}
        day = L.evaluate_day(day_arrays, rs, sc[sc.index <= d], t2s, cfg)
        h = L.hits_from({**day, "generated_at": None, "as_of": _d(d)})
        out[_d(d)] = {"regime": h["regime"], "rows": h["rows"]}
    return out


def _wrap(kind: str, cfg: dict, fields: list, days: dict, now: str) -> dict:
    keys = sorted(days)
    return {"generated_at": now, "kind": kind, "note": NOTE, "config": cfg, "from": keys[0] if keys else None,
            "to": keys[-1] if keys else None, "n_days": len(keys), "fields": fields, "days": days}


def run(history_dir: Path, set_index_file: Path, out_dir: Path, date_from: str | None = None,
        date_to: str | None = None, only: str | None = None, log=print) -> dict:
    t0 = time.time()
    funds = load_fund_tickers()
    t2s = L.load_sector_map()
    inp = load_inputs(history_dir, set_index_file, funds)
    set_df = inp["set_df"]
    if date_to:
        set_df = set_df[set_df.index <= date_to]
        inp["set_df"] = set_df
        inp["frames"] = {t: df[df.index <= date_to] for t, df in inp["frames"].items()}
        inp["closes"] = {t: s[s.index <= date_to] for t, s in inp["closes"].items()}
    cal = list(set_df.index)
    # RS-7 ต้องมี SET ครบ idx_high_win แท่ง · Luk เริ่มได้เมื่อมี RS Rating (≥ 126 แท่ง rs_score.MIN_DAYS)
    rs7_start = cal[min(len(cal) - 1, RS7_CONFIG["idx_high_win"])]
    luk_start = cal[min(len(cal) - 1, 126)]
    if date_from:
        rs7_start = max(rs7_start, pd.Timestamp(date_from))
        luk_start = max(luk_start, pd.Timestamp(date_from))
    first = min(rs7_start, luk_start) if only is None else (rs7_start if only == "rs7" else luk_start)
    rs_dates = [d for d in cal if d >= first]
    rsr = R.rs_rating_history(inp["closes"], funds, rs_dates)
    log(f"  backfill: {len(inp['frames'])} stocks · RS history {len(rs_dates)} days · {time.time() - t0:.0f}s")
    now = datetime.now(R.BKK).isoformat(timespec="seconds")
    out_dir.mkdir(parents=True, exist_ok=True)
    res = {}
    if only in (None, "rs7"):
        days = backfill_rs7(inp, funds, [d for d in cal if d >= rs7_start], rsr, log=log)
        res["rs7"] = _wrap("rs7_hits_backfill", RS7_CONFIG, R.HITS_FIELDS, days, now)
        R.write_json(res["rs7"], out_dir / RS7_FILE)
        log(f"  rs7: {len(days)} days · {sum(len(v['rows']) for v in days.values())} rows · {time.time() - t0:.0f}s")
    if only in (None, "luk"):
        days = backfill_luk(inp, funds, [d for d in cal if d >= luk_start], rsr, t2s)
        fields = L.hits_from({"generated_at": None, "as_of": None})["fields"]
        res["luk"] = _wrap("luk_hits_backfill", LUK_CONFIG, fields, days, now)
        L.write_json(res["luk"], out_dir / LUK_FILE)
        log(f"  luk: {len(days)} days · {sum(len(v['rows']) for v in days.values())} rows · {time.time() - t0:.0f}s")
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="rs7_hits / luk_hits backfill (point-in-time)")
    ap.add_argument("--history-dir", type=Path, default=HISTORY_DIR)
    ap.add_argument("--set-index", type=Path, default=None, help="default <history-dir>/SET_INDEX.csv")
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    ap.add_argument("--from", dest="date_from", default=None)
    ap.add_argument("--to", dest="date_to", default=None)
    ap.add_argument("--only", choices=["rs7", "luk"], default=None)
    a = ap.parse_args(argv)
    print("⏪ [backfill] สร้าง rs7_hits / luk_hits ย้อนหลัง...")
    run(a.history_dir, a.set_index or a.history_dir / "SET_INDEX.csv", a.out_dir, a.date_from, a.date_to, a.only)
    print(f"✅ [backfill] {a.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
