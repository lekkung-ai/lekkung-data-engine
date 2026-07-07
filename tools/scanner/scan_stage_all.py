"""
scan_stage_all.py
คำนวณ Market Stage สำหรับทุกหุ้นที่มีประวัติราคา >= 201 แท่ง
โดยไม่มีตัวกรอง ADTV (สำหรับ SectorTickerGrid ใน dashboard)
Output: stage_all.csv -> stage_all.json
"""
import pandas as pd
import numpy as np
import warnings
import sys
from pathlib import Path

warnings.filterwarnings('ignore')

sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import HISTORY_DIR
from data_utils import load_close_volume

OUTPUT_FILE = Path(__file__).resolve().parents[2] / 'data' / 'results' / 'stage_all.csv'


def tv_ema(series, length):
    if len(series) < length:
        return pd.Series(np.nan, index=series.index)
    sma = series.rolling(window=length, min_periods=length).mean()
    mod = series.copy()
    mod.iloc[:length - 1] = np.nan
    mod.iloc[length - 1] = sma.iloc[length - 1]
    return mod.ewm(span=length, adjust=False).mean()


def get_stage(p, e15, e50, e200):
    if p > e50 and p < e200 and e50 < e200: return "Recovery"
    if p > e50 and p > e200 and e50 < e200: return "Accumulation"
    if p > e50 and p > e200 and e50 > e200:
        if p > e15 and e15 > e50: return "S.Bull"
        return "Bull"
    if p < e50 and p > e200 and e50 > e200: return "Warning"
    if p < e50 and p < e200 and e50 > e200: return "Distribution"
    if p < e50 and p < e200 and e50 < e200: return "Bear"
    return "Unknown"


def scan_stage_all():
    if not HISTORY_DIR.exists():
        print("ไม่พบโฟลเดอร์ history")
        return

    print("คำนวณ Stage ทุกหุ้น (ไม่มี ADTV filter)...")
    results = []

    for file_path in sorted(HISTORY_DIR.glob("*.csv")):
        ticker = file_path.stem
        try:
            loaded = load_close_volume(file_path, min_len=201)
            if loaded is None:
                continue
            p_series, _ = loaded

            e15 = tv_ema(p_series, 15)
            e50 = tv_ema(p_series, 50)
            e200 = tv_ema(p_series, 200)

            last = len(p_series) - 1
            if pd.isna(e200.iloc[last]):
                continue

            stage = get_stage(
                p_series.iloc[last],
                e15.iloc[last],
                e50.iloc[last],
                e200.iloc[last],
            )

            chg30d = None
            if last >= 30:
                p_30d = float(p_series.iloc[last - 30])
                if p_30d > 0:
                    chg30d = round(((float(p_series.iloc[last]) - p_30d) / p_30d) * 100, 2)

            results.append({
                "Ticker": ticker,
                "Stage": stage,
                "Price": round(p_series.iloc[last], 2),
                "Chg30D": chg30d,
            })
        except Exception:
            pass

    if results:
        df = pd.DataFrame(results).sort_values("Ticker")
        df.to_csv(OUTPUT_FILE, index=False, encoding='utf-8-sig')
        print(f"เสร็จ: {len(results)} หุ้น -> {OUTPUT_FILE}")
        stage_counts = df['Stage'].value_counts()
        print(stage_counts.to_string())
    else:
        print("ไม่มีข้อมูล")


if __name__ == "__main__":
    scan_stage_all()
