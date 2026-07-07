import sys
import warnings
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")

# 🔌 เชื่อมต่อ config.py
sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import ADTV_MIN_MB, HISTORY_DIR, WYCKOFF_FILE
from utils import calculate_adtv, load_price_volume


def scan_all_4_stages():
    if not HISTORY_DIR.exists():
        print("❌ ไม่พบโฟลเดอร์ history")
        return

    print("🕵️‍♂️ [Quant Engine] กำลังเอกซเรย์โครงสร้างหุ้น (Wyckoff 4 Stages)...")
    results = []

    for file_path in HISTORY_DIR.glob("*.csv"):
        ticker = file_path.stem
        try:
            price_series, vol_series = load_price_volume(file_path, min_length=252)
            if price_series is None:
                continue

            adtv_mb = calculate_adtv(price_series, vol_series, window=50)
            if adtv_mb < ADTV_MIN_MB:
                continue

            sma_150 = price_series.rolling(window=150).mean()
            cur_sma_150 = float(sma_150.iloc[-1])
            past_sma_150 = float(sma_150.iloc[-20])
            slope_pct = ((cur_sma_150 - past_sma_150) / past_sma_150) * 100

            curr_price = float(price_series.iloc[-1])
            high_52w = float(price_series.tail(252).max())
            low_52w = float(price_series.tail(252).min())

            if high_52w == low_52w:
                continue

            position_in_52w = ((curr_price - low_52w) / (high_52w - low_52w)) * 100

            stage = "Unknown"
            stage_rank = 5

            if slope_pct > 1.5 and curr_price > cur_sma_150:
                stage = "Stage 2 (Advancing)"
                stage_rank = 2
            elif slope_pct < -1.5 and curr_price < cur_sma_150:
                stage = "Stage 4 (Declining)"
                stage_rank = 4
            elif -1.5 <= slope_pct <= 1.5:
                if position_in_52w <= 50.0:
                    # 💡 เพิ่มเงื่อนไข Volume Dry Up: วอลุ่ม 20 วันล่าสุด < วอลุ่มเฉลี่ย 150 วัน
                    vol_20_mean = vol_series.tail(20).mean()
                    vol_150_mean = vol_series.tail(150).mean()

                    if vol_20_mean < vol_150_mean:
                        stage = "Stage 1 (Accumulation - Dry Vol ⭐)"
                    else:
                        stage = "Stage 1 (Accumulation)"
                    stage_rank = 1
                else:
                    stage = "Stage 3 (Distribution)"
                    stage_rank = 3
            else:
                if slope_pct > 0 and curr_price < cur_sma_150:
                    stage = "Stage 2/3 (Warning/Pullback)"
                    stage_rank = 2.5
                elif slope_pct < 0 and curr_price > cur_sma_150:
                    stage = "Stage 4/1 (Early Recovery)"
                    stage_rank = 4.5

            results.append(
                {
                    "Ticker": ticker,
                    "Stage": stage,
                    "Price": round(curr_price, 2),
                    "SMA_150": round(cur_sma_150, 2),
                    "Slope_1M(%)": round(slope_pct, 2),
                    "Zone_1Yr(%)": round(position_in_52w, 1),
                    "ADTV(MB)": round(adtv_mb, 1),
                    "_Rank": stage_rank,
                }
            )
        except Exception as e:
            # print(f"⚠️ Error processing {ticker}: {e}")
            pass

    print("\n" + "=" * 90)
    print(" 📊 THE WYCKOFF 4 STAGES MARKET MAP")
    print("=" * 90)

    if results:
        df_final = pd.DataFrame(results)
        df_final = df_final.sort_values(
            by=["_Rank", "ADTV(MB)"], ascending=[True, False]
        ).drop(columns=["_Rank"])

        df_final.to_csv(WYCKOFF_FILE, index=False, encoding="utf-8-sig")

        stages_to_show = [
            "Stage 1 (Accumulation - Dry Vol ⭐)",
            "Stage 1 (Accumulation)",
            "Stage 2 (Advancing)",
            "Stage 3 (Distribution)",
            "Stage 4 (Declining)",
            "Stage 4/1 (Early Recovery)",
            "Stage 2/3 (Warning/Pullback)",
        ]

        for st in stages_to_show:
            df_stage = df_final[df_final["Stage"] == st].copy()
            if not df_stage.empty:
                print(f"\n📦 หมวดหมู่: {st} (จำนวน {len(df_stage)} ตัว)")
                print("-" * 90)
                df_display = df_stage.drop(columns=["Stage"])
                df_display.reset_index(drop=True, inplace=True)
                df_display.index = df_display.index + 1

                if len(df_display) > 15:
                    print(df_display.head(15).to_string())
                else:
                    print(df_display.to_string())

        print("\n✨ วิเคราะห์โครงสร้างหุ้นเสร็จสิ้น")
    else:
        print("📉 ไม่มีข้อมูลเพียงพอสำหรับการวิเคราะห์")


if __name__ == "__main__":
    scan_all_4_stages()
