import os
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Tuple

# Force UTF-8 encoding for standard output/error on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")


# 🔌 เชื่อมต่อ config.py
sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import (CHUNK_SIZE, DAILY_FILE, FUND_WORKERS, HISTORY_DIR,
                    SYMBOLS_FILE)

# 📦 Growth Cache — เก็บค่า Revenue_Growth_YoY / NetProfit_Growth_QoQY ที่เคยดึงสำเร็จ
# ข้ามรอบรัน เพื่อเติมให้ตัวที่ดึงพลาดในรอบนี้ (Yahoo .financials/.quarterly_financials
# คืนค่าว่างแบบสุ่ม ~40% ต่อ request โดยไม่มี error ให้ retry จับได้ในรอบเดียว)
GROWTH_CACHE_FILE = HISTORY_DIR.parent / "cache" / "growth_cache.csv"
GROWTH_CACHE_MAX_AGE_DAYS = 100  # ~1 ไตรมาส เกินนี้ถือว่าหมดอายุ ไม่เอามาเติม


def fetch_fundamentals(ticker: str) -> Tuple[str, Dict[str, Any]]:
    """ฟังก์ชันย่อยสำหรับดึงงบการเงินแบบรวดเร็ว"""
    try:
        # --- 💡 ส่วนคำนวณ QoQY ---
        qoqy_growth = None
        qoqy_ref_period = None
        yf_t = f"{ticker}.BK"
        stock = yf.Ticker(yf_t)

        # 1. ดึงข้อมูลงบการเงินรายไตรมาส
        qf = stock.quarterly_financials

        # 2. ตรวจสอบว่ามีข้อมูล 'Net Income' และมีข้อมูลพอ (อย่างน้อย 5 ไตรมาส)
        if not qf.empty and "Net Income" in qf.index and len(qf.columns) >= 5:
            net_income = qf.loc["Net Income"].sort_index()

            # 3. คำนวณ QoQY ตามสูตร
            qoqy_series = (
                (net_income - net_income.shift(4)) / net_income.shift(4).abs() * 100
            )

        # 4. ดึงค่าล่าสุดมาใช้งาน
        if not qoqy_series.empty:
            qoqy_growth = qoqy_series.iloc[-1]
            qoqy_ref_period = qoqy_series.index[-1]

        # --- 💡 ส่วนคำนวณ Annual Revenue Growth & Annual Net Income Growth ---
        ann_rev_growth = None
        ann_rev_ref_period = None
        ann_profit_growth = None
        f = stock.financials
        if not f.empty:
            if "Total Revenue" in f.index and len(f.columns) >= 2:
                rev = f.loc["Total Revenue"].sort_index(ascending=False)
                ann_rev_series = (rev.iloc[0] - rev.iloc[1]) / abs(rev.iloc[1])
                ann_rev_growth = ann_rev_series
                ann_rev_ref_period = rev.index[0]

            if "Net Income" in f.index and len(f.columns) >= 2:
                ni = f.loc["Net Income"].sort_index(ascending=False)
                ann_profit_series = (ni.iloc[0] - ni.iloc[1]) / abs(ni.iloc[1])
                ann_profit_growth = ann_profit_series

        # --- ส่วนดึงข้อมูล Snapshot เดิม ---
        info = stock.info
        return ticker, {
            "PE_Ratio": info.get("trailingPE", None),
            "EPS": info.get("trailingEps"),
            "Revenue": info.get("totalRevenue"),
            "ROE": info.get("returnOnEquity"),
            "Revenue_Growth_YoY": ann_rev_growth,  # 👈 ใช้ค่า Annual ที่คำนวณเอง
            "Revenue_Growth_YoY_ref": ann_rev_ref_period,  # 📦 metadata สำหรับ growth cache เท่านั้น
            "Profit_Growth_YoY": ann_profit_growth,  # 👈 ใช้ค่า Annual Net Income ที่คำนวณเอง (CANSLIM)
            "NetProfit_Growth_QoQY": qoqy_growth,  # 👈 ใช้ค่า QoQ ที่คำนวณเอง
            "NetProfit_Growth_QoQY_ref": qoqy_ref_period,  # 📦 metadata สำหรับ growth cache เท่านั้น
            "Market_Cap": info.get("marketCap"),
        }
    except Exception:
        return ticker, {
            "PE_Ratio": None,
            "EPS": None,
            "Revenue": None,
            "ROE": None,
            "Revenue_Growth_YoY": None,
            "Revenue_Growth_YoY_ref": None,
            "Profit_Growth_YoY": None,
            "NetProfit_Growth_QoQY": None,
            "NetProfit_Growth_QoQY_ref": None,
            "Market_Cap": None,
        }


