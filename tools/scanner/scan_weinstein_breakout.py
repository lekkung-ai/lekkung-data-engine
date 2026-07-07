import sys
from pathlib import Path

import pandas as pd

# 🔌 เชื่อมต่อ config.py
sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import ADTV_MIN_MB, HISTORY_DIR, RESULTS_DIR, WEINSTEIN_SLOPE
from utils import calculate_adtv, load_full_df


def weighted_moving_average(series, n):
    """คำนวณ Weighted Moving Average (WMA)"""
    weights = pd.Series(range(1, n + 1))
    return series.rolling(n).apply(
        lambda prices: (prices * weights).sum() / weights.sum(), raw=True
    )


def scan_weinstein_breakout():
    """
    สแกนหาหุ้นที่เพิ่ง Breakout เข้าสู่ Stage 2 ตามหลักของ Stan Weinstein
    โดยใช้ข้อมูลรายสัปดาห์ (Weekly)
    """
    output_path = RESULTS_DIR / "weinstein_breakouts.csv"

    if not HISTORY_DIR.exists():
        print(f"❌ ไม่พบโฟลเดอร์ history ที่: {HISTORY_DIR}")
        return

    print("🕵️‍♂️ [Stan Weinstein] กำลังสแกนหาหุ้น Stage 2 Breakout (Weekly)...")
    passed_stocks = []

    # --- ⚙️ ค่าที่ปรับจูนได้ (จาก Pine Script) ---
    ma_length = 30
    vol_mult = 2.0
    lookback_period = 20

    for file_path in HISTORY_DIR.glob("*.csv"):
        ticker = file_path.stem
        try:
            # โหลดข้อมูลรายวัน (Daily)
            df_daily = load_full_df(file_path)
            if df_daily is None or len(df_daily) < (
                ma_length * 5 + lookback_period
            ):  # เช็คข้อมูลรายวันให้พอ
                continue

            # --- [ด่านที่ 1] กรองสภาพคล่อง (Liquidity Filter) จากข้อมูล Daily ---
            adtv_mb = calculate_adtv(df_daily["Close"], df_daily["Volume"], window=50)
            if adtv_mb < ADTV_MIN_MB:
                continue

            # --- [ด่านที่ 2] แปลงข้อมูลเป็นรายสัปดาห์ (Weekly) ---
            df_daily["Date"] = pd.to_datetime(df_daily["Date"])
            df_weekly = (
                df_daily.resample("W-FRI", on="Date")
                .agg(
                    {
                        "Open": "first",
                        "High": "max",
                        "Low": "min",
                        "Close": "last",
                        "Volume": "sum",
                    }
                )
                .dropna()
            )

            if len(df_weekly) < ma_length + 2:  # เช็คข้อมูลรายสัปดาห์ให้พอ
                continue

            # --- [ด่านที่ 3] คำนวณ Indicators ตามสูตร ---
            # ใช้ WMA ตามต้นฉบับ
            df_weekly["MA30"] = weighted_moving_average(df_weekly["Close"], ma_length)
            df_weekly["Avg_Vol20"] = df_weekly["Volume"].rolling(window=20).mean()

            # หาแนวต้านสูงสุดย้อนหลัง (ไม่รวมแท่งปัจจุบัน)
            df_weekly["Highest_Resist"] = (
                df_weekly["High"].shift(1).rolling(window=lookback_period).max()
            )

            latest_week = df_weekly.iloc[-1]
            prev_week = df_weekly.iloc[-2]

            # --- [ด่านที่ 4] ตรวจสอบเงื่อนไขการ Breakout ---
            # 1. MA ต้องเป็นขาขึ้น (MA ปัจจุบัน > MA สัปดาห์ก่อน)
            ma_is_rising = latest_week["MA30"] > prev_week["MA30"]

            # 2. ราคาต้องทะลุแนวต้าน และยืนเหนือเส้น MA30
            price_breakout = (
                (prev_week["Close"] <= latest_week["Highest_Resist"])
                and (latest_week["Close"] > latest_week["Highest_Resist"])
                and (latest_week["Close"] > latest_week["MA30"])
            )

            # 3. Volume ต้องยืนยัน (มากกว่าค่าเฉลี่ย * ตัวคูณ)
            volume_confirmed = latest_week["Volume"] > (
                latest_week["Avg_Vol20"] * vol_mult
            )

            # --- สรุปผล ---
            if ma_is_rising and price_breakout and volume_confirmed:
                passed_stocks.append(
                    {
                        "Ticker": ticker,
                        "Breakout_Date": latest_week.name.strftime("%Y-%m-%d"),
                        "Price": round(latest_week["Close"], 2),
                        "Resistance": round(latest_week["Highest_Resist"], 2),
                        "Volume_Ratio": round(
                            latest_week["Volume"] / latest_week["Avg_Vol20"], 1
                        ),
                        "ADTV(MB)": round(adtv_mb, 1),
                    }
                )

        except Exception:
            continue

    if passed_stocks:
        df_final = pd.DataFrame(passed_stocks).sort_values(
            by="ADTV(MB)", ascending=False
        )
        df_final.to_csv(output_path, index=False, encoding="utf-8-sig")
        print(
            f"\n✅ พบหุ้น Stage 2 Breakout ทั้งหมด {len(df_final)} ตัว! บันทึกผลที่ {output_path.name}"
        )
        print(df_final.to_string(index=False))
    else:
        print("\n📉 ไม่พบหุ้นที่เกิดสัญญาณ Stage 2 Breakout ในสัปดาห์ล่าสุด")


if __name__ == "__main__":
    scan_weinstein_breakout()
