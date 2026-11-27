"""RS line (เส้นความแข็งแกร่งเทียบดัชนี) — กฎเดียวของ RS line ทั้ง data-engine

RS line = Close หุ้น ÷ Close ดัชนี (SET) วันเดียวกัน · ไม่ใช่ RS Rating (นั่นคือ tools/core/rs_score.py)
RS line ขึ้น = หุ้นชนะดัชนี แม้ราคาหุ้นจะลง

ตัวแปรตามสเปก RS-7 (window ค่าตั้งต้น 252 วัน = 52 สัปดาห์):
  rs_nh   RS ≥ สูงสุดของ RS 252 วันก่อนหน้า (ไม่รวมวันนี้)  → RS line new high (เทียบเท่า blue dot)
  rs_near RS ≥ near × สูงสุด RS 252 วัน (รวมวันนี้)          → RS ห่าง high ไม่เกิน 1 − near
  rs_gap  RS ÷ สูงสุด RS 252 วัน − 1                         → RS ต่ำกว่า high กี่ %
"""
import pandas as pd

RS_WINDOW = 252
RS_MIN_PERIODS = 120


def rs_line(close: pd.Series, index_close: pd.Series) -> pd.Series:
    """close / index_close บนวันที่ของหุ้น · ดัชนีไม่มีแท่งวันนั้น (เช่นแถววันหยุดในไฟล์หุ้น) ใช้ค่าล่าสุดก่อนหน้า"""
    idx = index_close.reindex(close.index.union(index_close.index)).sort_index().ffill().reindex(close.index)
    return close / idx


def rs_high(rs: pd.Series, window: int = RS_WINDOW, min_periods: int = RS_MIN_PERIODS) -> pd.Series:
    return rs.rolling(window, min_periods=min_periods).max()


def rs_nh(rs: pd.Series, window: int = RS_WINDOW, min_periods: int = RS_MIN_PERIODS) -> pd.Series:
    return rs >= rs.shift(1).rolling(window, min_periods=min_periods).max()


def rs_near(rs: pd.Series, near: float = 0.97, window: int = RS_WINDOW,
            min_periods: int = RS_MIN_PERIODS) -> pd.Series:
    return rs >= near * rs_high(rs, window, min_periods)


def rs_gap(rs: pd.Series, window: int = RS_WINDOW, min_periods: int = RS_MIN_PERIODS) -> pd.Series:
    return rs / rs_high(rs, window, min_periods) - 1
