import sys
from pathlib import Path

# Force UTF-8 encoding for standard output/error on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import json
import time
from datetime import date
import yfinance as yf
import pandas as pd

# 🔌 ส่วนที่ 1: เชื่อมต่อกับระบบหลัก
sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import DAILY_FILE, HISTORY_DIR, RESULTS_DIR
from utils import iter_stock_files, load_fund_tickers, load_full_df
from tools.core.growth_period import (annual_is_fresh, fy_end_month, growth_vs_year_ago, parse_thai_date,
                                      quarter_is_fresh, quarter_label, short_quarter, to_date)

INCOMPLETE_FILE = RESULTS_DIR / "lekkung_incomplete.csv"

# earnings_feed.json (F45 NetProfit YoY, fetched by stockdesk/scripts/fetch_earnings.py)
# is checked in a couple of candidate locations since this script runs in two
# different contexts:
#   - local dev: stockdesk is a sibling repo checkout on the same machine
#   - CI (daily-scan.yml): an early sparse-checkout puts it at
#     data_engine/stockdesk_earnings_check/ specifically so this script can
#     see it - stockdesk itself isn't checked out yet at this point in the
#     pipeline (the main stockdesk checkout used to *write* scan output
#     happens later, after run_all.py has already finished)
_DATA_ENGINE_ROOT = Path(__file__).resolve().parents[2]
EARNINGS_FEED_CANDIDATES = [
    _DATA_ENGINE_ROOT / "stockdesk_earnings_check" / "public" / "data" / "earnings" / "earnings_feed.json",
    _DATA_ENGINE_ROOT.parent.parent / "Claude" / "dashboard" / "stockdesk" / "public" / "data" / "earnings" / "earnings_feed.json",
]


# คอลัมน์งวด/แหล่งของค่า growth จาก 2_download_history.apply_growth_freshness
GROWTH_META_COLS = ["Growth_Quarter", "Growth_Quarter_End", "Growth_Source", "Rev_Growth_Period_End",
                    "Profit_Growth_Period_End", "Growth_Stale", "Growth_Note"]


