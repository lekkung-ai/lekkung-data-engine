import sys
from pathlib import Path

# Force UTF-8 encoding for standard output/error on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import time
import yfinance as yf
import pandas as pd

# 🔌 ส่วนที่ 1: เชื่อมต่อกับระบบหลัก
sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import DAILY_FILE, HISTORY_DIR, RESULTS_DIR
from utils import load_full_df


def scan_lekkung_growth():
    """
    สแกนหาหุ้น Growth Stock
    🔍 [โหมดสายลับ V.3]: เอ็กซเรย์ด่านงบการเงินทีละข้อ ดูว่าหุ้นหายไปไหน!
    """
    output_path = RESULTS_DIR / "lekkung_growth_scan.csv"

    if not HISTORY_DIR.exists() or not DAILY_FILE.exists():
        print(f"❌ [Lekkung Scan] ไม่พบไฟล์ข้อมูลตั้งต้น")
        return

    print("🕵️‍♂️ [Lekkung Scan] เริ่มสแกนหาหุ้น Growth Stock...")

    try:
        df_financial_data = pd.read_csv(DAILY_FILE)
        df_financial_data.columns = df_financial_data.columns.str.strip()
        if "Ticker" in df_financial_data.columns:
            df_financial_data["Ticker"] = df_financial_data["Ticker"].str.strip()

        # คำนวณ ADTV_MB
        if (
            "Close" in df_financial_data.columns
            and "Volume" in df_financial_data.columns
        ):
            df_financial_data["ADTV_MB"] = (
                df_financial_data["Close"] * df_financial_data["Volume"]
            ) / 1_000_000
        elif "ADTV_MB" not in df_financial_data.columns:
            df_financial_data["ADTV_MB"] = 0

    except Exception as e:
        print(f"❌ [Lekkung Scan] ไม่สามารถอ่านไฟล์: {e}")
        return

    selected_tickers = []
    market_tech_metrics = {}

    for file_path in HISTORY_DIR.glob("*.csv"):
        ticker = file_path.stem.strip()
        try:
            df_price = load_full_df(file_path)
            if df_price is None or len(df_price) < 200:
                continue

            df_price.columns = df_price.columns.str.strip()
            df_price["EMA_10"] = df_price["Close"].ewm(span=10, adjust=False).mean()
            df_price["EMA_50"] = df_price["Close"].ewm(span=50, adjust=False).mean()
            df_price["EMA_200"] = df_price["Close"].ewm(span=200, adjust=False).mean()

            if "Value" in df_price.columns:
                df_price["AvgVal_5"] = df_price["Value"].rolling(window=5).mean()
            else:
                df_price["Value"] = df_price["Close"] * df_price["Volume"]
                df_price["AvgVal_5"] = df_price["Value"].rolling(window=5).mean()

            latest = df_price.iloc[-1]

            cond_avg_val = latest["AvgVal_5"] > 5_000_000
            cond_ema_10 = latest["Close"] > latest["EMA_10"]
            cond_ema_trend = latest["EMA_50"] > latest["EMA_200"]

            if cond_avg_val and cond_ema_10 and cond_ema_trend:
                selected_tickers.append(ticker)
                market_tech_metrics[ticker] = latest["Close"]

        except Exception:
            continue

    if df_financial_data.empty or not selected_tickers:
        print("\n📉 [Lekkung Scan] ไม่พบหุ้นที่ผ่านเกณฑ์ทางเทคนิค")
        return

    # 🧠 ส่วนที่ 5: ตรรกะคัดกรองพื้นฐาน
    roe_threshold = 0.15

    print("\n🔍 [สายลับรายงาน V.3] เจาะลึกด่านงบการเงิน (หาว่าหุ้นตายด่านไหน):")

    cond_tech = df_financial_data["Ticker"].isin(selected_tickers)
    print(f"   ► 1. ผ่านด่านกราฟเทคนิค: {cond_tech.sum()} ตัว")

    cond_roe = (df_financial_data["ROE"] > roe_threshold)
    print(f"   ► 2. ผ่าน ROE > 15%: {cond_roe.sum()} ตัว")

    cond_pe = (df_financial_data["PE_Ratio"] > 0)
    print(f"   ► 3. ผ่าน PE > 0: {cond_pe.sum()} ตัว")

    condition = cond_tech & cond_roe & cond_pe
    print(f"   ► 💥 สรุปผ่าน 3 ด่านแรก: {condition.sum()} ตัว")

    # ⚡ ซ่อมแซมค่า NaN เฉพาะหุ้นที่ผ่านด่านแรก
    needs_patch = df_financial_data[condition][
        df_financial_data[condition]["Revenue_Growth_YoY"].isna() | 
        df_financial_data[condition]["NetProfit_Growth_QoQY"].isna()
    ]
    if not needs_patch.empty:
        print(f"\n   ⚡ [Dynamic Fetch] พบหุ้น {len(needs_patch)} ตัวที่งบเป็น NaN, กำลังดึงข้อมูลทดแทน (เพื่อหลบ Rate Limit)...")
        for idx, row in needs_patch.iterrows():
            ticker = row["Ticker"]
            yf_t = f"{ticker}.BK"
            try:
                stock = yf.Ticker(yf_t)
                info = stock.info
                if pd.isna(row["Revenue_Growth_YoY"]):
                    f = stock.financials
                    if not f.empty and "Total Revenue" in f.index and len(f.columns) >= 2:
                        rev = f.loc["Total Revenue"].sort_index(ascending=False)
                        rev_g = (rev.iloc[0] - rev.iloc[1]) / abs(rev.iloc[1]) * 100
                        df_financial_data.at[idx, "Revenue_Growth_YoY"] = rev_g
                
                if pd.isna(row["NetProfit_Growth_QoQY"]):
                    qf = stock.quarterly_financials
                    if not qf.empty and "Net Income" in qf.index and len(qf.columns) >= 5:
                        net_income = qf.loc["Net Income"].sort_index()
                        qoqy_series = ((net_income - net_income.shift(4)) / net_income.shift(4).abs() * 100)
                        if not qoqy_series.empty:
                            df_financial_data.at[idx, "NetProfit_Growth_QoQY"] = qoqy_series.iloc[-1]
            except Exception:
                pass
            time.sleep(0.5)

    if "Revenue_Growth_YoY" in df_financial_data.columns:
        col_rev = df_financial_data["Revenue_Growth_YoY"]
        cond_rev = (col_rev > 0)
        condition &= cond_rev
        print(
            f"   ► 4. ผ่าน Revenue Growth (>0): {cond_rev.sum()} ตัว (เหลือรอด {condition.sum()} ตัว)"
        )

    if "NetProfit_Growth_QoQY" in df_financial_data.columns:
        col_net = df_financial_data["NetProfit_Growth_QoQY"]
        cond_net = (col_net > 0)
        condition &= cond_net
        print(
            f"   ► 5. ผ่าน Net Profit Growth (>0): {cond_net.sum()} ตัว (เหลือรอด {condition.sum()} ตัว)"
        )

    mc_cols = [
        c
        for c in df_financial_data.columns
        if "marketcap" in c.lower() or "market_cap" in c.lower()
    ]
    if mc_cols:
        col_mc = df_financial_data[mc_cols[0]]
        cond_mc = (col_mc > 3_000_000_000)
        condition &= cond_mc
        print(
            f"   ► 6. ผ่าน Market Cap: {cond_mc.sum()} ตัว (เหลือรอด {condition.sum()} ตัว)"
        )

    final_scan = df_financial_data[condition].copy()

    if not final_scan.empty and market_tech_metrics:
        final_scan["Close"] = final_scan["Ticker"].map(market_tech_metrics)

    if not final_scan.empty:
        final_scan = final_scan.sort_values(by="ADTV_MB", ascending=False)
        final_scan.to_csv(output_path, index=False, encoding="utf-8-sig")

        print(
            f"\n✅ [Lekkung Scan] พบหุ้น Growth Stock {len(final_scan)} ตัว! บันทึกผลที่ {output_path.name}"
        )
        print("-" * 90)
        display_cols = ["Ticker", "Close", "PE_Ratio", "ROE", "ADTV_MB"]
        if "Revenue_Growth_YoY" in final_scan.columns:
            display_cols.append("Revenue_Growth_YoY")
        if "NetProfit_Growth_QoQY" in final_scan.columns:
            display_cols.append("NetProfit_Growth_QoQY")
        available_cols = [c for c in display_cols if c in final_scan.columns]
        print(final_scan[available_cols].round(2).to_string(index=False))
        print("-" * 90)
    else:
        print("\n📉 [Lekkung Scan] ไม่พบหุ้นที่เข้าเงื่อนไขในรอบนี้")


if __name__ == "__main__":
    scan_lekkung_growth()
