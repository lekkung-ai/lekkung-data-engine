"""ตัวชี้วัดราคา/วอลุ่มรายวัน — ที่เดียวสำหรับโค้ดใหม่ (scan_luk.py เป็นตัวแรก)

สแกนเดิมยังคำนวณ EMA ของตัวเองแบบ inline (`ewm(span, adjust=False)`) ซึ่งให้ค่าเท่ากับ ema() ที่นี่ —
ย้ายมาใช้ไฟล์นี้เป็นงานรอบอื่น (ต้องพิสูจน์ผลเดิมเหมือนทุกไบต์)

ทุกฟังก์ชันรับ pandas Series ที่เรียงตามวัน และคืน Series ยาวเท่าเดิม (ค่าแต่ละแท่งใช้ข้อมูลถึงแท่งนั้นเท่านั้น)
ยกเว้น avwap() ที่คืนตัวเลขตัวเดียว
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(close: pd.Series, n: int) -> pd.Series:
    """EMA แบบ recursive เริ่มจากค่าแรก (k = 2/(n+1)) = pandas ewm(span=n, adjust=False)"""
    return close.ewm(span=n, adjust=False).mean()


def adr_pct(high: pd.Series, low: pd.Series, n: int = 20) -> pd.Series:
    """ADR% = ค่าเฉลี่ย n แท่งของ (High/Low − 1) × 100 · แท่งที่ Low ≤ 0 ไม่นับ"""
    r = (high / low.where(low > 0) - 1) * 100
    return r.rolling(n, min_periods=1).mean()


def value_avg(close: pd.Series, volume: pd.Series, n: int = 20) -> pd.Series:
    """มูลค่าซื้อขายเฉลี่ย n แท่ง (บาท) = mean(Close × Volume) รวมแท่งนี้"""
    return (close * volume).rolling(n, min_periods=1).mean()


def volume_avg_prior(volume: pd.Series, n: int = 50) -> pd.Series:
    """วอลุ่มเฉลี่ย n แท่ง "ก่อนหน้า" (ไม่รวมแท่งนี้) — ใช้เทียบวอลุ่มวันนี้ว่ากี่เท่า"""
    return volume.shift(1).rolling(n, min_periods=1).mean()


def gap_pct(open_: pd.Series, close: pd.Series) -> pd.Series:
    """Gap% = Open วันนี้ / Close วันก่อน − 1 (×100)"""
    return (open_ / close.shift(1) - 1) * 100


def perf_pct(close: pd.Series, n: int) -> pd.Series:
    """ผลตอบแทน n แท่ง (%) = Close / Close n แท่งก่อน − 1"""
    return (close / close.shift(n) - 1) * 100


def avwap(high: pd.Series | np.ndarray, low: pd.Series | np.ndarray, close: pd.Series | np.ndarray,
          volume: pd.Series | np.ndarray, anchor: int, end: int) -> float | None:
    """Anchored VWAP จากแท่ง anchor ถึง end (รวมทั้งสองแท่ง) ด้วย typical price (H+L+C)/3 · วอลุ่มรวม 0 → None"""
    h, l, c, v = (np.asarray(x, dtype=float)[anchor:end + 1] for x in (high, low, close, volume))
    vv = float(np.nansum(v))
    if vv <= 0:
        return None
    return float(np.nansum((h + l + c) / 3 * v) / vv)
