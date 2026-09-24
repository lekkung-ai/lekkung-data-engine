import math
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

# 🔌 เชื่อมต่อระบบเดิมของคุณ (ดึง Path จาก config.py)
sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import ADTV_MIN_MB, HISTORY_DIR, RESULTS_DIR, RS_FILE  # type: ignore
from utils import calculate_adtv, load_full_df, load_price_volume  # type: ignore

# DUPLICATED from scan_sepa.py: fetch_fundamental_data() + fundamental_pass_for() + FUND thresholds
# keep in sync until consolidated into utils.py (line numbers drift — do NOT re-add them)
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


def pct_from_52w_high(file_path, price):
    """%_From_High เป็น string เช่น "-5.6%" หรือ None — เพิ่มเติมอย่างเดียว ไม่กระทบ pass/fail ของ Kell.

    # 52W high = intraday 252-day, mirror scan_sepa.py:126 — keep in sync
    ประวัติ High < 252 วัน / high ไม่ finite หรือ 0 / pct ไม่ finite → None
    """
    try:
        high = pd.to_numeric(load_full_df(file_path)["High"], errors="coerce").dropna()
        if len(high) < 252:
            return None
        high_52w = float(high.iloc[-252:].max())
        if not math.isfinite(high_52w) or high_52w <= 0:
            return None
        pct = ((price - high_52w) / high_52w) * 100
        if not math.isfinite(pct):
            return None
        return f"{round(pct, 2)}%"
    except Exception:
        return None


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

    # RS Rating — Kell ไม่ gate RS จึงไม่มีค่า = None (ไม่ใช่ 0 ซึ่งเป็นค่าปลอม)
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

    # วนลูปอ่านไฟล์หุ้นทีละตัวจากโฟลเดอร์ history
    for file_path in HISTORY_DIR.glob("*.csv"):
        ticker = file_path.stem
        try:
            # เปลี่ยน min_length เป็น 220 เพราะเราต้องเช็คเส้น SMA200 ย้อนหลังไปอีก 20 วัน
            price_series, vol_series = load_price_volume(file_path, min_length=220)
            if price_series is None:
                continue

            # --- [ด่านที่ 1] Liquidity Filter (สภาพคล่อง) ---
            # ADTV floor ไม่ใช่ส่วนหนึ่งของ Trend Template/Kell's Signal — เป็น
            # liquidity gate ที่เราใส่เอง หุ้นที่ผ่านด่าน 2-3 แต่สภาพคล่องต่ำกว่า
            # floor ยังต้อง "โผล่พร้อม flag" ไม่ใช่หายเงียบ (ตัดสินใจที่หน้าเว็บ)
            vol_mb = calculate_adtv(price_series, vol_series, window=5)
            low_liquidity = vol_mb < ADTV_MIN_MB

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

                rs_val = rs_dict.get(ticker.upper(), None)
                try:
                    rs_val = float(rs_val) if rs_val is not None else None
                    if rs_val is not None and not math.isfinite(rs_val):
                        rs_val = None
                except (TypeError, ValueError):
                    rs_val = None

                passed_stocks.append(
                    {
                        "Ticker": ticker,
                        "Signal": signal_type,
                        "Price": round(curr_price, 2),
                        "EMA10": round(curr_ema10, 2),
                        "Dist_EMA10_%": round(extension_pct, 2),
                        "ADTV(MB)": round(vol_mb, 1),
                        "Status": "🔥 Leader Ready",
                        "Low_Liquidity": low_liquidity,
                        # เพิ่มสำหรับ composite (additive — 8 field ข้างบนไม่เปลี่ยน)
                        "RS_Rating": rs_val,
                        "Fundamental_Pass": fundamental_pass_for(ticker, fundamentals),
                        "%_From_High": pct_from_52w_high(file_path, curr_price),
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
