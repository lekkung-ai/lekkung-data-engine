import json
import math
import os
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Force UTF-8 encoding for standard output/error on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import pandas as pd
import yfinance as yf

try:
    from tools.core.growth_period import (annual_is_fresh, fy_end_month, quarter_is_fresh, quarter_label,
                                          short_fy, short_quarter, to_date)
    from tools.core.settrade_helper import get_authenticated_session, fetch_settrade_fundamentals
    from tools.core.set_index_source import update_set_index_csv, update_set_index_volume
    from tools.core.trading_calendar import (MISSING_DATA_SHARE, SET_INDEX_FILE, drop_copy_rows, drop_dates,
                                             holiday_dates, index_dates, missing_data_days)
except ImportError:
    from growth_period import (annual_is_fresh, fy_end_month, quarter_is_fresh, quarter_label,
                               short_fy, short_quarter, to_date)
    from settrade_helper import get_authenticated_session, fetch_settrade_fundamentals
    from set_index_source import update_set_index_csv, update_set_index_volume
    from trading_calendar import (MISSING_DATA_SHARE, SET_INDEX_FILE, drop_copy_rows, drop_dates,
                                  holiday_dates, index_dates, missing_data_days)

warnings.filterwarnings("ignore")


# 🔌 เชื่อมต่อ config.py
sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import (CHUNK_SIZE, DAILY_FILE, FULL_HISTORY_MIN_ROWS, FUND_WORKERS, HISTORY_DIR, RESULTS_DIR,
                    PRICE_FALLBACK_MAX_STALE_BUSINESS_DAYS,
                    PRICE_FALLBACK_WARN_PCT, SET_INDEX_FETCH_PERIOD,
                    SET_INDEX_YF_SYMBOL, SHORT_HISTORY_FILE, SHORT_HISTORY_MIN_ROWS,
                    SYMBOLS_FILE)
from tools.scanner.utils import load_symbol_sources  # noqa: E402

# Source ใน active_symbols ที่ถือว่ายังจดทะเบียนอยู่ (เหมือน _LISTED_SOURCES ของ iter_stock_files)
LISTED_SOURCES = frozenset({"tv", "grace", "listed"})

RETRY_MAX_TRIES = 2
RETRY_SLEEP_SEC = 2.0

# 📦 Growth Cache — เก็บค่า Revenue_Growth_YoY / NetProfit_Growth_QoQY ที่เคยดึงสำเร็จ
# ข้ามรอบรัน เพื่อเติมให้ตัวที่ดึงพลาดในรอบนี้ (Yahoo .financials/.quarterly_financials
# คืนค่าว่างแบบสุ่ม ~40% ต่อ request โดยไม่มี error ให้ retry จับได้ในรอบเดียว)
GROWTH_CACHE_FILE = HISTORY_DIR.parent / "cache" / "growth_cache.csv"
GROWTH_CACHE_MAX_AGE_DAYS = 100  # ~1 ไตรมาส เกินนี้ถือว่าหมดอายุ ไม่เอามาเติม


