"""
growth_period.py — "is this financial period still the latest one a company should have reported?"
Shared by 2_download_history.py (growth sources), scan_lekkung_growth.py (F45 / dynamic fetch)
and scripts/health_check.py (H13).

SET filing deadlines: quarterly statements within 45 days of the quarter end, annual statements
within 60 days of the fiscal year end (Q4 has no separate quarterly filing). One more period is
"due" GRACE_DAYS after its deadline - from then on a growth figure computed from the period before
it is stale (e.g. on 2026-10-23 Q2/26 = 2026-06-30 is due since 2026-08-21, so a Q1/26 figure is
stale; Q3/26 only becomes due on 2026-11-21).

A stale growth figure is never used: callers try the next source, and with no fresh source the
value stays null and the ticker is labelled (lekkung_incomplete "stale: ...") instead of filled in.

Fiscal year end month: the month of the company's annual period end (Yahoo .financials column /
TradingView fiscal_period_end_fy) - decides which quarter end gets the 60-day annual deadline.
Unknown → December (most SET companies).
"""

from datetime import date, timedelta
from typing import Any, Optional

import pandas as pd

QUARTER_DEADLINE_DAYS = 45
ANNUAL_DEADLINE_DAYS = 60
GRACE_DAYS = 7
DEFAULT_FY_END_MONTH = 12

THAI_MONTHS = {
    "มกราคม": 1, "กุมภาพันธ์": 2, "มีนาคม": 3, "เมษายน": 4, "พฤษภาคม": 5, "มิถุนายน": 6,
    "กรกฎาคม": 7, "สิงหาคม": 8, "กันยายน": 9, "ตุลาคม": 10, "พฤศจิกายน": 11, "ธันวาคม": 12,
}


def to_date(v: Any) -> Optional[date]:
    """date / Timestamp / 'YYYY-MM-DD...' / unix seconds → date · NaN/None/garbage → None"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (int, float)):
        return pd.Timestamp(int(v), unit="s").date()
    try:
        ts = pd.Timestamp(v)
    except (ValueError, TypeError):
        return None
    return None if pd.isna(ts) else ts.date()


def parse_thai_date(text: Any) -> Optional[date]:
    """F45 periodEnd "30 กันยายน 2569" (พ.ศ.) → date(2026, 9, 30) · อ่านไม่ได้ → None"""
    parts = str(text or "").split()
    if len(parts) != 3 or parts[1] not in THAI_MONTHS:
        return None
    try:
        year = int(parts[2])
        return date(year - 543 if year > 2400 else year, THAI_MONTHS[parts[1]], int(parts[0]))
    except ValueError:
        return None


def _month_end(year: int, month: int) -> date:
    return (pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)).date()


def _add_months(d: date, months: int) -> date:
    m = d.year * 12 + d.month - 1 + months
    return _month_end(m // 12, m % 12 + 1)


def fy_end_month(*candidates: Any) -> int:
    """month of the first readable annual period end · none → DEFAULT_FY_END_MONTH"""
    for c in candidates:
        d = to_date(c)
        if d is not None:
            return d.month
    return DEFAULT_FY_END_MONTH


def _due_date(period_end: date, fy_month: int) -> date:
    deadline = ANNUAL_DEADLINE_DAYS if period_end.month == fy_month else QUARTER_DEADLINE_DAYS
    return period_end + timedelta(days=deadline + GRACE_DAYS)


def quarter_is_fresh(period_end: Any, today: date, fy_month: int = DEFAULT_FY_END_MONTH) -> bool:
    """True = the quarter after period_end is not due yet → period_end is still the latest expected"""
    d = to_date(period_end)
    return d is not None and today <= _due_date(_add_months(d, 3), fy_month)


def annual_is_fresh(fy_end: Any, today: date) -> bool:
    """True = the next fiscal year's statements are not due yet"""
    d = to_date(fy_end)
    return d is not None and today <= _add_months(d, 12) + timedelta(days=ANNUAL_DEADLINE_DAYS + GRACE_DAYS)


def expected_quarter_end(today: date, fy_month: int = DEFAULT_FY_END_MONTH) -> date:
    """latest quarter end (of this fiscal calendar) that is already due on today"""
    q = _month_end(today.year, today.month)
    while q.month % 3 != fy_month % 3:
        q = _add_months(q, -1)
    while _due_date(q, fy_month) >= today:
        q = _add_months(q, -3)
    return q


def quarter_label(period_end: Any) -> Optional[str]:
    """2026-06-30 → "2026Q2" (calendar quarter of the period end month)"""
    d = to_date(period_end)
    return None if d is None else f"{d.year}Q{(d.month - 1) // 3 + 1}"


def short_quarter(period_end: Any) -> str:
    """2026-06-30 → "Q2/26" (reasons / logs)"""
    d = to_date(period_end)
    return "?" if d is None else f"Q{(d.month - 1) // 3 + 1}/{d.year % 100:02d}"


def short_fy(fy_end: Any) -> str:
    """2025-12-31 → "FY25" """
    d = to_date(fy_end)
    return "?" if d is None else f"FY{d.year % 100:02d}"
