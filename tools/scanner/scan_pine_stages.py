import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# 🔌 เชื่อมต่อ config.py
sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import ADTV_MIN_MB, HISTORY_DIR, PINE_STAGES_FILE
from utils import calculate_adtv, load_price_volume


def tv_ema(series, length):
    if len(series) < length:
        return pd.Series(np.nan, index=series.index)
    sma = series.rolling(window=length, min_periods=length).mean()
    mod_series = series.copy()
    mod_series.iloc[: length - 1] = np.nan
    mod_series.iloc[length - 1] = sma.iloc[length - 1]
    return mod_series.ewm(span=length, adjust=False).mean()


def get_stage(p, e15, e50, e200):
    if pd.isna(e200) or pd.isna(p) or pd.isna(e50):
        return "UNKNOWN"
    if p > e50 and p < e200 and e50 < e200:
        return "Recovery"
    if p > e50 and p > e200 and e50 < e200:
        return "Accumulation"
    if p > e50 and p > e200 and e50 > e200:
        if p > e15 and e15 > e50:
            return "S.Bull"
        return "Bull"
    if p < e50 and p > e200 and e50 > e200:
        return "Warning"
    if p < e50 and p < e200 and e50 > e200:
        return "Distribution"
    if p < e50 and p < e200 and e50 < e200:
        return "Bear"
    return "UNKNOWN"


def scan_pine_script_stages():
    if not HISTORY_DIR.exists():
        print("❌ ไม่พบโฟลเดอร์ history")
        return

    print("🕵️‍♂️ [Quant Engine] กำลังคำนวณ Market Stage (Pine Script Logic)...")
    results = []

    for file_path in HISTORY_DIR.glob("*.csv"):
        ticker = file_path.stem
        try:
            p_series, v_series = load_price_volume(file_path, min_length=1)
            if p_series is None or len(p_series) == 0:
                continue

            adtv_mb = calculate_adtv(p_series, v_series, window=50)

            ema_15 = tv_ema(p_series, 15)
            ema_50 = tv_ema(p_series, 50)
            ema_200 = tv_ema(p_series, 200)

            idx_last = len(p_series) - 1
            latest_stage = get_stage(
                p_series.iloc[idx_last],
                ema_15.iloc[idx_last],
                ema_50.iloc[idx_last],
                ema_200.iloc[idx_last],
            )

            bar_count = 0
            for i in range(201):
                curr_idx = idx_last - i
                if curr_idx < 0 or pd.isna(ema_200.iloc[curr_idx]):
                    break
                st = get_stage(
                    p_series.iloc[curr_idx],
                    ema_15.iloc[curr_idx],
                    ema_50.iloc[curr_idx],
                    ema_200.iloc[curr_idx],
                )
                if st == latest_stage:
                    bar_count += 1
                elif st == "S.Bull" and latest_stage == "Bull":
                    bar_count += 1
                else:
                    break

            rank_map = {
                "S.Bull": 1,
                "Bull": 2,
                "Accumulation": 3,
                "Recovery": 4,
                "Warning": 5,
                "Distribution": 6,
                "Bear": 7,
                "UNKNOWN": 8,
            }

            results.append(
                {
                    "Ticker": ticker,
                    "Stage": latest_stage,
                    "Price": round(p_series.iloc[-1], 2),
                    "EMA50": round(ema_50.iloc[-1], 2),
                    "EMA200": round(ema_200.iloc[-1], 2),
                    "Bar_Count": bar_count,
                    "ADTV(MB)": round(adtv_mb, 1),
                    "_Rank": rank_map.get(latest_stage, 8),
                }
            )
        except Exception:
            pass

    if results:
        df_final = (
            pd.DataFrame(results)
            .sort_values(by=["_Rank", "ADTV(MB)"], ascending=[True, False])
            .drop(columns=["_Rank"])
        )
        df_final.to_csv(PINE_STAGES_FILE, index=False, encoding="utf-8-sig")

        stages = [
            "S.Bull",
            "Bull",
            "Accumulation",
            "Recovery",
            "Warning",
            "Distribution",
            "Bear",
        ]
        for st in stages:
            df_st = df_final[df_final["Stage"] == st]
            if not df_st.empty:
                print(f"\n🔥 {st} (จำนวน {len(df_st)} ตัว)")
                print(df_st.head(10).drop(columns=["Stage"]).to_string(index=False))
    else:
        print("📉 ไม่มีข้อมูล")


if __name__ == "__main__":
    scan_pine_script_stages()
