"""
trading_calendar.py — "was the market open?" helpers shared by run_all.py (non-trading-day guard)
and scripts/health_check.py.

Reference date = latest bar with Volume > 0 across the stock files in HISTORY_DIR (same rule as
the stale-file filter, last_traded_date). Yahoo adds rows for holidays with Volume 0 and the
previous Close, so those never move the reference date forward.

Today is a non-trading day only when BOTH sources say so:
  - stock files: reference date < today   (no stock traded today)
  - SET_INDEX.csv (TradingView, never has holiday rows): last bar < today
If only the stock files are behind (Yahoo late) the run goes ahead as before and the health
check reports the stale data - a quiet skip would hide a real outage.

Holiday rows (2_download_history.py drops them before writing the stock files): a date is a
holiday when BOTH
  - fewer than MIN_ACTIVE_SHARE of the stocks with a row that day have Volume > 0, and
  - SET_INDEX.csv has no bar that day (checked only inside SET_INDEX's own date range).
The volume rule alone is not enough: Yahoo's older history also has whole trading days copied
from the previous close with Volume 0 (e.g. 2024-03-12, 2025-10-07 - SET_INDEX has those days),
and dropping them would delete real trading days.

Missing-data days (Yahoo has no data for many stocks on a day the market WAS open): Yahoo sends a
"copy" row for those stocks - Volume 0 and Open/High/Low/Close = the previous Close. A few copies
are normal (illiquid stocks that did not trade: 2-6% of the universe per day since 2025-06), but
on a missing-data day the share jumps (2025-10-07: 99.5%, 2026-05-26: 20%, 65 days of 20-99% in
2024). A date is a missing-data day when copy rows are >= MISSING_DATA_SHARE of the stocks with a
row that day; on such a day every copy row is dropped (never filled in), so the files hold real
bars only. Everything downstream counts bars (RS iloc[-n], SMA/52W rolling rows, ADTV tail(n),
History_Days) and simply sees one bar less for that stock; breadth (a date grid) computes each
stock's MAs on its own bars - see calculate_breadth.compute_breadth_matrix.
"""

import sys
from datetime import date
from pathlib import Path
from typing import Mapping, Optional, Set

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[2]))
from tools.scanner.utils import NON_STOCK_FILES, last_traded_date  # noqa: E402

SET_INDEX_FILE = "SET_INDEX.csv"
MIN_ACTIVE_SHARE = 0.5
# copy-row share that marks a missing-data day: ~2x the normal daily maximum (5.9% since 2025-06,
# 95th percentile 5.0%) and below the smallest real gap seen (2026-05-26: 20.3%)
MISSING_DATA_SHARE = 0.10


def reference_date(history_dir: Path) -> Optional[date]:
    """วันอ้างอิง = max(last_traded_date) ของไฟล์หุ้น (ไม่รวม NON_STOCK_FILES) — เหมือน find_stale_files"""
    days = [
        d
        for p in history_dir.glob("*.csv")
        if p.name not in NON_STOCK_FILES and (d := last_traded_date(p)) is not None
    ]
    return max(days) if days else None


