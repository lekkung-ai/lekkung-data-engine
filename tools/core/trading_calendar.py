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
"""

import sys
from datetime import date
from pathlib import Path
from typing import Optional

sys.path.append(str(Path(__file__).resolve().parents[2]))
from tools.scanner.utils import NON_STOCK_FILES, last_traded_date  # noqa: E402

SET_INDEX_FILE = "SET_INDEX.csv"


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
