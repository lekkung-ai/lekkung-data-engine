import sys
import warnings
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")

# 🔌 เชื่อมต่อ config.py
sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import (ACCUM_FILE, ADTV_MIN_MB, BASE_WINDOW, FLAT_SLOPE_RANGE,
                    HISTORY_DIR, MAX_BASE_WIDTH, NEAR_BREAK_PCT)
from utils import calculate_adtv, load_price_volume


def scan_accumulation_base():
    if not HISTORY_DIR.exists():
        print("❌ ไม่พบโฟลเดอร์ history")
        return

    print(
        "🕵️‍♂️ [Quant Engine] กำลังสแกนหาหุ้นสะสมพลัง (ตัดหุ้นบาร์โค้ด/ไร้สภาพคล่องทิ้ง)..."
    )
    passed_stocks = []

    for file_path in HISTORY_DIR.glob("*.csv"):
        ticker = file_path.stem
        try:
            price_series, vol_series = load_price_volume(
                file_path, min_length=BASE_WINDOW
            )
            if price_series is None:
                continue

            # 🛡️ 0. ตัวกรองสภาพคล่อง
            adtv_mb = calculate_adtv(price_series, vol_series, window=50)

            if adtv_mb < ADTV_MIN_MB:
                continue

            # 📐 1. วิเคราะห์กรอบ
            recent_window_days = price_series.tail(BASE_WINDOW)
            base_high = float(recent_window_days.max())
            base_low = float(recent_window_days.min())
            base_width_pct = ((base_high - base_low) / base_low) * 100

            # 📈 2. วิเคราะห์เส้นค่าเฉลี่ย
            sma_150 = price_series.rolling(window=150).mean()
            cur_sma_150 = float(sma_150.iloc[-1])
            past_sma_150 = float(sma_150.iloc[-20])
            slope_pct = ((cur_sma_150 - past_sma_150) / past_sma_150) * 100
            sma_price_change = cur_sma_150 - past_sma_150

            # 🚀 3. หาจุดจ่อเบรกเอาท์
            curr_price = float(price_series.iloc[-1])
            dist_to_breakout_pct = ((base_high - curr_price) / base_high) * 100

            # --- เงื่อนไขการกรอง (อ้างอิงจาก config) ---
            is_tight_base = base_width_pct <= MAX_BASE_WIDTH
            is_stage_1_flat = -FLAT_SLOPE_RANGE <= slope_pct <= FLAT_SLOPE_RANGE
            is_ready_to_break = 0 <= dist_to_breakout_pct <= NEAR_BREAK_PCT
            is_above_sma = curr_price > cur_sma_150

            if all([is_tight_base, is_stage_1_flat, is_ready_to_break, is_above_sma]):
                passed_stocks.append(
                    {
                        "Ticker": ticker,
                        "Price": round(curr_price, 2),
                        "Box_Low": round(base_low, 2),
                        "Box_High(Break)": round(base_high, 2),
                        "To_Break": f"{round(dist_to_breakout_pct, 2)}%",
                        "ADTV(MB)": round(adtv_mb, 1),
                        "Box_Width": f"{round(base_width_pct, 2)}%",
                        "SMA150_Chg": round(sma_price_change, 3),
                    }
                )
        except Exception as e:
            # print(f"⚠️ Error processing {ticker}: {e}")
            pass

    print("\n" + "=" * 95)
    print("📦 WYCKOFF ACCUMULATION & STAGE 1 BREAKOUT WATCHLIST")
    print("=" * 95)

    if passed_stocks:
        df_final = pd.DataFrame(passed_stocks)
        df_final["Sort_Val"] = df_final["To_Break"].str.replace("%", "").astype(float)
        df_final = df_final.sort_values(by="Sort_Val", ascending=True).drop(
            columns=["Sort_Val"]
        )

        # 🎯 เซฟผ่าน Config
        df_final.to_csv(ACCUM_FILE, index=False, encoding="utf-8-sig")
        df_final.reset_index(drop=True, inplace=True)
        df_final.index = df_final.index + 1

        print(df_final.to_string())
        print("-" * 95)
        print(f"✨ พบหุ้นสะสมพลัง (สภาพคล่องสูง) จ่อเบรกแนวต้าน: {len(df_final)} ตัว")
    else:
        print("📉 ไม่พบหุ้นสภาพคล่องสูงที่เข้าข่ายกำลังสร้างฐานจ่อเบรกเอาท์ในขณะนี้")


if __name__ == "__main__":
    scan_accumulation_base()
