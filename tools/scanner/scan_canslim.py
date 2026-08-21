import sys
from pathlib import Path

# Force UTF-8 encoding for standard output/error on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore

import pandas as pd
import numpy as np

# 🔌 เชื่อมต่อ config.py
sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import ADTV_MIN_MB, CANSLIM_FILE, DAILY_FILE, HISTORY_DIR, RS_FILE  # type: ignore
from utils import load_full_df, prepare_stock_data  # type: ignore


def scan_canslim():
    """
    สแกนหาหุ้นที่เข้าเกณฑ์ CANSLIM ตามหลักการของ William O'Neil
    """
    output_path = CANSLIM_FILE

    if not HISTORY_DIR.exists() or not DAILY_FILE.exists():
        print("❌ [CANSLIM Scan] ไม่พบไฟล์ข้อมูลตั้งต้น (History หรือ Daily File)")
        return

    # อ่าน RS Score
    rs_dict = {}
    if RS_FILE.exists():
        try:
            df_rs = pd.read_csv(RS_FILE)
            rs_dict = dict(
                zip(df_rs["Ticker"].astype(str).str.upper(), df_rs["RS_Rating"])
            )
        except Exception as e:
            print(f"⚠️ [CANSLIM Scan] ไม่สามารถอ่านไฟล์ RS Rating: {e}")
            pass

    print("🕵️‍♂️ [CANSLIM Scan] เริ่มสแกนตามเกณฑ์ของ William O'Neil...")

    # โหลดข้อมูลงบการเงินจาก Daily Snapshot
    try:
        df_financial = pd.read_csv(DAILY_FILE)
        df_financial.columns = df_financial.columns.str.strip()
        if "Ticker" in df_financial.columns:
            df_financial["Ticker"] = df_financial["Ticker"].str.strip().str.upper()
    except Exception as e:
        print(f"❌ [CANSLIM Scan] ไม่สามารถอ่านข้อมูลปัจจัยพื้นฐาน: {e}")
        return

    passed_stocks = []

    # 1. วนลูปเช็คข้อมูลเชิงเทคนิคผ่านราคาใน History
    for file_path in HISTORY_DIR.glob("*.csv"):
        ticker = file_path.stem.strip().upper()
        try:
            df_price, adtv_mb = prepare_stock_data(file_path, min_days=252)
            if df_price is None or adtv_mb < ADTV_MIN_MB:
                continue

            price_series = df_price["Close"]
            
            # คำนวณเส้นค่าเฉลี่ย
            sma_50 = price_series.rolling(window=50).mean()
            sma_200 = price_series.rolling(window=200).mean()

            curr_price = float(price_series.iloc[-1])
            c_sma50 = float(sma_50.iloc[-1])
            c_sma200 = float(sma_200.iloc[-1])

            # หาจุดสูงสุดย้อนหลัง 52 สัปดาห์ (252 วันทำการ) - ใช้ High จริงของแต่ละวัน
            # ไม่ใช่ราคาปิด (Close ปิดวันนี้ไม่มีทางเกิน max ของตัวเองได้ ทำให้
            # เงื่อนไข "ใกล้ high" (N) ผ่านง่ายเกินจริง)
            high_52w = float(df_price["High"].iloc[-252:].max())

            # --- เงื่อนไขเชิงเทคนิค (N และ M) ---
            # N = New Highs/Setup: อยู่ในระยะไม่เกิน 15% จากจุดสูงสุด 52 สัปดาห์ และยืนเหนือ SMA 50
            cond_near_high = curr_price >= (high_52w * 0.85)
            cond_above_sma50 = curr_price > c_sma50
            cond_sma_alignment = c_sma50 > c_sma200
            
            # M = Market Direction (Uptrend): หุ้นต้องอยู่ในแนวโน้มขาขึ้นเหนือเส้น 200 วัน
            cond_above_sma200 = curr_price > c_sma200

            if cond_near_high and cond_above_sma50 and cond_sma_alignment and cond_above_sma200:
                passed_stocks.append({
                    "Ticker": ticker,
                    "Price": round(curr_price, 2),
                    "52W_High": round(high_52w, 2),
                    "%_From_52W_High": round(((curr_price - high_52w) / high_52w) * 100, 2),
                    "ADTV(MB)": round(adtv_mb, 1),
                })
        except Exception:
            continue

    if not passed_stocks:
        print("📉 [CANSLIM Scan] ไม่พบหุ้นที่ผ่านเกณฑ์ทางเทคนิคเบื้องต้น")
        return

    # แปลงข้อมูลเทคนิคเป็น DataFrame เพื่อนำไปผสานกับปัจจัยพื้นฐาน
    df_technical = pd.DataFrame(passed_stocks)

    # ผสานข้อมูลงบการเงินและ RS Rating
    df_merged = df_technical.merge(df_financial, on="Ticker", how="inner", suffixes=('', '_y'))
    
    # ดึงค่า RS Rating จาก rs_dict
    df_merged["RS_Rating"] = df_merged["Ticker"].map(rs_dict).fillna(0).astype(int)

    # 2. คัดกรองปัจจัยพื้นฐานเข้มข้น (C, A, L)
    # ROE threshold (17% ตามสูตร CANSLIM) - ROE ใน daily_prices.csv เก็บเป็น "เศษส่วน"
    # เสมอ (2_download_history.py หาร TradingView ด้วย 100 และ yfinance
    # returnOnEquity ก็เป็นเศษส่วนอยู่แล้ว) เหมือนที่ scan_lekkung_growth.py
    # hard-code 0.15 ไว้ตรงๆ
    # เดิมบรรทัดนี้เดาหน่วยจาก df_merged["ROE"].max() <= 1.0 ซึ่งพังทันทีที่มีหุ้น
    # ROE เกิน 100% สักตัวผ่านด่านเทคนิคเข้ามา: max > 1.0 -> threshold กลายเป็น 17.0
    # เทียบกับค่าที่เป็นเศษส่วน -> ไม่มีหุ้นตัวไหนผ่าน -> สแกนว่างทั้งกระดาน
    # (2026-08-20/21: SRICHA ROE 0.626 -> 1.1253 หลังงบ Q2 ทำให้ CAN SLIM เหลือ 0 ตัว
    #  ทั้งที่ COM7/FORTH/BCP/MBK/AMATA ยังเข้าเกณฑ์ครบ)
    ROE_THRESHOLD = 0.17

    # คัดกรอง
    # C: Net Profit Growth QoQY >= 20%
    cond_c = df_merged["NetProfit_Growth_QoQY"] >= 20.0
    
    # A: Profit Growth YoY >= 20% และ ROE >= 17%
    cond_a_growth = df_merged["Profit_Growth_YoY"] >= 20.0
    cond_a_roe = df_merged["ROE"] >= ROE_THRESHOLD
    
    # L: RS Rating >= 80 (ต้องเป็นหุ้นนำในตลาด)
    cond_l = df_merged["RS_Rating"] >= 80

    final_canslim_df = df_merged[cond_c & cond_a_growth & cond_a_roe & cond_l].copy()

    print("\n" + "=" * 95)
    print("🏆 WILLIAM O'NEIL'S CANSLIM BREAKOUT WATCHLIST 🏆")
    print("=" * 95)

    if not final_canslim_df.empty:
        # เลือกเฉพาะคอลัมน์ที่น่าสนใจเพื่อแสดงผล
        cols_to_keep = [
            "Ticker", "Price", "%_From_52W_High", "RS_Rating", 
            "NetProfit_Growth_QoQY", "Profit_Growth_YoY", "ROE", "ADTV(MB)"
        ]
        
        # จัดฟอร์แมต ROE ให้แสดงเป็น % สวยงาม (เศษส่วน -> % เสมอ ด้วยเหตุผลเดียว
        # กับ ROE_THRESHOLD ข้างบน: เดาหน่วยจาก max() ทำให้หุ้น ROE เกิน 100%
        # ตัวเดียวพาทั้งตารางไปแสดงผิดหน่วย เช่น 0.47 -> "0.47%" แทน "47%")
        final_canslim_df["ROE_Display"] = (final_canslim_df["ROE"] * 100).round(2)

        # จัดลำดับคอลัมน์และตั้งชื่อใหม่สำหรับการแสดงผล
        display_df = final_canslim_df[cols_to_keep + ["ROE_Display"]].copy()
        display_df = display_df.drop(columns=["ROE"]).rename(columns={"ROE_Display": "ROE(%)"})
        
        # เรียงลำดับตาม RS Rating และมูลค่าการซื้อขาย
        display_df = display_df.sort_values(by=["RS_Rating", "ADTV(MB)"], ascending=[False, False])
        display_df.reset_index(drop=True, inplace=True)
        display_df.index = display_df.index + 1

        # บันทึกผลลัพธ์ลง CSV
        final_canslim_df.to_csv(output_path, index=False, encoding="utf-8-sig")

        print(display_df.to_string())
        print("-" * 95)
        print(f"✨ คัดหุ้นผ่านตามกฎ CANSLIM ระดับผู้นำ: {len(display_df)} ตัว (บันทึกไปที่ {output_path.name})")
    else:
        # scan รันจบครบทุกขั้นแล้วแต่ไม่มีหุ้นผ่านเกณฑ์ = "ว่างจริง" ต้องเขียน CSV
        # ที่มีแต่ header (0 แถว) ไม่ใช่ไม่เขียนไฟล์ - เดิมเคสนี้กับเคส scan ล่ม
        # (return ก่อนหน้า) ออกทางเดียวกันคือไม่มีไฟล์ ทำให้ convert_to_json แยกไม่ออก
        # แล้วเขียน oneil.json = [] ทับของเดิมเงียบๆ (2026-08-20 หายจาก 5 ตัวเหลือ 0)
        final_canslim_df["ROE_Display"] = final_canslim_df["ROE"]
        final_canslim_df.to_csv(output_path, index=False, encoding="utf-8-sig")
        print("📉 ไม่พบหุ้นผู้นำตลาดที่เข้าเกณฑ์สแกน CANSLIM ครบถ้วนในรอบนี้")
        print(f"   (เขียน {output_path.name} 0 แถว = ว่างจริง ไม่ใช่ scan ล่ม)")


if __name__ == "__main__":
    scan_canslim()