def _load_growth_cache() -> Dict[str, Dict[str, Any]]:
    """โหลด growth cache เดิม (ถ้ามี) เป็น dict คีย์ด้วย Ticker"""
    if not GROWTH_CACHE_FILE.exists():
        return {}
    df = pd.read_csv(GROWTH_CACHE_FILE)
    df["Ticker"] = df["Ticker"].astype(str).str.strip()
    return {row["Ticker"]: row.to_dict() for _, row in df.iterrows()}


def _save_growth_cache(cache: Dict[str, Dict[str, Any]]) -> None:
    GROWTH_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    cols = [
        "Ticker",
        "Revenue_Growth_YoY",
        "Revenue_Growth_YoY_ref_period",
        "Revenue_Growth_YoY_fetched_date",
        "NetProfit_Growth_QoQY",
        "NetProfit_Growth_QoQY_ref_period",
        "NetProfit_Growth_QoQY_fetched_date",
    ]
    df = pd.DataFrame(list(cache.values()))
    if df.empty:
        df = pd.DataFrame(columns=cols)
    else:
        df = df.reindex(columns=cols)
    df.to_csv(GROWTH_CACHE_FILE, index=False, encoding="utf-8-sig")


def _is_fresh(fetched_date_str: Any) -> bool:
    if fetched_date_str is None or (isinstance(fetched_date_str, float) and pd.isna(fetched_date_str)):
        return False
    try:
        fetched = datetime.strptime(str(fetched_date_str)[:10], "%Y-%m-%d").date()
    except ValueError:
        return False
    return (date.today() - fetched).days <= GROWTH_CACHE_MAX_AGE_DAYS


def apply_growth_cache_fallback(fundamental_data: Dict[str, Dict[str, Any]]) -> None:
    """เติม Revenue_Growth_YoY / NetProfit_Growth_QoQY ที่เป็น None ในรอบนี้ ด้วยค่าจาก
    cache ที่เคยดึงสำเร็จ (ถ้ายังไม่หมดอายุ) และอัปเดต cache ด้วยค่าที่ดึงสำเร็จรอบนี้
    ไม่แตะ field อื่น ไม่แตะ threshold/logic การกรองใดๆ — แก้แค่ที่มาของค่า growth
    """
    cache = _load_growth_cache()
    today_str = date.today().isoformat()
    recovered_from_cache = 0

    for ticker, data in fundamental_data.items():
        entry = cache.get(ticker, {"Ticker": ticker})

        # Revenue_Growth_YoY
        if data.get("Revenue_Growth_YoY") is not None:
            entry["Revenue_Growth_YoY"] = data["Revenue_Growth_YoY"]
            ref = data.get("Revenue_Growth_YoY_ref")
            entry["Revenue_Growth_YoY_ref_period"] = str(ref)[:10] if ref is not None else None
            entry["Revenue_Growth_YoY_fetched_date"] = today_str
        elif _is_fresh(entry.get("Revenue_Growth_YoY_fetched_date")):
            data["Revenue_Growth_YoY"] = entry.get("Revenue_Growth_YoY")
            recovered_from_cache += 1

        # NetProfit_Growth_QoQY
        if data.get("NetProfit_Growth_QoQY") is not None:
            entry["NetProfit_Growth_QoQY"] = data["NetProfit_Growth_QoQY"]
            ref = data.get("NetProfit_Growth_QoQY_ref")
            entry["NetProfit_Growth_QoQY_ref_period"] = str(ref)[:10] if ref is not None else None
            entry["NetProfit_Growth_QoQY_fetched_date"] = today_str
        elif _is_fresh(entry.get("NetProfit_Growth_QoQY_fetched_date")):
            data["NetProfit_Growth_QoQY"] = entry.get("NetProfit_Growth_QoQY")
            recovered_from_cache += 1

        cache[ticker] = entry

        # metadata keys ใช้แค่ตอน merge เข้า cache ไม่ต้องส่งต่อไปยัง daily_snapshot
        data.pop("Revenue_Growth_YoY_ref", None)
        data.pop("NetProfit_Growth_QoQY_ref", None)

    _save_growth_cache(cache)
    print(
        f"   📦 [Growth Cache] เติมค่าจาก cache ที่เคยดึงสำเร็จ {recovered_from_cache} field, "
        f"cache สะสมทั้งหมด {len(cache)} ticker"
    )


