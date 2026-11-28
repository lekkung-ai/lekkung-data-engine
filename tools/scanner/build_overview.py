"""build_overview.py — Overview v2 (หน้า / ของ stockdesk) → data/results/output/overview.json

ส่วนในไฟล์: light (ไฟสัญญาณ 3 ข้อ) · volume (มูลค่า universe เทียบเฉลี่ย 50 วัน) · groups (Strength/Weakness
รายกลุ่มเทียบ SET) · leaders (หุ้นผู้นำ) · scanner_delta (จำนวนสแกนเทียบวันทำการก่อนหน้า)
เกณฑ์ทุกตัวอยู่ใน overview_config.py · ผลสืบ/ต้นแบบ: stockdesk/scratch/overview_2026-11-28/

ใช้ของเดิมของระบบ (import ไม่เขียนซ้ำ):
  EMA / มูลค่าเฉลี่ย / perf / วอลุ่มเฉลี่ยก่อนหน้า → tools/core/indicators.py
  ADTV 50 วัน → utils.calculate_adtv · กองทุน/REIT → utils.is_fund_ticker · ไฟล์ราคา → utils.iter_stock_files
  high 52 สัปดาห์ → คอลัมน์ 52W_High ในไฟล์ราคา (2_download_history.add_indicator_columns)
  สภาพตลาด + % เหนือ SMA50/200 → breadth.json (การ์ดสภาพตลาดเดิมอ่านจากไฟล์เดียวกัน)
  ช่วงดัชนี A/B/C + FTD → scan_rs7.index_features (FTD = calculate_breadth.compute_ftd_dd_detail)
  วันทำการก่อนหน้า → tools/core/set_holidays.previous_trading_day (กรองเสาร์–อาทิตย์ + วันหยุด SET)

รันเป็น step แยกใน daily-scan.yml (continue-on-error) ไม่อยู่ใน run_all.py → ไม่เข้า failed_steps
ส่วนไหนพัง ส่วนนั้นเป็น null + errors[] · ไฟล์เขียนแบบ atomic (tmp → rename) · exit 0 เสมอ ยกเว้นเขียนไฟล์ไม่ได้
อ่านไฟล์ในเครื่องเท่านั้น ไม่ดึงข้อมูลภายนอก

ย้อนหลัง (point-in-time): --as-of YYYY-MM-DD --today-dir <stockdesk>/data/history/<วันนั้น>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
sys.path[:0] = [str(_ROOT), str(_HERE)]
from config import HISTORY_DIR, RESULTS_DIR  # noqa: E402
from tools.core.indicators import ema, perf_pct, value_avg, volume_avg_prior  # noqa: E402
from tools.core.set_holidays import previous_trading_day  # noqa: E402
import overview_config as C  # noqa: E402
from scan_rs7 import index_features, read_set_index  # noqa: E402
from utils import SECTOR_MAP_CANDIDATES, calculate_adtv, is_fund_ticker, iter_stock_files, load_full_df  # noqa: E402

OUT_FILE = RESULTS_DIR / "output" / "overview.json"
TODAY_DIR = RESULTS_DIR / "output"          # ไฟล์ scan ของรอบนี้ (ก่อน copy ไป stockdesk)
STOCKDESK_DIR = _ROOT / "stockdesk_repo"    # CI: checkout ไว้แล้วก่อน step นี้
BKK = timezone(timedelta(hours=7))
TH_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


# ─────────────────────────────────────────────────────────── helpers
def _r(x, nd=2):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return round(x, nd) if np.isfinite(x) else None


def _d(ts) -> str | None:
    return None if ts is None or pd.isna(ts) else pd.Timestamp(ts).strftime("%Y-%m-%d")


def th_date(ts) -> str:
    if ts is None or pd.isna(ts):
        return "ไม่มี"
    t = pd.Timestamp(ts)
    return f"{t.day} {TH_MONTHS[t.month - 1]}"


def read_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def rows_of(d) -> list:
    """combined.json เป็น {data: [...]} · ไฟล์ scan อื่นเป็น list"""
    if isinstance(d, dict):
        return d.get("data") or []
    return d if isinstance(d, list) else []


def load_sector_map(explicit: Path | None, stockdesk_dir: Path) -> dict:
    cands = ([explicit] if explicit else []) + list(SECTOR_MAP_CANDIDATES) + \
        [stockdesk_dir / "data" / "scans" / "sector_map.json"]
    path = next((p for p in cands if p and p.exists()), None)
    if path is None:
        raise FileNotFoundError("sector_map.json ไม่พบ")
    return read_json(path).get("ticker_to_sector", {})


def group_of(t: str, t2s: dict) -> tuple[str | None, str | None]:
    """(ชื่อกลุ่ม, ตลาด) · SET = subsector · mai = "mai · {sector}" · กองทุน/REIT/warrant/DW/ไม่มีใน map = None"""
    if t.upper().endswith(C.NON_COMMON_SUFFIXES) or is_fund_ticker(t, t2s):
        return None, None
    info = t2s.get(t) or t2s.get(t.upper())
    if not isinstance(info, dict):
        return None, None
    if str(info.get("market", "")).upper() == "MAI":
        return (f"mai · {info['sector']}" if info.get("sector") else None), "mai"
    return (info.get("subsector") or None), "SET"


# ─────────────────────────────────────────────────────────── loading
def read_price(fp: Path) -> pd.DataFrame | None:
    full = load_full_df(fp)
    dates = pd.to_datetime(full.iloc[:, 0], errors="coerce")
    df = pd.DataFrame(index=pd.DatetimeIndex(dates))
    for col in ("Close", "High", "Volume", "52W_High"):
        df[col] = pd.to_numeric(full[col], errors="coerce").values if col in full.columns else np.nan
    df = df[~pd.isna(df.index)].dropna(subset=["Close"])
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df["Volume"] = df["Volume"].fillna(0)
    return df


def stock_metrics(df: pd.DataFrame, as_of: pd.Timestamp) -> dict | None:
    """ค่าของหุ้น ณ as_of (ใช้ข้อมูลถึง as_of เท่านั้น) · ไม่มีแท่งวัน as_of → None"""
    df = df[df.index <= as_of]
    if len(df) < 2 or df.index[-1] != as_of:
        return None
    c, v = df["Close"], df["Volume"]
    e10 = ema(c, C.LEADER["ema_n"]).iat[-1]
    prior = volume_avg_prior(v, 50).iat[-1]
    return {
        "close": float(c.iat[-1]),
        "chg": float(perf_pct(c, 1).iat[-1]),
        "ema10": float(e10),
        "val20": float(value_avg(c, v, C.GROUP["val_n"]).iat[-1]) / 1e6,
        "adtv50": float(calculate_adtv(c, v, 50)),
        "perf5": float(perf_pct(c, C.GROUP["perf_n"]).iat[-1]),
        "vol": float(v.iat[-1]),
        "volx50": float(v.iat[-1] / prior) if prior and prior > 0 else None,
        "hi52": float(df["52W_High"].iat[-1]) if pd.notna(df["52W_High"].iat[-1]) else None,
    }


# ─────────────────────────────────────────────────────────── sections
def build_leaders(m: dict, stage: dict, rs: dict, t2s: dict, pp: dict) -> dict:
    L = C.LEADER
    rows = []
    for t, x in m.items():
        if is_fund_ticker(t, t2s) or t.upper().endswith(C.NON_COMMON_SUFFIXES):
            continue
        if stage.get(t) not in L["stages"] or (rs.get(t) or 0) < L["rs_min"]:
            continue
        if x["adtv50"] < L["adtv50_min_mb"] or x["close"] < L["price_min"] or not x["hi52"]:
            continue
        dist = (x["close"] / x["hi52"] - 1) * 100
        if dist < -L["max_below_high_pct"]:
            continue
        if x["close"] < x["ema10"]:
            tag = "below_ema10"
        elif x["chg"] >= L["tag_up_pct"]:
            tag = "up"
        elif x["chg"] < 0:
            tag = "rest"
        else:
            tag = None
        rows.append({"t": t, "grp": group_of(t, t2s)[0], "stage": stage[t], "rs": int(rs[t]),
                     "close": _r(x["close"], 2), "dist_high": _r(dist), "chg": _r(x["chg"]),
                     "ema10_dist": _r((x["close"] / x["ema10"] - 1) * 100), "volx50": _r(x["volx50"]),
                     "adtv50": _r(x["adtv50"], 1), "tag": tag, "pp": pp.get(t)})
    rows.sort(key=lambda r: (-r["rs"], -(r["dist_high"] if r["dist_high"] is not None else -1e9), r["t"]))
    return {"total": len(rows), "criteria": L, "rows": rows}


def build_groups(m: dict, t2s: dict, set_chg: float, set_perf5: float, leader_set: set) -> dict:
    G = C.GROUP
    members: dict[str, list] = {}
    market: dict[str, str] = {}
    for t, x in m.items():
        g, mk = group_of(t, t2s)
        if g is None or not np.isfinite(x["chg"]):
            continue
        if x["val20"] < G["member_val20_min_mb"] or (G["member_need_volume"] and x["vol"] <= 0):
            continue
        members.setdefault(g, []).append((t, x))
        market[g] = mk
    rows, skipped = [], []
    for g, xs in members.items():
        if len(xs) < G["min_members"]:
            skipped.append({"g": g, "market": market[g], "n": len(xs)})
            continue
        chg = [x["chg"] for _, x in xs]
        p5 = [x["perf5"] for _, x in xs if np.isfinite(x["perf5"])]
        med = median(chg)
        up, down = sum(c > 0 for c in chg), sum(c < 0 for c in chg)
        vs = med - set_chg
        if vs >= G["vs_set_min"] and up / len(xs) >= G["share_min"]:
            status = "strength"
        elif vs <= -G["vs_set_min"] and down / len(xs) >= G["share_min"]:
            status = "weakness"
        else:
            status = None
        srt = sorted(xs, key=lambda p: (-p[1]["chg"], p[0]))
        cell = lambda p: {"t": p[0], "chg": _r(p[1]["chg"]), "leader": p[0] in leader_set}  # noqa: E731
        rows.append({"g": g, "market": market[g], "n": len(xs), "med": _r(med), "med_vs_set": _r(vs),
                     "up": up, "down": down,
                     "med5_vs_set": _r(median(p5) - set_perf5) if p5 and np.isfinite(set_perf5) else None,
                     "status": status, "top4": [cell(p) for p in srt[:G["top_n"]]],
                     # bot4 ไม่ซ้ำ top4 (กลุ่มเล็ก < 8 ตัว → bot4 สั้นลง)
                     "bot4": [cell(p) for p in srt[::-1][:max(0, min(G["top_n"], len(xs) - G["top_n"]))]]})
    rows.sort(key=lambda r: (-r["med_vs_set"], r["g"]))
    skipped.sort(key=lambda r: (-r["n"], r["g"]))
    return {"filter": {k: G[k] for k in ("member_val20_min_mb", "member_need_volume", "min_members")},
            "criteria": {k: G[k] for k in ("vs_set_min", "share_min", "perf_n")},
            "set_chg": _r(set_chg), "set_perf5": _r(set_perf5),
            "counts": {"strength": sum(r["status"] == "strength" for r in rows),
                       "weakness": sum(r["status"] == "weakness" for r in rows), "ranked": len(rows)},
            "rows": rows, "near": near_groups(rows), "skipped": skipped}


def near_groups(rows: list) -> list:
    """กลุ่มที่ผ่านเงื่อนไขหนึ่ง อีกเงื่อนไขขาดนิดเดียว · ฝั่งละไม่เกิน near_max · missing = เงื่อนไขที่ขาด"""
    G = C.GROUP
    out = []
    for side, sign, key in (("strength", 1, "up"), ("weakness", -1, "down")):
        cands = []
        for r in rows:
            if r["status"] is not None:
                continue
            vs, share = sign * r["med_vs_set"], r[key] / r["n"]
            vs_ok, share_ok = vs >= G["vs_set_min"], share >= G["share_min"]
            if vs_ok and not share_ok and G["share_min"] - share <= G["near_share_gap"]:
                cands.append((G["share_min"] - share, {"missing": "share", "need": G["share_min"]}, r, share))
            elif share_ok and not vs_ok and G["vs_set_min"] - vs <= G["near_vs_set_gap"]:
                cands.append(((G["vs_set_min"] - vs) / G["vs_set_min"] * G["near_share_gap"],
                              {"missing": "med_vs_set", "need": sign * G["vs_set_min"]}, r, share))
        cands.sort(key=lambda c: (c[0], c[2]["g"]))
        for _, miss, r, share in cands[:G["near_max"]]:
            out.append({"g": r["g"], "side": side, "med_vs_set": r["med_vs_set"], "share": _r(share, 3), **miss})
    return out


def build_light(set_df: pd.DataFrame, as_of: pd.Timestamp, breadth: dict, warnings: list) -> dict:
    L = C.LIGHT
    ms = breadth.get("market_stage") or {}
    stage, dd = ms.get("stage"), ms.get("dd_count_25d")
    rows = breadth.get("breadth") or []
    last = rows[-1] if rows else {}
    if last.get("date") != _d(as_of):
        warnings.append(f"breadth.json แถวล่าสุด {last.get('date')} ≠ as_of {_d(as_of)}")
    ma50, ma200 = last.get("pct_above_ma50"), last.get("pct_above_ma200")

    f = index_features(set_df[set_df.index <= as_of])
    fr = f.iloc[-1]
    ftd_dates = f.index[f["ftd"].values]
    last_ftd = ftd_dates[-1] if len(ftd_dates) else None
    bars = fr["bars_since_ftd"]
    ftd_within = bool(np.isfinite(bars) and bars <= L["ftd_within_days"] - 1)
    set_chg = float(perf_pct(set_df["Close"], 1).loc[as_of])

    a_pass = stage == L["stage_ok"]
    b_pass = fr["phase"] == L["phase_ok"]
    c_pass = ftd_within or (ma50 is not None and ma50 >= L["ma50_min_pct"])
    n_pass = int(a_pass) + int(b_pass) + int(c_pass)
    color = C.LIGHT_COLOR[n_pass]
    v = {"set_chg": f"{set_chg:+.2f}%", "ma50": f"{ma50:.0f}" if ma50 is not None else "?",
         "ma200": f"{ma200:.0f}" if ma200 is not None else "?", "stage": stage or "?",
         "stage_th": C.STAGE_TH.get(stage, stage or "?"), "dd": dd if dd is not None else "?",
         "phase": fr["phase"], "phase_th": C.PHASE_TH.get(fr["phase"], fr["phase"]),
         "idx_dd": f"{fr['idx_dd'] * 100:.1f}", "rally": f"{fr['rally'] * 100:+.1f}",
         "last_ftd_th": th_date(last_ftd), "ftd_days": L["ftd_within_days"], "ma50_min": f"{L['ma50_min_pct']:.0f}",
         "n_pass": n_pass}
    keys = [("market_stage", a_pass), ("index_phase", b_pass), ("ftd_or_breadth", c_pass)]
    v["missing"] = " · ".join(C.MISSING[k].format(**v) for k, ok in keys if not ok)
    direction = "up" if set_chg >= 0 else "down"
    checks = [
        {"key": "market_stage", "pass": a_pass, "value": {"stage": stage, "dd_count_25d": dd},
         "hint": C.HINT["market_stage"].format(**v)},
        {"key": "index_phase", "pass": bool(b_pass),
         "value": {"phase": fr["phase"], "from_high_pct": _r(fr["idx_dd"] * 100),
                   "from_low_pct": _r(fr["rally"] * 100)},
         "hint": C.HINT["index_phase"].format(**v)},
        {"key": "ftd_or_breadth", "pass": bool(c_pass),
         "value": {"ftd_within": ftd_within, "last_ftd": _d(last_ftd),
                   "bars_since_ftd": int(bars) if np.isfinite(bars) else None,
                   "pct_above_ma50": ma50, "pct_above_ma200": ma200},
         "hint": C.HINT["ftd_or_breadth"].format(**v)},
    ]
    return {"color": color, "n_pass": n_pass, "set_chg": _r(set_chg), "checks": checks,
            "sentence": C.SENTENCE[(color, direction)].format(**v), "action": C.ACTION[color],
            "criteria": L}


def build_volume(set_df: pd.DataFrame, as_of: pd.Timestamp) -> dict:
    vol = set_df["Volume"][set_df.index <= as_of]
    vol = vol.where(vol > 0)                         # แท่งที่ไม่มีมูลค่า universe = ไม่นับ
    today = vol.iat[-1]
    avg = volume_avg_prior(vol.dropna(), C.VOLUME["avg_n"]).iat[-1] if pd.notna(today) else np.nan
    return {"value_bn": _r(today / 1e9), "avg50_bn": _r(avg / 1e9),
            "ratio": _r(today / avg) if pd.notna(today) and avg and avg > 0 else None, "source": "universe"}


def scan_count(key: str, d) -> int:
    rows = rows_of(d)
    if key == "dual":
        return sum(bool(r.get("sepa")) and bool(r.get("kell")) for r in rows)
    if key == "stage2":
        return sum(r.get("Stage") in ("S.Bull", "Bull") for r in rows)
    return len(rows)


def build_scanner_delta(today_dir: Path, prev_dir: Path | None, errors: list) -> dict:
    out = {}
    for key, name in C.SCANNER_FILES.items():
        try:
            n = scan_count(key, read_json(today_dir / name))
        except Exception as e:  # noqa: BLE001
            errors.append(f"scanner_delta.{key}: {type(e).__name__}: {e}")
            out[key] = [None, None]
            continue
        prev = None
        if prev_dir is not None and (prev_dir / name).exists():
            try:
                prev = scan_count(key, read_json(prev_dir / name))
            except Exception as e:  # noqa: BLE001
                errors.append(f"scanner_delta.{key} (prev): {type(e).__name__}")
        out[key] = [n, None if prev is None else n - prev]
    return out


def load_pp(path: Path, trading_days: pd.DatetimeIndex, as_of: pd.Timestamp, closes: dict) -> dict:
    """{ticker: {date, kind, price, discount_pct}} รายการ PP ล่าสุดภายใน pp_window_days วันทำการ (รวมวันนี้)
    PP = board ที่ tags มี "PP" หรือ listing ที่ source_type = "PP" · price มีเฉพาะ listing
    discount_pct = ราคา PP เทียบราคาปิดวันนี้ (%) · ไฟล์ไม่มี/อ่านไม่ได้ → raise (ผู้เรียกให้ pp = null ทุกตัว)"""
    d = read_json(path)
    days = trading_days[trading_days <= as_of]
    start = days[-C.LEADER["pp_window_days"]] if len(days) >= C.LEADER["pp_window_days"] else days[0]
    out: dict[str, dict] = {}
    for e in d.get("events") or []:
        is_pp = ((e.get("kind") == "board" and "PP" in (e.get("tags") or []))
                 or (e.get("kind") == "listing" and e.get("source_type") == "PP"))
        if not is_pp or not e.get("ticker") or not e.get("date"):
            continue
        dt = pd.Timestamp(str(e["date"])[:10])
        if not (start <= dt <= as_of):
            continue
        t = e["ticker"]
        cur = out.get(t)
        price = e.get("price") if e.get("kind") == "listing" else None
        if cur is None or dt.strftime("%Y-%m-%d") > cur["date"]:
            keep_price = price if price is not None else (cur or {}).get("price")
            out[t] = {"date": dt.strftime("%Y-%m-%d"), "kind": e["kind"], "price": keep_price}
        elif price is not None and cur.get("price") is None:
            cur["price"] = price
    for t, p in out.items():
        c = closes.get(t)
        p["discount_pct"] = _r((p["price"] / c - 1) * 100) if p.get("price") and c else None
    return out


# ─────────────────────────────────────────────────────────── main build
def build(history_dir: Path, set_index_file: Path, today_dir: Path, stockdesk_dir: Path,
          capital_events: Path | None = None, sector_map: Path | None = None, as_of: str | None = None,
          log=print) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    out = {"generated_at": datetime.now(BKK).isoformat(timespec="seconds"), "as_of": None,
           "prev_trading_day": None, "rule_version": C.RULE_VERSION,
           "light": None, "volume": None, "groups": None, "leaders": None, "scanner_delta": None,
           "errors": errors, "warnings": warnings}

    set_df = read_set_index(set_index_file)          # พัง = ทั้งไฟล์ไม่มีความหมาย → main() เขียนไฟล์ errors
    if as_of:
        set_df = set_df[set_df.index <= pd.Timestamp(as_of)]
    day = set_df.index[-1]

    t2s: dict = {}
    try:
        t2s = load_sector_map(sector_map, stockdesk_dir)
    except Exception as e:  # noqa: BLE001
        errors.append(f"sector_map: {type(e).__name__}: {e} — ไม่มีกลุ่ม/ไม่ได้ตัดกองทุน")

    frames, bad = {}, []
    for fp in iter_stock_files(history_dir):
        try:
            frames[fp.stem] = read_price(fp)
        except Exception as e:  # noqa: BLE001
            bad.append(f"{fp.stem}:{type(e).__name__}")
    if bad:
        warnings.append(f"อ่านไฟล์ราคาไม่ได้ {len(bad)} ตัว: {', '.join(bad[:10])}")
    # วันล่าสุดของไฟล์หุ้น (ที่ไม่เกินวันของ SET_INDEX — ย้อนหลังด้วย --as-of ไม่นับแท่งหลังวันนั้น)
    last = pd.Series({t: d.index[d.index <= day][-1] for t, d in frames.items() if (d.index <= day).any()})
    if len(last):
        stock_day = last.mode().iloc[0]
        if stock_day != day:
            warnings.append(f"SET_INDEX.csv วันล่าสุด {_d(day)} ≠ ข้อมูลหุ้น {_d(stock_day)}")
            day = min(day, stock_day)
            set_df = set_df[set_df.index <= day]
    out["as_of"] = _d(day)
    prev = pd.Timestamp(previous_trading_day(day.date()))
    out["prev_trading_day"] = _d(prev)
    idx_prev = set_df.index[-2] if len(set_df) > 1 else None
    if idx_prev is not None and idx_prev != prev:
        warnings.append(f"วันทำการก่อนหน้าตาม set_holidays = {_d(prev)} แต่ SET_INDEX แท่งก่อนหน้า = {_d(idx_prev)}")

    m = {}
    for t, df in frames.items():
        try:
            x = stock_metrics(df, day)
        except Exception:  # noqa: BLE001
            x = None
        if x is not None:
            m[t] = x
    log(f"  หุ้นที่มีแท่งวัน {_d(day)}: {len(m)} / {len(frames)}")

    def section(name, fn):
        try:
            out[name] = fn()
        except Exception as e:  # noqa: BLE001
            out[name] = None
            errors.append(f"{name}: {type(e).__name__}: {e}")

    section("light", lambda: build_light(set_df, day, read_json(today_dir / "breadth.json"), warnings))
    section("volume", lambda: build_volume(set_df, day))

    def leaders():
        stage = {r["Ticker"]: r.get("Stage") for r in rows_of(read_json(today_dir / "market_stage.json"))}
        rs = {r["Ticker"]: r.get("RS_Rating") for r in rows_of(read_json(today_dir / "rs_ranking.json"))}
        pp: dict = {}
        cev = capital_events or stockdesk_dir / "public" / "data" / "capital_events.json"
        try:
            pp = load_pp(cev, set_df.index, day, {t: x["close"] for t, x in m.items()})
        except Exception as e:  # noqa: BLE001
            warnings.append(f"capital_events อ่านไม่ได้ ({type(e).__name__}) — pp = null ทุกตัว")
        return build_leaders(m, stage, rs, t2s, pp)

    section("leaders", leaders)
    leader_set = {r["t"] for r in (out["leaders"] or {}).get("rows", [])}
    set_c = set_df["Close"]
    section("groups", lambda: build_groups(m, t2s, float(perf_pct(set_c, 1).iat[-1]),
                                           float(perf_pct(set_c, C.GROUP["perf_n"]).iat[-1]), leader_set))
    prev_dir = stockdesk_dir / "data" / "history" / _d(prev)
    if not prev_dir.is_dir():
        warnings.append(f"ไม่มี stockdesk data/history/{_d(prev)} — scanner Δ = null")
        prev_dir = None
    section("scanner_delta", lambda: build_scanner_delta(today_dir, prev_dir, errors))
    return out


def write_json(out: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Overview v2 → overview.json")
    ap.add_argument("--history-dir", type=Path, default=HISTORY_DIR)
    ap.add_argument("--set-index", type=Path, default=None, help="default <history-dir>/SET_INDEX.csv")
    ap.add_argument("--today-dir", type=Path, default=TODAY_DIR,
                    help="โฟลเดอร์ที่มี breadth/market_stage/rs_ranking/sepa/oliver_kell/breakout/combined.json ของวันนั้น")
    ap.add_argument("--stockdesk-dir", type=Path, default=STOCKDESK_DIR)
    ap.add_argument("--capital-events", type=Path, default=None)
    ap.add_argument("--sector-map", type=Path, default=None)
    ap.add_argument("--as-of", default=None, help="ย้อนหลัง: ตัดข้อมูลถึงวันนี้ (YYYY-MM-DD)")
    ap.add_argument("--out", type=Path, default=OUT_FILE)
    a = ap.parse_args(argv)
    print("🧭 [Overview] กำลังสร้าง overview.json...")
    try:
        out = build(a.history_dir, a.set_index or a.history_dir / "SET_INDEX.csv", a.today_dir, a.stockdesk_dir,
                    a.capital_events, a.sector_map, a.as_of)
    except Exception as e:  # noqa: BLE001 — กันทุกกรณี: ยังเขียนไฟล์ (ทุกส่วน null) + errors
        out = {"generated_at": datetime.now(BKK).isoformat(timespec="seconds"), "as_of": None,
               "prev_trading_day": None, "rule_version": C.RULE_VERSION, "light": None, "volume": None,
               "groups": None, "leaders": None, "scanner_delta": None,
               "errors": [f"build_overview ล้ม: {type(e).__name__}: {e}"], "warnings": []}
    for w in out["warnings"]:
        print(f"  ⚠️ {w}")
    for e in out["errors"]:
        print(f"  ❌ {e}")
    try:
        write_json(out, a.out)
    except Exception as e:  # noqa: BLE001
        print(f"❌ [Overview] เขียน {a.out} ไม่ได้: {e}")
        return 1
    print(f"✅ [Overview] {a.out} ({a.out.stat().st_size / 1024:.1f} KB) as_of={out['as_of']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