def fetch_fundamentals(ticker: str) -> Tuple[str, Dict[str, Any]]:
    """ฟังก์ชันย่อยสำหรับดึงงบการเงินแบบรวดเร็ว"""
    try:
        # --- 💡 ส่วนคำนวณ QoQY ---
        qoqy_growth = None
        qoqy_ref_period = None
        qoqy_note = None  # เหตุผลที่ QoQY คำนวณไม่ได้ → Growth_Note (ถ้าไม่มีแหล่งอื่นเติม)
        yf_t = f"{ticker}.BK"
        stock = yf.Ticker(yf_t)

        # 1. ดึงข้อมูลงบการเงินรายไตรมาส
        qf = stock.quarterly_financials

        # 2. ตรวจสอบว่ามีข้อมูล 'Net Income' และมีข้อมูลพอ (อย่างน้อย 5 ไตรมาส = ไตรมาสล่าสุด + ไตรมาสเดียวกันปีก่อน)
        # ไม่พอ (IPO / Yahoo ส่งงบมาไม่ครบ) → QoQY = None + เหตุผล แต่ field อื่นยังคำนวณต่อ
        # (เดิม qoqy_series ไม่ถูกสร้าง → NameError → except ทำให้ทุก field ของแถวเป็น None)
        if qf.empty or "Net Income" not in qf.index:
            qoqy_note = "ไม่มีงบรายไตรมาส"
        elif len(qf.columns) < 5:
            qoqy_note = f"ไตรมาสไม่พอ ({len(qf.columns)}/5)"
        else:
            net_income = qf.loc["Net Income"].sort_index()

            # 3. คำนวณ QoQY ตามสูตร
            qoqy_series = (
                (net_income - net_income.shift(4)) / net_income.shift(4).abs() * 100
            )

            # 4. ดึงค่าล่าสุดมาใช้งาน
            qoqy_growth = qoqy_series.iloc[-1]
            qoqy_ref_period = qoqy_series.index[-1]

        # --- 💡 ส่วนคำนวณ Annual Revenue Growth & Annual Net Income Growth ---
        ann_rev_growth = None
        ann_rev_ref_period = None
        ann_profit_growth = None
        ann_profit_ref_period = None
        f = stock.financials
        if not f.empty:
            if "Total Revenue" in f.index and len(f.columns) >= 2:
                rev = f.loc["Total Revenue"].sort_index(ascending=False)
                ann_rev_series = (rev.iloc[0] - rev.iloc[1]) / abs(rev.iloc[1])
                ann_rev_growth = ann_rev_series
                ann_rev_ref_period = rev.index[0]

            if "Net Income" in f.index and len(f.columns) >= 2:
                ni = f.loc["Net Income"].sort_index(ascending=False)
                ann_profit_series = (ni.iloc[0] - ni.iloc[1]) / abs(ni.iloc[1])
                ann_profit_growth = ann_profit_series
                ann_profit_ref_period = ni.index[0]

        # --- ส่วนดึงข้อมูล Snapshot เดิม ---
        info = stock.info
        return ticker, {
            "PE_Ratio": info.get("trailingPE", None),
            "EPS": info.get("trailingEps"),
            "Revenue": info.get("totalRevenue"),
            "ROE": info.get("returnOnEquity"),
            "Revenue_Growth_YoY": ann_rev_growth,  # 👈 ใช้ค่า Annual ที่คำนวณเอง
            "Revenue_Growth_YoY_ref": ann_rev_ref_period,  # 📦 period end (growth cache + ตัวกันงบค้าง)
            "Profit_Growth_YoY": ann_profit_growth,  # 👈 ใช้ค่า Annual Net Income ที่คำนวณเอง (CANSLIM)
            "Profit_Growth_YoY_ref": ann_profit_ref_period,  # 📦 period end (ตัวกันงบค้าง)
            "NetProfit_Growth_QoQY": qoqy_growth,  # 👈 ใช้ค่า QoQ ที่คำนวณเอง
            "NetProfit_Growth_QoQY_ref": qoqy_ref_period,  # 📦 period end (growth cache + ตัวกันงบค้าง)
            "NetProfit_Growth_QoQY_note": qoqy_note,  # 📦 เหตุผลที่ไม่มีค่า (apply_growth_freshness → Growth_Note)
            "Market_Cap": info.get("marketCap"),
        }
    except Exception:
        return ticker, {
            "PE_Ratio": None,
            "EPS": None,
            "Revenue": None,
            "ROE": None,
            "Revenue_Growth_YoY": None,
            "Revenue_Growth_YoY_ref": None,
            "Profit_Growth_YoY": None,
            "Profit_Growth_YoY_ref": None,
            "NetProfit_Growth_QoQY": None,
            "NetProfit_Growth_QoQY_ref": None,
            "NetProfit_Growth_QoQY_note": None,
            "Market_Cap": None,
        }


def _load_growth_cache() -> Dict[str, Dict[str, Any]]:
    """โหลด growth cache เดิม (ถ้ามี) เป็น dict คีย์ด้วย Ticker"""
    if not GROWTH_CACHE_FILE.exists():
        return {}
    df = pd.read_csv(GROWTH_CACHE_FILE)
    df["Ticker"] = df["Ticker"].astype(str).str.strip()
    return {row["Ticker"]: row.to_dict() for _, row in df.iterrows()}


def _save_growth_cache(cache: Dict[str, Dict[str, Any]]) -> None:
    GROWTH_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    cols = [
        "Ticker",
        "Revenue_Growth_YoY",
        "Revenue_Growth_YoY_ref_period",
        "Revenue_Growth_YoY_fetched_date",
        "NetProfit_Growth_QoQY",
        "NetProfit_Growth_QoQY_ref_period",
        "NetProfit_Growth_QoQY_fetched_date",
    ]
    df = pd.DataFrame(list(cache.values()))
    if df.empty:
        df = pd.DataFrame(columns=cols)
    else:
        df = df.reindex(columns=cols)
    df.to_csv(GROWTH_CACHE_FILE, index=False, encoding="utf-8-sig")


def _is_fresh(fetched_date_str: Any) -> bool:
    if fetched_date_str is None or (isinstance(fetched_date_str, float) and pd.isna(fetched_date_str)):
        return False
    try:
        fetched = datetime.strptime(str(fetched_date_str)[:10], "%Y-%m-%d").date()
    except ValueError:
        return False
    return (date.today() - fetched).days <= GROWTH_CACHE_MAX_AGE_DAYS


