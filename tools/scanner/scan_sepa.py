import sys
import warnings
from pathlib import Path

import pandas as pd
import requests

warnings.filterwarnings("ignore")

# 🔌 เชื่อมต่อ config.py
sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import ADTV_MIN_MB, HISTORY_DIR, RS_FILE, SEPA_FILE  # type: ignore
from utils import calculate_adtv, load_price_volume, prepare_stock_data  # type: ignore

# ── SEPA Fundamental Filter (Phase 3) ────────────────────────────────────────
# Data source: TradingView's scanner API (scanner.tradingview.com/thailand/scan)
# — same source already used by the dashboard's /api/fundamental route.
# Verified against Yahoo Finance quoteSummary on 5 sample tickers first: Yahoo
# only returned quarterly EPS for 4/5 (missing FSMART) and only exposes 4
# trailing quarters (not enough to compute a true YoY delta reliably without
# extra date-matching, since its wider fundamentals-timeseries history has
# gaps). TradingView returns pre-computed YoY growth for 5/5 with no gaps.
#
# "EPS Acceleration" has no direct "prior quarter's YoY growth" field on
# TradingView, so as a disclosed proxy this compares the latest quarter's YoY
# growth against the trailing-twelve-month YoY growth: if the quarter is
# outpacing the trailing-year average, growth is accelerating.
TV_FUNDAMENTAL_COLUMNS = [
    "name",
    "total_revenue_yoy_growth_fq",
    "earnings_per_share_diluted_yoy_growth_fq",
    "earnings_per_share_diluted_yoy_growth_ttm",
]
EPS_YOY_MIN = 20.0
REVENUE_YOY_MIN = 15.0


def fetch_fundamental_data() -> dict:
    """ดึง EPS/Revenue YoY growth ของหุ้นไทยทั้งตลาดในคำขอเดียว (bulk scan)."""
    try:
        res = requests.post(
            "https://scanner.tradingview.com/thailand/scan",
            json={"markets": ["thailand"], "columns": TV_FUNDAMENTAL_COLUMNS, "range": [0, 3000]},
            timeout=15,
        )
        res.raise_for_status()
        rows = res.json().get("data", [])
    except Exception as e:
        print(f"⚠️ ดึงข้อมูล Fundamental จาก TradingView ไม่สำเร็จ: {e}")
        return {}

    out = {}
    for row in rows:
        d = row.get("d", [])
        if len(d) < 4 or not d[0]:
            continue
        ticker = str(d[0]).upper()
        out[ticker] = {"revenue_yoy": d[1], "eps_yoy": d[2], "eps_yoy_ttm": d[3]}
    return out


def fundamental_pass_for(ticker: str, fundamentals: dict):
    """None = ไม่มีข้อมูลพอตัดสิน, True/False = ผ่าน/ไม่ผ่านเกณฑ์ fundamental"""
    f = fundamentals.get(ticker.upper())
    if not f:
        return None
    eps_yoy, rev_yoy, eps_yoy_ttm = f["eps_yoy"], f["revenue_yoy"], f["eps_yoy_ttm"]
    if eps_yoy is None or rev_yoy is None or eps_yoy_ttm is None:
        return None
    eps_growth_ok = eps_yoy > EPS_YOY_MIN
    revenue_growth_ok = rev_yoy > REVENUE_YOY_MIN
    eps_accelerating = eps_yoy > eps_yoy_ttm
    return bool(eps_growth_ok and revenue_growth_ok and eps_accelerating)