def download_history_and_build_snapshot() -> None:
    if not SYMBOLS_FILE.exists():
        print(f"❌ ไม่พบไฟล์ '{SYMBOLS_FILE.name}' กรุณารัน Step 1 ก่อนครับ")
        return

    df_symbols = pd.read_csv(SYMBOLS_FILE)
    tickers = df_symbols["Ticker"].tolist()

    print(
        f"📥 [Phase 1/2] กำลังดาวน์โหลดข้อมูลย้อนหลัง 2 ปี สำหรับหุ้น {len(tickers)} ตัว..."
    )

    daily_snapshot = []
    success_count = 0
    valid_tickers = []

    for i in range(0, len(tickers), CHUNK_SIZE):
        chunk_tickers = tickers[i : i + CHUNK_SIZE]
        yf_tickers = [f"{t}.BK" for t in chunk_tickers]

        print(
            f"   ⏳ กำลังโหลดล็อตที่ {(i//CHUNK_SIZE)+1} ({len(chunk_tickers)} ตัว)..."
        )
        data = yf.download(
            yf_tickers,
            period="3y",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
        )

        for ticker in chunk_tickers:
            try:
                yf_t = f"{ticker}.BK"
                if len(yf_tickers) > 1:
                    if yf_t not in data.columns.levels[0]:
                        continue
                    df = data[yf_t].dropna(subset=["Close"])
                else:
                    df = data.dropna(subset=["Close"])

                if len(df) >= 200:
                    df["SMA_50"] = df["Close"].rolling(window=50).mean().round(2)
                    df["SMA_150"] = df["Close"].rolling(window=150).mean().round(2)
                    df["SMA_200"] = df["Close"].rolling(window=200).mean().round(2)
                    df["52W_High"] = df["Close"].rolling(window=252).max().round(2)
                    df["52W_Low"] = df["Close"].rolling(window=252).min().round(2)

                    # 🎯 เซฟไฟล์ประวัติลงโฟลเดอร์ HISTORY_DIR
                    save_file = HISTORY_DIR / f"{ticker}.csv"
                    df.to_csv(save_file)

                    last_row = df.iloc[-1]
                    prev_row = df.iloc[-2]
                    pct_change = (
                        (last_row["Close"] - prev_row["Close"]) / prev_row["Close"]
                    ) * 100

                    daily_snapshot.append(
                        {
                            "Date": df.index[-1].strftime("%Y-%m-%d"),
                            "Ticker": ticker,
                            "Close": round(last_row["Close"], 2),
                            "Change_Pct": round(pct_change, 2),
                            "Volume": int(last_row["Volume"]),
                            "SMA_50": last_row["SMA_50"],
                            "SMA_150": last_row["SMA_150"],
                            "SMA_200": last_row["SMA_200"],
                            "52W_High": last_row["52W_High"],
                            "52W_Low": last_row["52W_Low"],
                        }
                    )

                    valid_tickers.append(ticker)
                    success_count += 1
            except Exception as e:
                print(f"⚠️ Error downloading {ticker}: {e}")
                continue

        time.sleep(1)

    print(f"\n🔎 [Phase 2/2] กำลังดึงข้อมูลปัจจัยพื้นฐาน (PE, EPS, ROE)...")
    fundamental_data = {}

    # 🛡️ กางร่มครอบ ThreadPool ทั้งหมดด้วย redirect_stdout/stderr (เปิดครั้งเดียว ป้องกัน Thread ชนกัน)
    with open(os.devnull, "w") as fnull:
        with redirect_stdout(fnull), redirect_stderr(fnull):
            with ThreadPoolExecutor(max_workers=FUND_WORKERS) as executor:
                futures = {
                    executor.submit(fetch_fundamentals, t): t for t in valid_tickers
                }
                for future in as_completed(futures):
                    t, fund_data = future.result()
                    fundamental_data[t] = fund_data

    apply_growth_cache_fallback(fundamental_data)

    for row in daily_snapshot:
        t = row["Ticker"]
        row.update(fundamental_data.get(t, {}))

    print(f"\n🔎 [Phase 2.5/2] กำลังดึงข้อมูล TradingView Fundamentals เสริมความแม่นยำ...")
    try:
        import requests
        tv_payload = {
            "filter": [{"left": "name", "operation": "in_range", "right": valid_tickers}],
            "options": {"lang": "en"},
            "markets": ["thailand"],
            "columns": ["name", "price_earnings_ttm", "earnings_per_share_basic_ttm", "return_on_equity", "market_cap_basic"],
            "range": [0, len(valid_tickers)]
        }
        res = requests.post("https://scanner.tradingview.com/thailand/scan", json=tv_payload, timeout=15)
        if res.status_code == 200:
            tv_data = res.json().get("data", [])
            tv_dict = {item["d"][0]: item["d"] for item in tv_data if len(item.get("d", [])) >= 5}
            for row in daily_snapshot:
                t = row["Ticker"]
                if t in tv_dict:
                    d = tv_dict[t]
                    if d[1] is not None: row["PE_Ratio"] = d[1]
                    if d[2] is not None: row["EPS"] = d[2]
                    if d[3] is not None: row["ROE"] = d[3] / 100.0
                    if d[4] is not None: row["Market_Cap"] = d[4]
    except Exception as e:
        print(f"⚠️ Error fetching TradingView data: {e}")

    if daily_snapshot:
        df_daily = pd.DataFrame(daily_snapshot)
        for col in ["Close", "Change_Pct", "PE_Ratio", "EPS"]:
            if col in df_daily.columns:
                df_daily[col] = pd.to_numeric(df_daily[col], errors="coerce").round(2)
        if "ROE" in df_daily.columns:
            df_daily["ROE"] = pd.to_numeric(df_daily["ROE"], errors="coerce").round(4)
        # 💡 แปลงค่า Growth ที่ดึงจาก yfinance (เป็นอัตราส่วน 0.xx) ให้เป็น %
        if "Revenue_Growth_YoY" in df_daily.columns:
            df_daily["Revenue_Growth_YoY"] = (
                pd.to_numeric(df_daily["Revenue_Growth_YoY"], errors="coerce") * 100
            )
        if "Profit_Growth_YoY" in df_daily.columns:
            df_daily["Profit_Growth_YoY"] = (
                pd.to_numeric(df_daily["Profit_Growth_YoY"], errors="coerce") * 100
            )
        # 💡 ส่วน QoQY ที่เราคำนวณเองเป็น % แล้ว แค่แปลงเป็นตัวเลขก็พอ
        if "NetProfit_Growth_QoQY" in df_daily.columns:
            df_daily["NetProfit_Growth_QoQY"] = pd.to_numeric(
                df_daily["NetProfit_Growth_QoQY"], errors="coerce"
            ).round(2)

        # 🎯 เซฟไฟล์ Master Snapshot ลงโฟลเดอร์ตาม config
        df_daily.to_csv(DAILY_FILE, index=False, encoding="utf-8-sig")

    print("\n" + "=" * 80)
    print(f"🎉 อัปเดตเสร็จสมบูรณ์ (สำเร็จ {success_count}/{len(tickers)} ตัว)")
    print("=" * 80)


if __name__ == "__main__":
    download_history_and_build_snapshot()
