"""RS Score (IBD-proxy) — กฎเดียวของ RS_Raw / RS_Rating ทั้ง data-engine

3_calculate_rs.py ใช้กับวันล่าสุด (rs_ranking.csv) · scan_rs7.py เรียกฟังก์ชันเดียวกันกับ series ที่ตัด
ถึงแต่ละวันเพื่อได้ RS Rating ย้อนหลัง — ห้ามเขียนสูตร RS ชุดที่สองที่อื่น

IBD-proxy RS: 3/6/9/12-month returns weighted 0.4/0.2/0.2/0.2 (latest quarter counts double).
Stocks with less than the full 252 days still get scored on whichever of these periods actually
fit, as long as at least the 126-day period is available (below that, exclude entirely).
"""
from pathlib import Path
from typing import Optional

import pandas as pd

PERIODS = [63, 126, 189, 252]
WEIGHTS = [0.4, 0.2, 0.2, 0.2]
MIN_DAYS = 126


def load_close_series(file_path: Path) -> pd.Series:
    """Close ของไฟล์ history (รองรับ header 2 ชั้นแบบ yfinance) · to_numeric + dropna"""
    with open(file_path, "r", encoding="utf-8") as f:
        first_line = f.readline()
    df = pd.read_csv(file_path, header=[0, 1] if "Price" in first_line else 0)
    if isinstance(df.columns, pd.MultiIndex):
        close_col = [col for col in df.columns if "Close" in col[0]][0]
        return pd.to_numeric(df[close_col], errors="coerce").dropna()
    return pd.to_numeric(df["Close"], errors="coerce").dropna()


def rs_raw(series: pd.Series) -> Optional[float]:
    """RS_Raw ของแท่งสุดท้ายใน series (นับแท่งของหุ้นเอง) · None = ประวัติไม่พอ"""
    n = len(series)
    if n < MIN_DAYS:
        return None

    current_close = float(series.iloc[-1])

    # Use whichever lookback periods actually fit in the available
    # history, renormalizing the remaining weights so they still sum
    # to 1.0 — keeps RS_raw a fair weighted average regardless of how
    # many periods a newer listing can supply.
    weighted_sum = 0.0
    weight_total = 0.0
    for period, weight in zip(PERIODS, WEIGHTS):
        if n < period:
            continue
        c_ago = float(series.iloc[-period])
        r = (current_close - c_ago) / c_ago
        weighted_sum += r * weight
        weight_total += weight

    if weight_total == 0:
        return None
    return weighted_sum / weight_total


def rs_ratings(raw: pd.Series, is_fund: pd.Series) -> pd.Series:
    """RS_Rating 1-99 (Int64) จาก RS_Raw · กองทุน/REIT ไม่ร่วมจัดอันดับ (= <NA>)"""
    # IBD-style 1-99 percentile rank. rank(pct=True) tops out at exactly 1.0
    # for the single highest-ranked stock, which would land on 100 — clip it
    # back down to 99 so the whole scale stays 1-99.
    raw_rating = raw[~is_fund].rank(pct=True) * 99 + 1
    return raw_rating.round().clip(upper=99).astype("Int64").reindex(raw.index)