def scan_sepa():
    # อ่าน RS Score
    rs_dict = {}
    if RS_FILE.exists():
        try:
            df_watch = pd.read_csv(RS_FILE, encoding="utf-8")
            rs_dict = dict(
                zip(df_watch["Ticker"].astype(str).str.upper(), df_watch["RS_Rating"])
            )
        except:
            pass

    fundamentals = fetch_fundamental_data()

    print("🚀 เริ่มสแกนกฎ SEPA จากประวัติราคา (อิกนอร์เงื่อนไข RS)...")
    passed_stocks = []

    if not HISTORY_DIR.exists():
        print("❌ ไม่พบโฟลเดอร์ history")
        return

    for file_path in HISTORY_DIR.glob("*.csv"):
        ticker = file_path.stem
        try:
            df, adtv_mb = prepare_stock_data(file_path, min_days=250)
            if df is None or adtv_mb < ADTV_MIN_MB:
                continue

            price_series = df["Close"]

            sma_50 = price_series.rolling(window=50).mean()
            sma_150 = price_series.rolling(window=150).mean()
            sma_200 = price_series.rolling(window=200).mean()

            current_price = float(price_series.iloc[-1])
            cur_sma_50 = float(sma_50.iloc[-1])
            cur_sma_150 = float(sma_150.iloc[-1])
            cur_sma_200 = float(sma_200.iloc[-1])

            # True 52-week high/low from the day's actual intraday High/Low,
            # not the closing price - a Close-based max/min is trivially "hit"
            # by that same day's own Close, which made T7 (near-high) pass far
            # too often. `current_price` below stays Close-based, since T6/T7
            # ask "is the current close within X% of the true high/low band".
            low_52_week = float(df["Low"].iloc[-252:].min())
            high_52_week = float(df["High"].iloc[-252:].max())
            sma_200_1m_ago = float(sma_200.iloc[-20])

            cond_1 = (current_price > cur_sma_150) and (current_price > cur_sma_200)
            cond_2 = cur_sma_150 > cur_sma_200
            cond_3 = cur_sma_200 > sma_200_1m_ago
            cond_4 = (cur_sma_50 > cur_sma_150) and (cur_sma_50 > cur_sma_200)
            cond_5 = current_price > cur_sma_50
            cond_6 = current_price >= (1.30 * low_52_week)
            cond_7 = current_price >= (0.75 * high_52_week)

            if all([cond_1, cond_2, cond_3, cond_4, cond_5, cond_6, cond_7]):
                rs_rating = rs_dict.get(ticker.upper(), 0)

                # [Minervini Rule 8] RS Rating must be at least 70
                try:
                    if float(rs_rating) < 70:
                        continue
                except ValueError:
                    continue

                pct_from_high = ((current_price - high_52_week) / high_52_week) * 100

                passed_stocks.append(
                    {
                        "Ticker": ticker,
                        "RS_Rating": rs_rating,
                        "Price": round(current_price, 2),
                        "SL(-8%)": round(current_price * 0.92, 2),
                        "TP(+16%)": round(current_price * 1.16, 2),
                        "SMA_50": round(cur_sma_50, 2),
                        "SMA_200": round(cur_sma_200, 2),
                        "52W_High": round(high_52_week, 2),
                        "%_From_High": f"{round(pct_from_high, 2)}%",
                        "ADTV(MB)": round(adtv_mb, 1),
                        # Trend Template — 8 เงื่อนไขตาม Minervini (p.79), เก็บ
                        # เป็น boolean แยกข้อไว้ให้หน้าเว็บแสดง checkmark ได้
                        "T1_Price_Above_150_200": cond_1,
                        "T2_SMA50_Above_150_200": cond_4,
                        "T3_SMA150_Above_SMA200": cond_2,
                        "T4_SMA200_Trending_Up": cond_3,
                        "T5_Price_Above_SMA50": cond_5,
                        "T6_Above_52wLow_30pct": cond_6,
                        "T7_Within_52wHigh_25pct": cond_7,
                        "T8_RS_At_Least_70": True,
                        "Fundamental_Pass": fundamental_pass_for(ticker, fundamentals),
                    }
                )
        except Exception as e:
            # print(f"⚠️ Error processing {ticker}: {e}")
            pass

    print("\n" + "=" * 80)
    print("🎯 MINERVINI SEPA DASHBOARD (สแกนโครงสร้างเพียวๆ)")
    print("=" * 80)

    if passed_stocks:
        final_df = pd.DataFrame(passed_stocks)
        final_df["Sort_RS"] = pd.to_numeric(
            final_df["RS_Rating"], errors="coerce"
        ).fillna(0)
        final_df = final_df.sort_values(by="Sort_RS", ascending=False).drop(
            columns=["Sort_RS"]
        )

        final_df.to_csv(SEPA_FILE, index=False, encoding="utf-8-sig")
        final_df.reset_index(drop=True, inplace=True)
        final_df.index = final_df.index + 1

        print(final_df.to_string())
        print("-" * 80)
        print(f"✨ สแกนเจอหุ้นทรงกระทิง (SEPA): {len(final_df)} ตัว")
    else:
        print("📉 ไม่มีหุ้นทรง SEPA ในตลาดเลย")


if __name__ == "__main__":
    scan_sepa()
