import sys
import warnings
from pathlib import Path

import pandas as pd
import numpy as np

warnings.filterwarnings("ignore")

# 🔌 เชื่อมต่อ config.py
sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import ADTV_MIN_MB, HISTORY_DIR, RESULTS_DIR, RS_FILE, DAILY_FILE
from tools.scanner.utils import load_full_df, calculate_adtv

def run_stage_analysis():
    output_path = RESULTS_DIR / "weinstein_stages_result.csv"

    if not RS_FILE.exists() or not HISTORY_DIR.exists():
        print("❌ ไม่พบไฟล์ตั้งต้น (RS Ranking หรือ โฟลเดอร์ history)")
        return

    # อ่าน RS_Rating
    try:
        df_rs = pd.read_csv(RS_FILE)
        rs_dict = dict(zip(df_rs["Ticker"], df_rs["RS_Rating"]))
        tickers = df_rs["Ticker"].astype(str).str.upper().tolist()
    except Exception as e:
        print(f"❌ เกิดข้อผิดพลาดในการอ่านไฟล์ RS Ranking: {e}")
        return

    # อ่าน Fundamentals (PE, ROE, 52W H/L)
    fund_dict = {}
    try:
        df_daily = pd.read_csv(DAILY_FILE)
        for _, row in df_daily.iterrows():
            fund_dict[row["Ticker"]] = {
                "PE_Ratio": row.get("PE_Ratio", 0.0),
                "ROE": row.get("ROE", 0.0),
                "52W_High": row.get("52W_High", 0.0),
                "52W_Low": row.get("52W_Low", 0.0),
            }
    except Exception as e:
        print(f"⚠️ ไม่สามารถอ่านไฟล์ daily_prices.csv เพื่อดึงข้อมูลพื้นฐานได้: {e}")

    results = []
    print("🧠 [Quant Engine] เริ่มประมวลผล Stan Weinstein's Stage Analysis (Weekly & stageanalysis.net rules)...")

    for ticker in tickers:
        file_path = HISTORY_DIR / f"{ticker}.csv"
        if not file_path.exists():
            continue

        try:
            # โหลดข้อมูลรายวัน
            df_daily = load_full_df(file_path)
            if df_daily is None or len(df_daily) == 0:
                continue
                
            if "Date" not in df_daily.columns and "Unnamed: 0" in df_daily.columns:
                df_daily.rename(columns={"Unnamed: 0": "Date"}, inplace=True)
            elif "Date" not in df_daily.columns and df_daily.index.name == "Date":
                df_daily = df_daily.reset_index()

            # 🛡️ เช็คสภาพคล่อง (ADTV) จากข้อมูล Daily
            adtv_mb = calculate_adtv(df_daily["Close"], df_daily["Volume"], window=50)

            # แปลงเป็นกราฟรายสัปดาห์ (Weekly Chart)
            df_daily["Date"] = pd.to_datetime(df_daily["Date"])
            df_weekly = (
                df_daily.resample("W-FRI", on="Date")
                .agg({
                    "Open": "first",
                    "High": "max",
                    "Low": "min",
                    "Close": "last",
                    "Volume": "sum",
                })
                .dropna()
            )

            if len(df_weekly) < 35: # ต้องการอย่างน้อย 30 สัปดาห์สำหรับ MA30
                stage = "Unknown"
                curr_price = df_weekly.iloc[-1]["Close"] if not df_weekly.empty else df_daily.iloc[-1]["Close"]
                ma30 = 0
                slope_4w_pct = 0
                rs_score = rs_dict.get(ticker, 0)
                curr = {"MA10": 0, "Vol10": 0}
            else:
                # คำนวณ 30-Week MA และ 10-Week MA
                df_weekly["MA30"] = df_weekly["Close"].rolling(window=30).mean()
                df_weekly["MA10"] = df_weekly["Close"].rolling(window=10).mean()
                df_weekly["Vol10"] = df_weekly["Volume"].rolling(window=10).mean()
    
                # หาแนวต้าน (High) ย้อนหลัง 20 สัปดาห์ (ไม่รวมแท่งปัจจุบัน) สำหรับหา Breakout
                df_weekly["Highest_Resist"] = df_weekly["High"].shift(1).rolling(window=20).max()
    
                # ข้อมูลสัปดาห์ปัจจุบัน และอดีต
                curr = df_weekly.iloc[-1]
                prev = df_weekly.iloc[-2]
                past_4w = df_weekly.iloc[-5] if len(df_weekly) >= 5 else prev
                past_12w = df_weekly.iloc[-13] if len(df_weekly) >= 13 else df_weekly.iloc[0]
    
                rs_score = rs_dict.get(ticker, 0)
                curr_price = curr["Close"]
                ma30 = curr["MA30"]

                if pd.isna(curr["MA30"]) or pd.isna(past_4w["MA30"]):
                    stage = "Unknown"
                    slope_4w_pct = 0
                    ma30 = 0
                else:
                    # ประเมินความชันของ MA30 (ช่วง 4 สัปดาห์)
                    slope_4w_pct = ((curr["MA30"] - past_4w["MA30"]) / past_4w["MA30"]) * 100
                    
                    ma30_rising = slope_4w_pct > 1.0
                    ma30_declining = slope_4w_pct < -1.0
                    ma30_flat = -1.0 <= slope_4w_pct <= 1.0
                    
                    ma10_rising = curr["MA10"] > prev["MA10"]
                    
                    # การแบ่ง Stage
                    stage = "Unknown"
                    
                    # ตรวจสอบ Stage 2A Breakout (Stageanalysis.net)
                    is_breakout = (
                        curr_price > curr["Highest_Resist"] and 
                        curr_price > ma30 and 
                        ma10_rising and
                        (ma30_rising or ma30_flat) and
                        curr["Volume"] > (curr["Vol10"] * 2) and
                        rs_score > 70
                    )
        
                    if is_breakout:
                        stage = "Stage 2A (Breakout)"
                    elif curr_price > ma30 and ma30_rising:
                        stage = "Stage 2 (Advancing)"
                    elif curr_price < ma30 and ma30_declining:
                        stage = "Stage 4 (Declining)"
                    elif ma30_flat:
                        # ถ้าแบนราบ ให้ดูประวัติเดิม (12 สัปดาห์ก่อน) ว่ามาจากขาขึ้นหรือขาลง
                        if past_12w["Close"] > past_12w["MA30"]:
                            stage = "Stage 3 (Topping)"
                        else:
                            stage = "Stage 1 (Basing)"
                    else:
                        # กรณีอื่นๆ เช่น MA ชันขึ้นแต่ราคาหลุด หรือ MA ชันลงแต่ราคาทะลุ
                        if curr_price > ma30:
                            stage = "Stage 2- transition" if ma30_declining else "Stage 2 (Advancing)"
                        else:
                            stage = "Stage 4- transition" if ma30_rising else "Stage 4 (Declining)"

            fund = fund_dict.get(ticker, {})
            results.append(
                {
                    "Ticker": ticker,
                    "Stage": stage,
                    "Price": round(curr_price, 2),
                    "MA30": round(ma30, 2),
                    "MA10": round(curr["MA10"], 2),
                    "Vol10": round(curr["Vol10"], 0),
                    "Slope_4W_%": round(slope_4w_pct, 2),
                    "RS_Rating": int(rs_score),
                    "ADTV(MB)": round(adtv_mb, 1),
                    "PE_Ratio": fund.get("PE_Ratio", 0.0),
                    "ROE": fund.get("ROE", 0.0),
                    "52W_High": fund.get("52W_High", 0.0),
                    "52W_Low": fund.get("52W_Low", 0.0),
                }
            )
        except Exception as e:
            print(f"Error on {ticker}: {e}")
            pass

    if results:
        df_res = pd.DataFrame(results)
        
        # จัดเรียงลำดับ Stage
        def get_rank(s):
            if "Stage 2A" in s: return 1
            if "Stage 2" in s: return 2
            if "Stage 1" in s: return 3
            if "Stage 3" in s: return 4
            return 5
            
        df_res["Stage_Rank"] = df_res["Stage"].apply(get_rank)
        df_res = df_res.sort_values(
            by=["Stage_Rank", "RS_Rating", "Slope_4W_%"], ascending=[True, False, False]
        ).drop(columns=["Stage_Rank"])

        df_res.to_csv(output_path, index=False, encoding="utf-8-sig")
        print(f"✅ วิเคราะห์เสร็จสิ้น! บันทึกผลลัพธ์ลง weinstein_stages_result.csv")

        print("\n🏆 ตัวอย่างหุ้นที่แข็งแกร่งที่สุดใน Stage 2 (เรียงตาม RS):")
        top_stage2 = df_res[df_res["Stage"].str.contains("Stage 2")].head(10)
        if not top_stage2.empty:
            print(top_stage2.to_string(index=False))
        else:
            print("ไม่พบหุ้น Stage 2 ในขณะนี้")

if __name__ == "__main__":
    run_stage_analysis()