def _has_value(v: Any) -> bool:
    """ตัวเลขจริง (ไม่ใช่ None / NaN / ±inf)"""
    try:
        return v is not None and math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def apply_growth_cache_fallback(fundamental_data: Dict[str, Dict[str, Any]], today: Optional[date] = None) -> None:
    """เติม Revenue_Growth_YoY / NetProfit_Growth_QoQY ที่ Yahoo รอบนี้ไม่มีค่า (หรือได้ไตรมาสค้าง) ด้วยค่าจาก
    cache ที่เคยดึงสำเร็จ (ถ้ายังไม่หมดอายุ) และอัปเดต cache ด้วยค่าที่ดึงสำเร็จรอบนี้
    ค่าจาก cache ใช้ได้เฉพาะเมื่องวดของมันยังเป็นงวดล่าสุดที่ควรมี (growth_period) — cache เก็บข้ามรอบ
    (CI: daily-scan.yml "Restore/Save growth cache" · ไม่ commit ใน repo) จึงอาจเป็นงวดเก่าได้เสมอ
    ไม่มีไฟล์ (รอบแรก / restore ไม่ได้) → cache ว่าง ทำงานต่อตามปกติ
    ค่าค้างที่เหลือ → apply_growth_freshness ตัดสิน (แหล่งถัดไป / null + เหตุผล)
    ไม่แตะ field อื่น ไม่แตะ threshold/logic การกรองใดๆ — แก้แค่ที่มาของค่า growth
    """
    cache = _load_growth_cache()
    today = today or date.today()
    today_str = today.isoformat()
    recovered_from_cache = 0
    refused_stale = 0

    for ticker, data in fundamental_data.items():
        entry = cache.get(ticker, {"Ticker": ticker})
        fy = fy_end_month(data.get("Revenue_Growth_YoY_ref"), entry.get("Revenue_Growth_YoY_ref_period"))
        period_ok = {
            "Revenue_Growth_YoY": lambda p: annual_is_fresh(p, today),
            "NetProfit_Growth_QoQY": lambda p: quarter_is_fresh(p, today, fy),
        }
        for col, is_period_fresh in period_ok.items():
            ref = data.get(f"{col}_ref")
            data[f"{col}_src"] = "yahoo"
            if data.get(col) is not None:
                entry[col] = data[col]
                entry[f"{col}_ref_period"] = str(ref)[:10] if ref is not None else None
                entry[f"{col}_fetched_date"] = today_str
                if is_period_fresh(ref):
                    continue
            cached_ref = entry.get(f"{col}_ref_period")
            if not (_is_fresh(entry.get(f"{col}_fetched_date")) and _has_value(entry.get(col))):
                continue
            if not is_period_fresh(cached_ref):
                refused_stale += 1
                if data.get(col) is not None:  # ค่าสดรอบนี้ (แม้ค้าง) ใหม่กว่า cache เสมอ
                    continue
            data[col] = entry.get(col)
            data[f"{col}_ref"] = cached_ref
            data[f"{col}_src"] = "cache"
            recovered_from_cache += 1

        cache[ticker] = entry

    _save_growth_cache(cache)
    print(
        f"   📦 [Growth Cache] เติมค่าจาก cache ที่เคยดึงสำเร็จ {recovered_from_cache} field "
        f"(ในนั้นงวดค้าง {refused_stale} field → ตัวกันงบค้างตัดสินต่อ), "
        f"cache สะสมทั้งหมด {len(cache)} ticker"
    )


# ⏳ ตัวกันงบค้าง: (คอลัมน์, index ใน tv_dict ของค่า, ของ period end, งบปี?, ตัวคูณ TradingView → หน่วยของคอลัมน์ ณ จุดนี้)
# Revenue/Profit ยังเป็นอัตราส่วน (×100 ตอนสร้าง df_daily) · QoQY เป็น % แล้ว · TradingView ส่ง % มา
GROWTH_FIELDS = (
    ("NetProfit_Growth_QoQY", 5, 6, False, 1.0, "Growth_Quarter_End"),
    ("Revenue_Growth_YoY", 7, 9, True, 0.01, "Rev_Growth_Period_End"),
    ("Profit_Growth_YoY", 8, 9, True, 0.01, "Profit_Growth_Period_End"),
)
TV_GROWTH_COLUMNS = ["net_income_yoy_growth_fq", "fiscal_period_end_fq", "total_revenue_yoy_growth_fy",
                     "net_income_yoy_growth_fy", "fiscal_period_end_fy"]