def last_csv_date(path: Path) -> Optional[date]:
    """วันที่ของแถวข้อมูลแถวสุดท้าย (คอลัมน์แรก) ของ CSV"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            return date.fromisoformat(line.split(",", 1)[0].strip()[:10])
        except ValueError:
            continue
    return None


def is_non_trading_day(today: date, ref: Optional[date], index_last: Optional[date]) -> bool:
    """True when neither the stock files nor SET_INDEX have a bar for `today`.
    Missing information (None) never counts as a non-trading day - the run goes ahead."""
    if ref is None or index_last is None:
        return False
    return ref < today and index_last < today


def index_dates(set_index_csv: Path) -> Set[date]:
    """วันที่ทุกแถวของ SET_INDEX.csv (TradingView — ไม่มีแท่งวันหยุด) · ไม่มีไฟล์ / อ่านไม่ได้ → set ว่าง"""
    try:
        idx = pd.read_csv(set_index_csv, index_col=0, usecols=[0]).index
    except (OSError, ValueError):
        return set()
    return {d.date() for d in pd.to_datetime(idx, errors="coerce") if not pd.isna(d)}


def _volume(df: pd.DataFrame) -> pd.Series:
    """คอลัมน์ Volume ทั้งแบบหัวตารางชั้นเดียวและ 2 ชั้นของ yfinance (Price/Ticker)"""
    col = df["Volume"]
    return col.iloc[:, 0] if isinstance(col, pd.DataFrame) else col


def holiday_dates(frames: Mapping[str, pd.DataFrame], index_days: Set[date]) -> Set[date]:
    """วันที่ไม่ใช่วันทำการในราคาหุ้นทั้งชุด (frames: ticker -> OHLCV, index = วันที่)

    = วันที่หุ้นที่มีแถววันนั้นมี Volume > 0 ไม่ถึง MIN_ACTIVE_SHARE และ SET_INDEX ไม่มีแท่ง
    วันนั้น (เฉพาะช่วงวันที่ที่ SET_INDEX ครอบคลุม · ไม่มี SET_INDEX → ไม่ตัดอะไร)"""
    if not index_days:
        return set()
    lo, hi = min(index_days), max(index_days)
    rows: dict = {}
    active: dict = {}
    for df in frames.values():
        if df is None or df.empty or "Volume" not in df.columns.get_level_values(0):
            continue
        vol = pd.to_numeric(_volume(df), errors="coerce").fillna(0).to_numpy()
        for ts, v in zip(df.index, vol):
            d = ts.date()
            rows[d] = rows.get(d, 0) + 1
            if v > 0:
                active[d] = active.get(d, 0) + 1
    return {
        d for d, n in rows.items()
        if active.get(d, 0) < MIN_ACTIVE_SHARE * n and lo <= d <= hi and d not in index_days
    }


def drop_dates(df: pd.DataFrame, dates: Set[date]) -> pd.DataFrame:
    """df โดยไม่มีแถวของวันที่ใน dates"""
    if not dates or df.empty:
        return df
    keep = [ts.date() not in dates for ts in df.index]
    return df[keep]


def copy_rows(df: pd.DataFrame) -> pd.Series:
    """True for rows that only repeat the previous bar: Volume 0 and Open = High = Low = Close = previous Close"""
    if df is None or df.empty or len(df) < 2:
        return pd.Series(False, index=getattr(df, "index", None), dtype=bool)
    def col(name: str) -> pd.Series:
        c = df[name]
        return pd.to_numeric(c.iloc[:, 0] if isinstance(c, pd.DataFrame) else c, errors="coerce")
    close = col("Close")
    prev = close.shift(1)
    vol0 = col("Volume").fillna(0) == 0
    same = vol0 & prev.notna()
    for name in ("Open", "High", "Low", "Close"):
        same &= (col(name) - prev).abs() <= 1e-9 * prev.abs().clip(lower=1)
    return same.fillna(False)


def missing_data_days(frames: Mapping[str, pd.DataFrame]) -> dict:
    """{date: (copy rows, rows)} for every date whose copy-row share >= MISSING_DATA_SHARE"""
    rows: dict = {}
    copies: dict = {}
    for df in frames.values():
        if df is None or df.empty:
            continue
        flags = copy_rows(df)
        for ts, is_copy in zip(df.index, flags.to_numpy()):
            d = ts.date()
            rows[d] = rows.get(d, 0) + 1
            if is_copy:
                copies[d] = copies.get(d, 0) + 1
    return {d: (copies[d], n) for d, n in rows.items() if copies.get(d, 0) >= MISSING_DATA_SHARE * n}


def drop_copy_rows(df: pd.DataFrame, days) -> pd.DataFrame:
    """df without its copy rows on the given missing-data days (real bars on those days stay)"""
    if not days or df is None or df.empty:
        return df
    flags = copy_rows(df).to_numpy()
    keep = [not (f and ts.date() in days) for ts, f in zip(df.index, flags)]
    return df[keep]
