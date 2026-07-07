"""
data_utils.py — Shared helpers for the data_engine
==================================================
รวมโค้ดที่เคยซ้ำๆ กันในทุก scanner ไว้ที่เดียว:
  - การอ่านไฟล์ประวัติราคา (history CSV)
  - การคำนวณสภาพคล่อง (ADTV)
  - การยิง HTTP request ที่มี timeout เสมอ (กันค้าง)

ทุก script ใน tools/* เพิ่ม data_engine เข้า sys.path อยู่แล้ว
จึง import ได้ตรงๆ ด้วย:  from data_utils import load_close_series, adtv_mb, ...
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

import pandas as pd
import requests

# ค่า timeout เริ่มต้น (วินาที) สำหรับทุก network call
DEFAULT_TIMEOUT = 15


# ── HISTORY CSV LOADING ───────────────────────────────────────────────────────

def load_history(path: Path) -> Optional[pd.DataFrame]:
    """
    อ่านไฟล์ประวัติราคาหนึ่งตัว (Date, Open, High, Low, Close, Volume, ...)
    คืนค่า DataFrame หรือ None ถ้าอ่านไม่ได้ / ไม่มีคอลัมน์ Close

    หมายเหตุ: ไฟล์ history ที่ระบบสร้าง (2_download_history.py) เป็น header
    บรรทัดเดียวเสมอ จึงอ่านแบบตรงไปตรงมา ไม่ต้องเดา MultiIndex อีกต่อไป
    """
    try:
        df = pd.read_csv(path)
    except Exception:
        return None
    if "Close" not in df.columns:
        return None
    return df


def load_close_series(path: Path, min_len: int = 0) -> Optional[pd.Series]:
    """คืน Series ราคาปิด (ตัด NaN ออก) — None ถ้าข้อมูลสั้นกว่า min_len"""
    df = load_history(path)
    if df is None:
        return None
    close = pd.to_numeric(df["Close"], errors="coerce").dropna()
    if len(close) < min_len:
        return None
    return close


def load_close_volume(path: Path, min_len: int = 0) -> Optional[Tuple[pd.Series, pd.Series]]:
    """
    คืน (close, volume) — close ตัด NaN, volume เติม 0 แทน NaN
    None ถ้าข้อมูลไม่พอ หรือไม่มีคอลัมน์ Volume
    """
    df = load_history(path)
    if df is None or "Volume" not in df.columns:
        return None
    close = pd.to_numeric(df["Close"], errors="coerce").dropna()
    if len(close) < min_len:
        return None
    volume = pd.to_numeric(df["Volume"], errors="coerce").fillna(0)
    return close, volume


def adtv_mb(close: pd.Series, volume: pd.Series, window: int = 50) -> float:
    """
    Average Daily Turnover (มูลค่าซื้อขายเฉลี่ย) หน่วยล้านบาท
    คำนวณจาก ราคา x ปริมาณ ของ N วันล่าสุด
    """
    return float((close.tail(window) * volume.tail(window)).mean()) / 1_000_000


# ── SAFE HTTP ─────────────────────────────────────────────────────────────────

def http_get(url: str, **kwargs) -> requests.Response:
    """requests.get ที่ใส่ timeout ให้อัตโนมัติ (กัน request ค้างทั้ง pipeline)"""
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    return requests.get(url, **kwargs)


def http_post(url: str, **kwargs) -> requests.Response:
    """requests.post ที่ใส่ timeout ให้อัตโนมัติ"""
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    return requests.post(url, **kwargs)