def apply_growth_freshness(rows: List[Dict[str, Any]], tv_dict: Dict[str, list], today: date) -> None:
    """ทุกค่า growth ต้องมาจากงวดล่าสุดที่ควรมีแล้ว (growth_period: เส้นตาย SET 45/60 วัน + ผ่อน 7 วัน)
    Yahoo/cache งวดค้างหรือไม่มีค่า → TradingView scanner (มี period end ของตัวเอง) ถ้างวดนั้นสด →
    ไม่มีแหล่งสด → null (ไม่เติมค่าเอง) + Growth_Stale = งวดที่ค้าง (scan_lekkung_growth ใส่กลุ่มงบไม่ครบ)
    QoQY ที่คำนวณไม่ได้เลย (ไตรมาสไม่พอ / ไม่มีงบรายไตรมาส) และไม่มีแหล่งอื่นเติม → Growth_Note = เหตุผล
    เขียนงวดที่ใช้ลงคอลัมน์ period end + Growth_Quarter ("2026Q2") + Growth_Source ของ QoQY"""
    counts: Dict[str, int] = {}

    def bump(k: str) -> None:
        counts[k] = counts.get(k, 0) + 1

    for row in rows:
        tv = tv_dict.get(row["Ticker"]) or []

        def tv_at(i: int) -> Any:
            return tv[i] if len(tv) > i and tv[i] is not None else None

        fy = fy_end_month(row.get("Revenue_Growth_YoY_ref"), tv_at(9))
        stale_notes = []
        qoqy_note = row.pop("NetProfit_Growth_QoQY_note", None)
        for col, vi, pi, annual, scale, period_col in GROWTH_FIELDS:
            def is_period_fresh(p: Any) -> bool:
                return annual_is_fresh(p, today) if annual else quarter_is_fresh(p, today, fy)

            value, ref = row.get(col), row.pop(f"{col}_ref", None)
            src = row.pop(f"{col}_src", "yahoo")
            if _has_value(value) and is_period_fresh(ref):
                pass
            elif _has_value(tv_at(vi)) and is_period_fresh(tv_at(pi)):
                bump(f"{col}: {'stale' if _has_value(value) else 'missing'} → tv")
                value, ref, src = float(tv_at(vi)) * scale, tv_at(pi), "tv"
            else:
                if _has_value(value):
                    bump(f"{col}: stale → null")
                    stale_notes.append(f"{col} {(short_fy if annual else short_quarter)(ref)}")
                value, ref, src = None, None, None
            row[col] = value
            d = to_date(ref)
            row[period_col] = d.isoformat() if d else None
            if col == "NetProfit_Growth_QoQY":
                row["Growth_Source"] = src
        row["Growth_Quarter"] = quarter_label(row["Growth_Quarter_End"])
        row["Growth_Stale"] = "; ".join(stale_notes) or None
        row["Growth_Note"] = qoqy_note if row["NetProfit_Growth_QoQY"] is None and qoqy_note else None
        if row["Growth_Note"]:
            bump(f"NetProfit_Growth_QoQY: {qoqy_note.split(' (')[0]} → null")

    print(f"   ⏳ [Growth งวดล่าสุด] ณ {today}: " + (", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "ทุกค่าเป็นงวดล่าสุดแล้ว"))


def business_days_between(d1: date, d2: date) -> int:
    """Count business days (Mon-Fri) strictly between d1 and d2 (d2 > d1),
    exclusive of d1, inclusive of d2. Same Mon-Fri approximation used
    elsewhere in this codebase (SET holidays not excluded)."""
    count = 0
    d = d1
    while d < d2:
        d += timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return count


def add_indicator_columns(df: pd.DataFrame) -> pd.DataFrame:
    """SMA 50/150/200 + 52W High/Low on a copy of an OHLCV frame (written into the history file)."""
    df = df.copy()
    df["SMA_50"] = df["Close"].rolling(window=50).mean().round(2)
    df["SMA_150"] = df["Close"].rolling(window=150).mean().round(2)
    df["SMA_200"] = df["Close"].rolling(window=200).mean().round(2)
    # True 52-week high/low uses the day's actual intraday High/Low, not
    # the closing price - a Close-based rolling max can never be exceeded
    # by that same day's own Close, which made "at the 52W high" trivially
    # true for any stock closing at a multi-month high and inflated the
    # scanner's "NH" (near-high) badge across an implausibly large share
    # of the universe on ordinary days.
    df["52W_High"] = df["High"].rolling(window=252).max().round(2)
    df["52W_Low"] = df["Low"].rolling(window=252).min().round(2)
    return df


def drop_holiday_rows_from_files(holidays, skip: set, missing_days=None) -> Tuple[int, int]:
    """ตัดแถววันหยุด + แถวสำเนาในวันข้อมูลหาย (missing_days) ออกจากไฟล์หุ้นที่รอบนี้ไม่ได้เขียนใหม่
    (stale fallback / ticker ที่หลุด universe) แล้วคำนวณคอลัมน์ SMA/52W ใหม่ · ไม่แตะ SET_INDEX.csv
    · คืน (จำนวนไฟล์, จำนวนแถว) ที่ตัด"""
    if not holidays and not missing_days:
        return 0, 0
    files = rows = 0
    for path in HISTORY_DIR.glob("*.csv"):
        if path.name == SET_INDEX_FILE or path.stem in skip:
            continue
        try:
            df = pd.read_csv(path, index_col=0, parse_dates=True)
        except Exception as e:  # ไฟล์เสีย — ปล่อยไว้ให้ fallback / stale filter จัดการเหมือนเดิม
            print(f"   ⚠️ [Holiday] อ่าน {path.name} ไม่ได้ ({type(e).__name__}) — ข้าม")
            continue
        if not isinstance(df.index, pd.DatetimeIndex):
            continue
        cleaned = drop_copy_rows(drop_dates(df, holidays), missing_days)
        if len(cleaned) == len(df):
            continue
        if {"Close", "High", "Low"} <= set(cleaned.columns):
            cleaned = add_indicator_columns(cleaned)
        cleaned.to_csv(path)
        files += 1
        rows += len(df) - len(cleaned)
    return files, rows


MISSING_DATA_FILE = RESULTS_DIR / "missing_data_days.json"


def write_missing_data_days(days: Dict[Any, Tuple[int, int]], dropped: int) -> None:
    """MISSING_DATA_FILE: วันข้อมูลหายที่เจอในข้อมูลที่โหลดรอบนี้ (trading_calendar.missing_data_days)
    — health_check H11 อ่าน · เขียนทุกรอบ (ว่างก็เขียน) ไม่ให้รายการเก่าค้าง"""
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "threshold_share": MISSING_DATA_SHARE,
        "rows_dropped": dropped,
        "days": [{"date": str(d), "copy_rows": c, "rows": n, "share": round(c / n, 4)}
                 for d, (c, n) in sorted(days.items())],
    }
    MISSING_DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    MISSING_DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def write_short_history(short_history: Dict[str, int]) -> None:
    """SHORT_HISTORY_FILE: หุ้นที่แถวดิบ < FULL_HISTORY_MIN_ROWS (IPO) รอบนี้ — อ่านโดย
    tools/scanner/utils.load_short_history · เขียนทุกรอบ (ว่างก็เขียน) ไม่ให้รายชื่อเก่าค้าง"""
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "min_rows_kept": SHORT_HISTORY_MIN_ROWS,
        "full_history_min_rows": FULL_HISTORY_MIN_ROWS,
        "tickers": dict(sorted(short_history.items())),
    }
    SHORT_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    SHORT_HISTORY_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if short_history:
        print(f"\n🆕 [IPO] หุ้นประวัติสั้น {len(short_history)} ตัว (เก็บไฟล์ ไม่เข้า RS / scanner): "
              + ", ".join(f"{t}({n})" for t, n in sorted(short_history.items())))


