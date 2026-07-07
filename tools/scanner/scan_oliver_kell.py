import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

# 🔌 เชื่อมต่อระบบเดิมของคุณ (ดึง Path จาก config.py)
sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import ADTV_MIN_MB, HISTORY_DIR, RESULTS_DIR  # type: ignore
from utils import calculate_adtv, load_price_volume  # type: ignore


def scan_oliver_kell_strict():
    # กำหนดไฟล์ผลลัพธ์
    output_path = RESULTS_DIR / "oliver_kell_signals.csv"

    if not HISTORY_DIR.exists():
        print(f"❌ ไม่พบโฟลเดอร์ history ที่: {HISTORY_DIR}")
        return

    print(
        f"🏆 [U.S. Champion Logic] กำลังสแกนหา 'หุ้นผู้นำ' ตามสูตร Strict Oliver Kell... ({datetime.now().strftime('%H:%M')})"
    )
    passed_stocks = []

    # วนลูปอ่านไฟล์หุ้นทีละตัวจากโฟลเดอร์ history
    for file_path in HISTORY_DIR.glob("*.csv"):
        ticker = file_path.stem
        try:
            # เปลี่ยน min_length เป็น 220 เพราะเราต้องเช็คเส้น SMA200 ย้อนหลังไปอีก 20 วัน
            price_series, vol_series = load_price_volume(file_path, min_length=220)
            if price_series is None:
                continue

            # --- [ด่านที่ 1] Liquidity Filter (สภาพคล่อง) ---
            vol_mb = calculate_adtv(price_series, vol_series, window=5)

            if vol_mb < ADTV_MIN_MB:
                continue  # เขี่ยหุ้นป่าช้าทิ้งทันที

            # --- คำนวณเส้นค่าเฉลี่ย ---
            ema10 = price_series.ewm(span=10, adjust=False).mean()
            ema20 = price_series.ewm(span=20, adjust=False).mean()
            sma50 = price_series.rolling(window=50).mean()
            sma200 = price_series.rolling(window=200).mean()

            curr_price = float(price_series.iloc[-1])

            # --- [ด่านที่ 2] Trend Template (ต้องเป็นขาขึ้น Stage 2) ---
            is_stage_2 = (curr_price > float(sma50.iloc[-1])) and (
                float(sma50.iloc[-1]) > float(sma200.iloc[-1])
            )
            is_ma_uptrend = float(sma200.iloc[-1]) > float(
                sma200.iloc[-20]
            )  # SMA200 ต้องชันขึ้น

            if not (is_stage_2 and is_ma_uptrend):
                continue

            # --- [ด่านที่ 3] Kell's Signal (จังหวะเข้าทำ) ---
            curr_ema10 = float(ema10.iloc[-1])
            curr_ema20 = float(ema20.iloc[-1])
            
            # 1. ราคาต้องยืนเหนือ 10 EMA และ 20 EMA
            is_price_above_ema = (curr_price > curr_ema10) and (curr_price > curr_ema20)
            
            # 2. แนวโน้มระยะสั้นเรียงตัวสวยงาม และมีความชันเป็นบวก
            is_aligned = curr_ema10 > curr_ema20
            is_ema_sloping_up = (curr_ema10 > float(ema10.iloc[-5])) and (curr_ema20 > float(ema20.iloc[-5]))
            
            # 3. ความแน่น (Tightness): ราคาพักตัวใกล้ 10 EMA หรือ 20 EMA ไม่เกิน 3.5%
            dist_ema10_pct = abs(curr_price - curr_ema10) / curr_ema10 * 100
            dist_ema20_pct = abs(curr_price - curr_ema20) / curr_ema20 * 100
            is_tight_to_10 = dist_ema10_pct <= 3.5
            is_tight_to_20 = dist_ema20_pct <= 3.5
            is_tight = is_tight_to_10 or is_tight_to_20

            # 4. เช็คจังหวะเพิ่งตัดขึ้น (EMAC)
            emac_cross = (float(ema10.iloc[-2]) <= float(ema20.iloc[-2])) and is_aligned

            if is_price_above_ema and is_aligned and is_ema_sloping_up and is_tight:
                signal_type = "EMAC Buy" if emac_cross else "Trend Riding"
                
                # ใช้ค่าที่ใกลัที่สุดสำหรับการแสดงผล
                extension_pct = min(dist_ema10_pct, dist_ema20_pct)

                passed_stocks.append(
                    {
                        "Ticker": ticker,
                        "Signal": signal_type,
                        "Price": round(curr_price, 2),
                        "EMA10": round(curr_ema10, 2),
                        "Dist_EMA10_%": round(extension_pct, 2),
                        "ADTV(MB)": round(vol_mb, 1),
                        "Status": "🔥 Leader Ready",
                    }
                )

        except Exception as e:
            # ข้ามตัวที่มีปัญหา (เช่น ข้อมูลแหว่ง)
            pass

    # แสดงผลลัพธ์
    if passed_stocks:
        df_final = pd.DataFrame(passed_stocks)
        # เรียงตามความใกล้เส้น 10 EMA (ยิ่งใกล้ ยิ่งเสี่ยงต่ำ)
        df_final = df_final.sort_values(by="Dist_EMA10_%", ascending=True)

        df_final.to_csv(output_path, index=False, encoding="utf-8-sig")
        print(f"\n✅ สแกนเสร็จสิ้น! พบหุ้น 'ตัวจริง' ทั้งหมด {len(df_final)} ตัว")
        print("=" * 65)
        print(df_final.head(15).to_string(index=False))  # โชว์แค่ 15 ตัวแรก
        print("=" * 65)
        print(f"📁 บันทึกผลลัพธ์ไว้ที่: {output_path.name}")
    else:
        print(
            "\n📉 วันนี้ยังไม่พบหุ้นที่เข้าสูตรของแชมป์ (ตลาดอาจพักตัว หรือไม่มี Leader ชัดเจน)"
        )


if __name__ == "__main__":
    scan_oliver_kell_strict()