def load_f45_netprofit_qoqy() -> dict:
    """Ticker -> (most-recent F45 netProfitYoY, period end date or None) (already
    same-quarter-vs-last-year, matching NetProfit_Growth_QoQY's own definition).
    Returns {} if earnings_feed.json isn't reachable from either candidate
    location - callers must fall back to Yahoo/cache in that case, not error."""
    for path in EARNINGS_FEED_CANDIDATES:
        if not path.exists():
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # announcements is sorted newest-first by fetch_earnings.py already
            result = {}
            for a in data.get("announcements", []):
                t = a.get("ticker")
                yoy = a.get("netProfitYoY")
                if t and t not in result and yoy is not None:
                    result[t] = (yoy, parse_thai_date(a.get("periodEnd")))
            print(f"   📄 [F45] โหลด earnings_feed.json จาก {path} -> {len(result)} ticker มี NetProfit YoY")
            return result
        except Exception as e:
            print(f"   ⚠️ [F45] อ่าน {path} ไม่สำเร็จ: {e}")
    print("   📄 [F45] ไม่พบ earnings_feed.json ในตำแหน่งที่รู้จัก - ใช้ Yahoo/cache ล้วนสำหรับ NetProfit_Growth_QoQY")
    return {}


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

    # ⏳ งวดของค่า growth (2_download_history.apply_growth_freshness เขียนมา · daily_prices.csv รุ่นเก่าไม่มี)
    today = date.today()
    for col in GROWTH_META_COLS:
        if col not in df_financial_data.columns:
            df_financial_data[col] = None
        df_financial_data[col] = df_financial_data[col].astype(object).where(df_financial_data[col].notna(), None)

    def fy_of(idx) -> int:
        return fy_end_month(df_financial_data.at[idx, "Rev_Growth_Period_End"])

    def drop_stale_note(idx, col) -> None:
        notes = [n for n in str(df_financial_data.at[idx, "Growth_Stale"] or "").split("; ") if n and not n.startswith(col)]
        df_financial_data.at[idx, "Growth_Stale"] = "; ".join(notes) or None

    def set_quarter(idx, value, period_end, source) -> None:
        """ใส่ค่า QoQY ที่งวดสดแล้ว + งวด/แหล่ง · ลบป้ายงบค้างของ QoQY (ถ้ามี)"""
        df_financial_data.at[idx, "NetProfit_Growth_QoQY"] = value
        df_financial_data.at[idx, "Growth_Quarter_End"] = period_end.isoformat()
        df_financial_data.at[idx, "Growth_Quarter"] = quarter_label(period_end)
        df_financial_data.at[idx, "growth_source"] = source
        df_financial_data.at[idx, "Growth_Note"] = None
        drop_stale_note(idx, "NetProfit_Growth_QoQY")

    # 🔄 NetProfit_Growth_QoQY: ใช้ F45 ของเราเองก่อน (ตรงไตรมาสจริง ไม่มีปัญหา Yahoo
    # คืนค่าว่างแบบสุ่ม) แล้วค่อย fallback Yahoo/cache/TradingView เฉพาะตัวที่ยังไม่มี F45 ในระบบ
    # F45 ก็ต้องเป็นงวดล่าสุดที่ควรมี (บริษัทที่ส่งงบช้าอาจเพิ่งประกาศงวดก่อน) — งวดค้าง → ไม่ใช้
    df_financial_data["growth_source"] = df_financial_data["Growth_Source"].fillna("yahoo")
    f45_netprofit = load_f45_netprofit_qoqy()
    if f45_netprofit:
        used, skipped = 0, []
        for idx in df_financial_data.index[df_financial_data["Ticker"].isin(f45_netprofit.keys())]:
            yoy, period_end = f45_netprofit[df_financial_data.at[idx, "Ticker"]]
            # งวดใหม่ที่สุดชนะ: F45 ต้องไม่เก่ากว่างวดที่ daily_prices ใช้อยู่ (งวดเท่ากัน → F45 ตามลำดับเดิม)
            current = to_date(df_financial_data.at[idx, "Growth_Quarter_End"])
            if current is not None and period_end is not None and period_end < current:
                skipped.append(f"{df_financial_data.at[idx, 'Ticker']}({short_quarter(period_end)}<{short_quarter(current)})")
            elif quarter_is_fresh(period_end, today, fy_of(idx)):
                set_quarter(idx, yoy, period_end, "f45")
                used += 1
            else:
                skipped.append(f"{df_financial_data.at[idx, 'Ticker']}({short_quarter(period_end)})")
        print(f"   📄 [F45] แทนที่ NetProfit_Growth_QoQY ด้วยข้อมูล F45 จริงสำหรับ {used} ตัว"
              + (f" · ไม่ใช้ F45 (งวดค้าง / เก่ากว่างวดที่ใช้อยู่) {len(skipped)} ตัว: {', '.join(skipped)}" if skipped else ""))

    selected_tickers = []
    market_tech_metrics = {}

    fund_tickers = load_fund_tickers()
    for file_path in iter_stock_files(HISTORY_DIR):
        ticker = file_path.stem.strip()
        if ticker.upper() in fund_tickers:  # กองทุน/REIT ไม่เข้า scanner หุ้น
            continue
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
                market_tech_metrics[ticker] = {
                    "Close": float(latest["Close"]),
                    "EMA_10": round(float(latest["EMA_10"]), 2),
                }

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
                    if not f.empty and "Total Revenue" in f.index:
                        # ปีล่าสุดเทียบปีที่ห่าง 12 เดือนพอดี (ตามวันที่ ไม่ใช่ตำแหน่ง) · งบปีค้าง → ไม่ใช้
                        rev_g, rev_end, _ = growth_vs_year_ago(f.loc["Total Revenue"])
                        if rev_g is not None and annual_is_fresh(rev_end, today):
                            df_financial_data.at[idx, "Revenue_Growth_YoY"] = rev_g * 100
                            df_financial_data.at[idx, "Rev_Growth_Period_End"] = rev_end.isoformat()
                            drop_stale_note(idx, "Revenue_Growth_YoY")
                
                if pd.isna(row["NetProfit_Growth_QoQY"]):
                    qf = stock.quarterly_financials
                    if not qf.empty and "Net Income" in qf.index:
                        # ไตรมาสล่าสุดเทียบไตรมาสที่ห่าง 12 เดือนพอดี (ตามวันที่) · ไตรมาสค้าง → ไม่ใช้
                        q_g, q_end, _ = growth_vs_year_ago(qf.loc["Net Income"])
                        if q_g is not None and quarter_is_fresh(q_end, today, fy_of(idx)):
                            set_quarter(idx, q_g * 100, q_end, "yahoo")
            except Exception:
                pass
            time.sleep(0.5)

    # 📋 Transparency list: ตัวที่ผ่านราคา/สภาพคล่อง (cond_tech) แต่ข้อมูลงบไม่ครบ แม้หลัง
    # fallback ทุกทางแล้ว (F45 / Yahoo dynamic fetch / growth_cache) - กันไม่ให้หุ้นแบบ MGC
    # หายเงียบโดยไม่มีร่องรอย ผู้ใช้จะเห็นจำนวน "รอข้อมูล" แทน
    incomplete_rows = []
    fundamentals_incomplete_mask = (
        df_financial_data["ROE"].isna()
        | df_financial_data["PE_Ratio"].isna()
        | df_financial_data["Revenue_Growth_YoY"].isna()
        | df_financial_data["NetProfit_Growth_QoQY"].isna()
    )
    for _, row in df_financial_data[cond_tech & fundamentals_incomplete_mask].iterrows():
        # งบค้าง (apply_growth_freshness ตัดเป็น null) แยกเป็นเหตุผลของตัวเอง: "stale: NetProfit_Growth_QoQY Q1/26"
        stale = [n for n in str(row.get("Growth_Stale") or "").split("; ") if n and pd.isna(row.get(n.split(" ")[0]))]
        stale_cols = {n.split(" ")[0] for n in stale}
        missing = [
            name for name, val in [
                ("ROE", row.get("ROE")),
                ("PE_Ratio", row.get("PE_Ratio")),
                ("Revenue_Growth_YoY", row.get("Revenue_Growth_YoY")),
                ("NetProfit_Growth_QoQY", row.get("NetProfit_Growth_QoQY")),
            ]
            if pd.isna(val) and name not in stale_cols
        ]
        parts = ([f"missing: {', '.join(missing)}"] if missing else []) + ([f"stale: {', '.join(stale)}"] if stale else [])
        # เหตุผลที่ QoQY คำนวณไม่ได้ (Growth_Note เช่น "ไตรมาสไม่พอ (4/5)") — ข้อความไทยตรง ๆ ต่อท้าย
        if row.get("Growth_Note") and pd.isna(row.get("NetProfit_Growth_QoQY")):
            parts.append(str(row["Growth_Note"]))
        incomplete_rows.append({"Ticker": row["Ticker"], "Reason": "; ".join(parts)})

    # ตัวที่ผ่านเทคนิคจาก HISTORY_DIR แต่หลุดจาก daily_prices.csv ไปเลย (เช่น ราคา fallback
    # ก็ยังกู้คืนไม่ได้ - ไฟล์เก่าเกินไปหรือไม่มีไฟล์เลย)
    tickers_in_financial_data = set(df_financial_data["Ticker"])
    for ticker in selected_tickers:
        if ticker not in tickers_in_financial_data:
            incomplete_rows.append({"Ticker": ticker, "Reason": "missing: not in daily_prices.csv (price/fundamentals fetch failed entirely)"})

    if incomplete_rows:
        pd.DataFrame(incomplete_rows).to_csv(INCOMPLETE_FILE, index=False, encoding="utf-8-sig")
        print(f"   📋 [Transparency] {len(incomplete_rows)} ตัวผ่านราคา/สภาพคล่องแต่ข้อมูลงบไม่ครบ -> {INCOMPLETE_FILE.name}")
    elif INCOMPLETE_FILE.exists():
        INCOMPLETE_FILE.unlink()

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

    # growth_source (รวม f45) แทน Growth_Source ของ daily_prices.csv ในผลสแกน
    final_scan = df_financial_data[condition].drop(columns=["Growth_Source"]).copy()

    if not final_scan.empty and market_tech_metrics:
        final_scan["Close"] = final_scan["Ticker"].map(lambda t: market_tech_metrics.get(t, {}).get("Close"))
        final_scan["EMA_10"] = final_scan["Ticker"].map(lambda t: market_tech_metrics.get(t, {}).get("EMA_10"))

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