def build_snapshot_row(ticker: str, df: pd.DataFrame, data_date: str, fallback: bool) -> Optional[Dict[str, Any]]:
    """Compute technical columns + build the daily_snapshot row dict for one
    ticker's OHLCV dataframe. Used for both freshly-downloaded data and
    stale-file fallback data (the latter already has SMA/52W columns
    precomputed from whenever the file was last refreshed)."""
    if len(df) < 2:
        return None
    if fallback:
        # Stale file already has SMA/52W columns computed - reuse as-is.
        last_row = df.iloc[-1]
        prev_row = df.iloc[-2]
    else:
        df = add_indicator_columns(df)
        save_file = HISTORY_DIR / f"{ticker}.csv"
        df.to_csv(save_file)
        last_row = df.iloc[-1]
        prev_row = df.iloc[-2]

    pct_change = ((last_row["Close"] - prev_row["Close"]) / prev_row["Close"]) * 100

    return {
        "Date": data_date,
        "Ticker": ticker,
        "Close": round(last_row["Close"], 2),
        "Change_Pct": round(pct_change, 2),
        "Volume": int(last_row["Volume"]),
        "SMA_50": last_row["SMA_50"],
        "SMA_150": last_row["SMA_150"],
        "SMA_200": last_row["SMA_200"],
        "52W_High": last_row["52W_High"],
        "52W_Low": last_row["52W_Low"],
        "data_date": data_date,
        "fallback": fallback,
    }


def retry_single_ticker_download(ticker: str) -> Optional[pd.DataFrame]:
    """Short, per-ticker retry for a ticker that failed the bulk batch
    download - yfinance's bulk multi-ticker calls occasionally drop/empty a
    handful of tickers for no persistent reason (same random-empty-response
    behavior documented for .financials/.quarterly_financials elsewhere in
    this file); a couple of individual retries often succeeds where the
    batch call didn't."""
    yf_t = f"{ticker}.BK"
    for attempt in range(RETRY_MAX_TRIES):
        try:
            df = yf.download(yf_t, period="3y", interval="1d", auto_adjust=True, progress=False)
            if not df.empty:
                df = df.dropna(subset=["Close"])
                if len(df) >= SHORT_HISTORY_MIN_ROWS:
                    return df
        except Exception:
            pass
        time.sleep(RETRY_SLEEP_SEC)
    return None


def load_stale_fallback(ticker: str, today: date) -> Optional[Dict[str, Any]]:
    """Reuse the existing data/history/{ticker}.csv file when fresh download
    (bulk + retry) both failed. Refuses files older than
    PRICE_FALLBACK_MAX_STALE_BUSINESS_DAYS business days - past that point a
    ticker is more likely suspended/delisted than Yahoo being flaky, and
    silently reusing ancient data would let a ghost linger in scan results
    indefinitely."""
    stale_path = HISTORY_DIR / f"{ticker}.csv"
    if not stale_path.exists():
        print(f"   ⚠️ [Fallback] {ticker}: no history file at all (never downloaded, or the cache miss lost it), dropped")
        return None
    try:
        df = pd.read_csv(stale_path, index_col=0, parse_dates=True)
    except Exception as e:
        print(f"   ⚠️ [Fallback] {ticker}: history file unreadable ({e}), dropped")
        return None
    if df.empty:
        print(f"   ⚠️ [Fallback] {ticker}: history file empty, dropped")
        return None

    last_date = df.index[-1].date()
    age = business_days_between(last_date, today)
    if age > PRICE_FALLBACK_MAX_STALE_BUSINESS_DAYS:
        print(f"   ⚠️ [Fallback] {ticker}: stale >{PRICE_FALLBACK_MAX_STALE_BUSINESS_DAYS}d (last data {last_date}), dropped")
        return None

    row = build_snapshot_row(ticker, df, last_date.strftime("%Y-%m-%d"), fallback=True)
    if row is None:
        print(f"   ⚠️ [Fallback] {ticker}: stale file too short, dropped")
    return row


