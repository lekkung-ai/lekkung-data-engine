from pathlib import Path
from typing import Optional, Tuple, Union

import pandas as pd


def load_price_volume(
    file_path: Union[str, Path], min_length: int = 0
) -> Tuple[Optional[pd.Series], Optional[pd.Series]]:
    """
    อ่านไฟล์ CSV และเตรียมข้อมูล Price/Volume อย่างรวดเร็ว
    แก้ปัญหาการโหลดไฟล์ช้าจากการอ่านไฟล์ซ้ำซ้อน

    Args:
        file_path: Path to the CSV file.
        min_length: Minimum number of rows required.

    Returns:
        Tuple of (price_series, vol_series) or (None, None) if criteria not met.
    """
    # เรียกใช้ load_full_df เพื่อลดความซ้ำซ้อนของโค้ดอ่านไฟล์
    df = load_full_df(file_path)

    # load_full_df จัดการ Header ให้แล้ว จึงเรียกชื่อคอลัมน์ได้ตรงๆ
    close_col = next((c for c in df.columns if "Close" in c), "Close")
    vol_col = next((c for c in df.columns if "Volume" in c), "Volume")

    price_series = pd.to_numeric(df[close_col], errors="coerce").dropna()
    vol_series = pd.to_numeric(df[vol_col], errors="coerce").fillna(0)

    if len(price_series) < min_length:
        return None, None

    return price_series, vol_series


def load_full_df(file_path: Union[str, Path]) -> pd.DataFrame:
    """
    อ่านไฟล์ CSV และคืนค่าเป็น DataFrame เต็มรูปแบบ (มีครบทุกคอลัมน์ Date, O, H, L, C, V)
    พร้อมจัดการปัญหา Multi-Index Header แบบอัตโนมัติ

    Args:
        file_path: Path to the CSV file.

    Returns:
        pd.DataFrame containing the parsed stock data.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        first_line = f.readline()

    header_idx = [0, 1] if "Price" in first_line else 0
    df = pd.read_csv(file_path, header=header_idx)

    # ถ้ายอดคอลัมน์เป็น Multi-Index (เช่น มีชั้น Price, Ticker) ให้ยุบเหลือแค่ชั้นเดียว
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [str(col[0]).strip() for col in df.columns]

    return df


def calculate_adtv(
    price_series: pd.Series, vol_series: pd.Series, window: int = 50
) -> float:
    """
    คำนวณมูลค่าการซื้อขายเฉลี่ยต่อวัน (ADTV) เป็นหน่วยล้านบาท

    Args:
        price_series: Series containing closing prices.
        vol_series: Series containing volume.
        window: The rolling window for calculation.

    Returns:
        Average Daily Trading Volume in millions of baht.
    """
    return (price_series.tail(window) * vol_series.tail(window)).mean() / 1_000_000


def prepare_stock_data(
    file_path: Union[str, Path], min_days: int = 250
) -> Tuple[Optional[pd.DataFrame], Optional[float]]:
    """
    อ่านไฟล์ CSV และเตรียม DataFrame พื้นฐานสำหรับการสแกนหุ้น
    รวมถึงจัดการเรื่องคอลัมน์ที่จำเป็น แปลงชนิดข้อมูล และเช็คความยาวข้อมูล

    Args:
        file_path: Path to the CSV file.
        min_days: Minimum required trading days in the history.

    Returns:
        Tuple of (clean_dataframe, adtv_mb) or (None, None) if criteria not met.
    """
    df = load_full_df(file_path)
    if df is None or len(df) < min_days:
        return None, None

    required_cols = ["Close", "High", "Low", "Volume"]
    if not all(col in df.columns for col in required_cols):
        # Fallback to load_price_volume if only Close/Volume needed?
        # For simplicity, if High/Low is missing, scanners needing them will fail gracefully.
        return None, None

    for col in required_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df.dropna(subset=required_cols, inplace=True)

    if len(df) < min_days:
        return None, None

    adtv_mb = calculate_adtv(df["Close"], df["Volume"], window=50)
    return df, adtv_mb