def download_set_index() -> None:
    """อัปเดตราคา SET_INDEX.csv (สำหรับ breadth) — TradingView หลัก, Yahoo ต่อท้ายแท่งล่าสุดเป็นตัวสำรอง
    ไม่เข้า daily_snapshot — เป็น index ไม่ใช่หุ้น downstream scanner ไม่ควรเห็น
    รายละเอียด/กติกาการเขียนดู tools/core/set_index_source.py
    คอลัมน์ Volume ใส่ทีหลังใน update_set_index_volume() หลังดาวน์โหลดหุ้นเสร็จ"""
    try:
        update_set_index_csv(HISTORY_DIR, SET_INDEX_YF_SYMBOL, SET_INDEX_FETCH_PERIOD)
    except Exception as e:  # ห้ามให้ดัชนีทำ pipeline ล้ม — ไฟล์เดิมยังอยู่ (เขียนแบบ atomic)
        print(f"  ❌ ERROR [SET_INDEX] อัปเดตไม่สำเร็จ: {type(e).__name__}: {e} — คงไฟล์เดิม")


def download_history_and_build_snapshot() -> None:
    if not SYMBOLS_FILE.exists():
        print(f"❌ ไม่พบไฟล์ '{SYMBOLS_FILE.name}' กรุณารัน Step 1 ก่อนครับ")
        return

    df_symbols = pd.read_csv(SYMBOLS_FILE)
    tickers = df_symbols["Ticker"].tolist()

    print("📈 กำลังอัปเดต SET Index (TradingView หลัก / Yahoo สำรอง) ก่อนดาวน์โหลดหุ้น...")
    download_set_index()

    print(
        f"📥 [Phase 1/2] กำลังดาวน์โหลดข้อมูลย้อนหลัง 2 ปี สำหรับหุ้น {len(tickers)} ตัว..."
    )

    daily_snapshot = []
    success_count = 0
    valid_tickers = []
    chunk_failed_all: List[str] = []
    fresh: Dict[str, pd.DataFrame] = {}  # ticker -> ราคาที่โหลดสำเร็จรอบนี้ (ยังไม่เขียนไฟล์)
    today = datetime.now().date()

    for i in range(0, len(tickers), CHUNK_SIZE):
        chunk_tickers = tickers[i : i + CHUNK_SIZE]
        yf_tickers = [f"{t}.BK" for t in chunk_tickers]

        print(
            f"   ⏳ กำลังโหลดล็อตที่ {(i//CHUNK_SIZE)+1} ({len(chunk_tickers)} ตัว)..."
        )
        data = yf.download(
            yf_tickers,
            period="3y",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
        )

        for ticker in chunk_tickers:
            try:
                yf_t = f"{ticker}.BK"
                if len(yf_tickers) > 1:
                    if yf_t not in data.columns.levels[0]:
                        chunk_failed_all.append(ticker)
                        continue
                    df = data[yf_t].dropna(subset=["Close"])
                else:
                    df = data.dropna(subset=["Close"])

                if len(df) >= SHORT_HISTORY_MIN_ROWS:
                    fresh[ticker] = df
                else:
                    chunk_failed_all.append(ticker)
            except Exception as e:
                print(f"⚠️ Error downloading {ticker}: {e}")
                chunk_failed_all.append(ticker)

        time.sleep(1)

    # 🔁 [Retry] short per-ticker retry for everything the bulk batch calls
    # missed, then fall back to each ticker's existing (possibly stale)
    # history file rather than dropping it from daily_prices.csv outright.
    fallback_tickers: List[str] = []
    dropped_tickers: List[str] = []
    still_failed: List[str] = []
    if chunk_failed_all:
        print(f"\n🔁 [Retry] {len(chunk_failed_all)} ticker(s) failed bulk download, retrying individually...")
        for ticker in chunk_failed_all:
            retry_df = retry_single_ticker_download(ticker)
            if retry_df is not None:
                fresh[ticker] = retry_df
            else:
                still_failed.append(ticker)

    # 🆕 [IPO] ไฟล์ของหุ้นประวัติสั้นถูกเก็บ (stage "IPO" บนเว็บ) แต่ RS / scanner ต้องเห็น universe
    # เท่าเดิม = หุ้นที่ผ่านเกณฑ์เดิม (FULL_HISTORY_MIN_ROWS แถวดิบ) · รายชื่อนี้ iter_stock_files ใช้กรอง
    # ประวัติสั้นแต่ไม่อยู่ในรายชื่อที่ยังจดทะเบียน (Source = sector_map เช่นหุ้นเพิ่งถูกเพิกถอน ซึ่ง
    # Yahoo เหลือแถวไม่กี่สิบวัน) → ทางเดิม: ไม่เขียนไฟล์ ส่งต่อ fallback / ตัดออก
    sources, _ = load_symbol_sources()
    for t in [t for t, df in fresh.items()
              if len(df) < FULL_HISTORY_MIN_ROWS and (sources or {}).get(t) not in LISTED_SOURCES]:
        del fresh[t]
        still_failed.append(t)
    short_history = {t: len(df) for t, df in fresh.items() if len(df) < FULL_HISTORY_MIN_ROWS}
    write_short_history(short_history)

    # 🗓️ [Holiday] Yahoo ใส่แถววันหยุด (Volume 0, ราคาเดิม) มาทุกครั้งที่ดาวน์โหลด — ตัดออกก่อนเขียน
    # (นิยาม + เหตุผลที่ต้องเทียบ SET_INDEX ดู tools/core/trading_calendar.py)
    holidays = holiday_dates(fresh, index_dates(HISTORY_DIR / SET_INDEX_FILE))
    if holidays:
        print(f"\n🗓️ [Holiday] ตัดแถววันที่ตลาดไม่เปิด {len(holidays)} วัน: {', '.join(sorted(map(str, holidays)))}")

    # 🕳️ [Missing data] วันที่ตลาดเปิดแต่ Yahoo ไม่มีข้อมูลหุ้นจำนวนมาก — Yahoo ส่ง "แถวสำเนา" (Volume 0,
    # OHLC = ราคาปิดวันก่อน) มาแทน → ตัดแถวสำเนาของวันนั้นทิ้ง ไม่เติมค่าเอง (นิยามดู trading_calendar.py)
    fresh = {t: drop_dates(df, holidays) for t, df in fresh.items()}
    missing = missing_data_days(fresh)
    missing_rows = 0
    if missing:
        before_rows = sum(len(df) for df in fresh.values())
        fresh = {t: drop_copy_rows(df, set(missing)) for t, df in fresh.items()}
        missing_rows = before_rows - sum(len(df) for df in fresh.values())
        print(f"\n🕳️ [Missing data] วันข้อมูลหาย {len(missing)} วัน (แถวสำเนา ≥ {MISSING_DATA_SHARE:.0%}) "
              f"— ตัดแถวสำเนา {missing_rows} แถว: "
              + ", ".join(f"{d}({c}/{n})" for d, (c, n) in sorted(missing.items())))
    write_missing_data_days(missing, missing_rows)

    for ticker, df in fresh.items():
        row = build_snapshot_row(ticker, df, today.strftime("%Y-%m-%d"), fallback=False)
        if row is not None:
            daily_snapshot.append(row)
            valid_tickers.append(ticker)
            success_count += 1
        else:
            still_failed.append(ticker)

    # ไฟล์ที่รอบนี้ไม่ได้เขียนใหม่ (stale fallback / ticker ที่หลุด universe) ก็ต้องไม่มีแถววันหยุดค้าง
    cleaned_files, cleaned_rows = drop_holiday_rows_from_files(holidays, skip=set(fresh), missing_days=set(missing))
    if cleaned_files:
        print(f"   🗓️ [Holiday/Missing] ไฟล์ที่ไม่ได้โหลดใหม่: ตัด {cleaned_rows} แถวจาก {cleaned_files} ไฟล์")

    if still_failed:
        for ticker in still_failed:
            fb_row = load_stale_fallback(ticker, today)
            if fb_row is not None:
                daily_snapshot.append(fb_row)
                valid_tickers.append(ticker)
                fallback_tickers.append(ticker)
            else:
                dropped_tickers.append(ticker)

        print(f"   📦 [Fallback] {len(fallback_tickers)} ticker(s) used stale history file, {len(dropped_tickers)} dropped (no usable file)")
        if fallback_tickers:
            print(f"      Fallback tickers: {', '.join(fallback_tickers)}")

        fallback_pct = len(fallback_tickers) / len(tickers) if tickers else 0
        if fallback_pct > PRICE_FALLBACK_WARN_PCT:
            print(
                f"\n{'='*80}\n"
                f"🚨 WARNING: {len(fallback_tickers)}/{len(tickers)} tickers ({fallback_pct*100:.1f}%) "
                f"needed stale-file fallback this run - this looks like a systemic Yahoo issue, "
                f"not isolated per-ticker flakiness.\n"
                f"{'='*80}"
            )

    # 📊 Volume ของ SET_INDEX.csv = มูลค่าซื้อขายรวมของ universe — ต้องทำหลังไฟล์หุ้นเขียนครบแล้ว
    try:
        update_set_index_volume(HISTORY_DIR)
    except Exception as e:
        print(f"  ❌ ERROR [SET_INDEX] ใส่มูลค่าซื้อขายรวมไม่สำเร็จ: {type(e).__name__}: {e}")

    print(f"\n🔎 [Phase 2/2] กำลังดึงข้อมูลปัจจัยพื้นฐาน (PE, EPS, ROE)...")
    fundamental_data = {}

    # 🛡️ กางร่มครอบ ThreadPool ทั้งหมดด้วย redirect_stdout/stderr (เปิดครั้งเดียว ป้องกัน Thread ชนกัน)
    with open(os.devnull, "w") as fnull:
        with redirect_stdout(fnull), redirect_stderr(fnull):
            with ThreadPoolExecutor(max_workers=FUND_WORKERS) as executor:
                futures = {
                    executor.submit(fetch_fundamentals, t): t for t in valid_tickers
                }
                for future in as_completed(futures):
                    t, fund_data = future.result()
                    fundamental_data[t] = fund_data

    apply_growth_cache_fallback(fundamental_data)

    for row in daily_snapshot:
        t = row["Ticker"]
        row.update(fundamental_data.get(t, {}))

    print(f"\n🔎 [Phase 2.5/2] กำลังดึงข้อมูล SETTrade & TradingView Fundamentals เสริมความแม่นยำ...")

    # 1. TradingView Fallback Batch Fetch
    tv_dict = {}
    try:
        import requests
        tv_payload = {
            "filter": [{"left": "name", "operation": "in_range", "right": valid_tickers}],
            "options": {"lang": "en"},
            "markets": ["thailand"],
            "columns": ["name", "price_earnings_ttm", "earnings_per_share_basic_ttm", "return_on_equity", "market_cap_basic"]
                       + TV_GROWTH_COLUMNS,  # index 5-9 → GROWTH_FIELDS
            "range": [0, len(valid_tickers)]
        }
        res = requests.post("https://scanner.tradingview.com/thailand/scan", json=tv_payload, timeout=15)
        if res.status_code == 200:
            tv_data = res.json().get("data", [])
            tv_dict = {item["d"][0]: item["d"] for item in tv_data if len(item.get("d", [])) >= 5}
    except Exception as e:
        print(f"⚠️ Error fetching TradingView fallback data: {e}")

    # 2. SETTrade Primary Fetch via ThreadPoolExecutor (5 workers)
    settrade_dict = {}
    try:
        sess = get_authenticated_session()
        def _fetch_st(t):
            return t, fetch_settrade_fundamentals(sess, t)

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(_fetch_st, t) for t in valid_tickers]
            for future in as_completed(futures):
                t, sf = future.result()
                if sf is not None:
                    settrade_dict[t] = sf
    except Exception as e:
        print(f"⚠️ Error in SETTrade parallel fetch: {e}")

    # 3. Chain Priority Loop (SETTrade > TradingView > Yahoo/Keep original)
    for row in daily_snapshot:
        t = row["Ticker"]
        sf = settrade_dict.get(t)
        tv = tv_dict.get(t)

        tv_pe = tv[1] if tv and len(tv) > 1 and tv[1] is not None else None
        row["PE_Ratio_tv"] = tv_pe

        # PE: SETTrade primary > TradingView fallback > Yahoo/original
        if sf and sf.get("pe") is not None:
            row["PE_Ratio"] = sf["pe"]
        elif tv_pe is not None:
            row["PE_Ratio"] = tv_pe
        # else: keep original row["PE_Ratio"] (merge-guard)

        # PBV: SETTrade primary
        if sf and sf.get("pbv") is not None:
            row["PBV"] = sf["pbv"]

        # EPS / ROE / Market_Cap: TradingView original fields
        if tv:
            if len(tv) > 2 and tv[2] is not None: row["EPS"] = tv[2]
            if len(tv) > 3 and tv[3] is not None: row["ROE"] = tv[3] / 100.0
            if len(tv) > 4 and tv[4] is not None: row["Market_Cap"] = tv[4]

    apply_growth_freshness(daily_snapshot, tv_dict, today)

    if daily_snapshot:
        df_daily = pd.DataFrame(daily_snapshot)
        for col in ["Close", "Change_Pct", "PE_Ratio", "PE_Ratio_tv", "PBV", "EPS"]:
            if col in df_daily.columns:
                df_daily[col] = pd.to_numeric(df_daily[col], errors="coerce").round(2)
        if "ROE" in df_daily.columns:
            df_daily["ROE"] = pd.to_numeric(df_daily["ROE"], errors="coerce").round(4)
        # 💡 แปลงค่า Growth ที่ดึงจาก yfinance (เป็นอัตราส่วน 0.xx) ให้เป็น %
        if "Revenue_Growth_YoY" in df_daily.columns:
            df_daily["Revenue_Growth_YoY"] = (
                pd.to_numeric(df_daily["Revenue_Growth_YoY"], errors="coerce") * 100
            )
        if "Profit_Growth_YoY" in df_daily.columns:
            df_daily["Profit_Growth_YoY"] = (
                pd.to_numeric(df_daily["Profit_Growth_YoY"], errors="coerce") * 100
            )
        # 💡 ส่วน QoQY ที่เราคำนวณเองเป็น % แล้ว แค่แปลงเป็นตัวเลขก็พอ
        if "NetProfit_Growth_QoQY" in df_daily.columns:
            df_daily["NetProfit_Growth_QoQY"] = pd.to_numeric(
                df_daily["NetProfit_Growth_QoQY"], errors="coerce"
            ).round(2)

        # 🛡️ SOFT GUARD: SETTrade ล่มทั้ง batch → เตือน + ข้าม PE update (คงค่าเดิมจาก merge-guard) แต่ยังเขียนราคา
        if len(settrade_dict) == 0 and len(valid_tickers) > 0:
            print(
                f"\n⚠️ [Phase 2.5] WARNING: SETTrade returned 0 responses across all {len(valid_tickers)} tickers. "
                f"Keeping existing PE/PBV (TradingView fallback / previous values). Price data will still be written normally."
            )

        # 🎯 เซฟไฟล์ Master Snapshot ลงโฟลเดอร์ตาม config
        df_daily.to_csv(DAILY_FILE, index=False, encoding="utf-8-sig")

    print("\n" + "=" * 80)
    print(
        f"🎉 อัปเดตเสร็จสมบูรณ์ (สด {success_count}/{len(tickers)} ตัว, "
        f"fallback {len(fallback_tickers)} ตัว, ตัดออก {len(dropped_tickers)} ตัว)"
    )
    print("=" * 80)


if __name__ == "__main__":
    download_history_and_build_snapshot()
