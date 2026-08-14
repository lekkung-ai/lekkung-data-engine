"""
calculate_breadth.py
คำนวณ SET Market Breadth (%เหนือ MA10/20/50/200, %ทำ 52W High/Low ใหม่รายวัน),
SET Index OHLCV + FTD/DD markers, market_stage สรุป, และ sparklines (close ย้อนหลัง)
จาก price history ที่ดาวน์โหลดไว้แล้วใน data/history/

เขียนผลเป็น breadth.json และ sparklines.json ไปที่ data/results/output/
(pipeline เดิม - update_stockdesk.bat และ daily-scan.yml - copy *.json จากโฟลเดอร์นี้
ไป stockdesk/data/scans/ อยู่แล้ว ไม่ต้องแก้ path เพิ่ม)

รันต่อท้าย pipeline เดิม (ต่อจาก convert_to_json.py ใน run_all.py scan_scripts)
"""
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import json
import time
from datetime import datetime, timezone, timedelta

import pandas as pd
import yfinance as yf

sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import (
    HISTORY_DIR,
    RESULTS_DIR,
    BREADTH_MA_PERIODS,
    BREADTH_BACKFILL_DAYS,
    BREADTH_MIN_HISTORY_DAYS,
    NEW_HIGH_LOW_WINDOW,
    SPARKLINE_DAYS,
    SET_INDEX_YF_SYMBOL,
    SET_INDEX_FETCH_PERIOD,
    DD_MIN_DECLINE_PCT,
    FTD_MIN_GAIN_PCT,
    FTD_WINDOW_START_DAY,
    FTD_WINDOW_END_DAY,
    MARKET_STAGE_DD_CORRECTION,
    MARKET_STAGE_DD_PRESSURE,
    MARKET_STAGE_MA50_PRESSURE_PCT,
)

OUTPUT_DIR = RESULTS_DIR / "output"
BKK_TZ = timezone(timedelta(hours=7))
DD_MARKER_LOOKBACK_DAYS = 25  # market_stage ใช้นับ DD ใน 25 session ล่าสุด


def now_iso() -> str:
    return datetime.now(BKK_TZ).strftime("%Y-%m-%dT%H:%M:%S+07:00")


# ─── โหลด price history ทั้ง universe ──────────────────────────────────────────

def load_ticker_close(file_path: Path) -> pd.Series | None:
    """อ่าน history 1 ตัว คืน Close series ที่ index เป็น Date (ตัดทิ้งถ้าประวัติสั้นกว่าเกณฑ์)"""
    df = pd.read_csv(file_path)
    df = df.rename(columns={df.columns[0]: "Date"})
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    df = df.dropna(subset=["Date", "Close"]).set_index("Date").sort_index()
    if len(df) < BREADTH_MIN_HISTORY_DAYS:
        return None
    series = df["Close"]
    series.name = file_path.stem.strip()
    return series


def load_universe_closes() -> dict[str, pd.Series]:
    closes: dict[str, pd.Series] = {}
    for file_path in sorted(HISTORY_DIR.glob("*.csv")):
        try:
            series = load_ticker_close(file_path)
        except Exception:
            series = None
        if series is not None and not series.empty:
            closes[series.name] = series
    return closes


# ─── SET Index (^SET.BK ไม่เคยถูกเก็บใน data/history ของ pipeline นี้มาก่อน ต้องดึงสด) ──

def fetch_set_index() -> pd.DataFrame:
    last_err = None
    for attempt in range(3):
        try:
            df = yf.download(
                SET_INDEX_YF_SYMBOL,
                period=SET_INDEX_FETCH_PERIOD,
                interval="1d",
                auto_adjust=True,
                progress=False,
            )
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0] for c in df.columns]
            df = df.dropna(subset=["Close"]).sort_index()
            if not df.empty:
                return df
            last_err = "empty result"
        except Exception as e:
            last_err = str(e)
        if attempt < 2:
            wait = 3 * (2 ** attempt)   # 3s, 6s, (12s ถ้ามี attempt 3)
            print(f"  ⏳ [Breadth] SET Index ดึงไม่ได้ (ครั้งที่ {attempt+1}/3): {last_err} — รอ {wait}s แล้วลองใหม่")
            time.sleep(wait)
    print(f"  ❌ [Breadth] SET Index ดึงไม่สำเร็จหลัง retry 3 ครั้ง: {last_err}")
    return pd.DataFrame()


FTD_DD_RULE_TEXT = (
    "DD (Distribution Day): SET index ปิดลบ >= {dd_pct}% ด้วย volume มากกว่าวันก่อนหน้า "
    "(นับทุกครั้งที่เข้าเงื่อนไข ไม่ขึ้นกับสถานะ rally attempt). "
    "FTD (Follow-Through Day): เกิดได้เฉพาะวันที่ {win_start}-{win_end} ของ 'rally attempt' "
    "(วันที่ 1 = วันแรกที่ index ปิดบวกหลังวันก่อนหน้าปิดลบ) โดยวันนั้นต้องปิดบวก >= {ftd_pct}% "
    "ด้วย volume มากกว่าวันก่อนหน้า. rally attempt ถือว่าล้มเหลวและเริ่มนับวันที่ 1 ใหม่ ถ้า close "
    "หลุดต่ำกว่า low ของวันที่ 1 ก่อนเกิด FTD. หลัง FTD ยืนยันแล้ว จะกลับไปเฝ้าหา rally attempt ใหม่ "
    "อีกครั้งเมื่อ index ปิดลบเป็นวันแรก (pullback ครั้งถัดไป). "
    "หมายเหตุ: เป็นการตีความเกณฑ์ O'Neil แบบย่อของทีมเรา ไม่ใช่สูตรทางการของ IBD "
    "ปรับ threshold ได้ที่ config.py (DD_MIN_DECLINE_PCT / FTD_MIN_GAIN_PCT / FTD_WINDOW_START_DAY / FTD_WINDOW_END_DAY)"
).format(
    dd_pct=DD_MIN_DECLINE_PCT,
    ftd_pct=FTD_MIN_GAIN_PCT,
    win_start=FTD_WINDOW_START_DAY,
    win_end=FTD_WINDOW_END_DAY,
)


def compute_ftd_dd(set_df: pd.DataFrame) -> list[str | None]:
    n = len(set_df)
    marker: list[str | None] = [None] * n
    close = set_df["Close"].to_numpy()
    low = set_df["Low"].to_numpy()
    vol = set_df["Volume"].to_numpy()

    state = "searching"  # searching -> counting -> confirmed
    day1_low = None
    day_count = 0

    for i in range(1, n):
        pct_chg = (close[i] - close[i - 1]) / close[i - 1] * 100
        higher_vol = vol[i] > vol[i - 1]

        if pct_chg <= -DD_MIN_DECLINE_PCT and higher_vol:
            marker[i] = "DD"

        if state == "searching":
            if close[i] > close[i - 1]:
                state = "counting"
                day1_low = low[i]
                day_count = 1
        elif state == "counting":
            day_count += 1
            if close[i] < day1_low:
                state = "searching"
                day1_low = None
                day_count = 0
            elif (
                FTD_WINDOW_START_DAY <= day_count <= FTD_WINDOW_END_DAY
                and pct_chg >= FTD_MIN_GAIN_PCT
                and higher_vol
            ):
                marker[i] = "FTD"
                state = "confirmed"
        elif state == "confirmed":
            if close[i] < close[i - 1]:
                state = "searching"

    return marker


# ─── Breadth stats (%เหนือ MA, %52W high/low ใหม่) ─────────────────────────────

def compute_breadth_matrix(close_df: pd.DataFrame) -> pd.DataFrame:
    result = pd.DataFrame(index=close_df.index)

    for period in BREADTH_MA_PERIODS:
        ma = close_df.rolling(period, min_periods=period).mean()
        valid = ma.notna() & close_df.notna()
        denom = valid.sum(axis=1)
        numer = ((close_df > ma) & valid).sum(axis=1)
        result[f"pct_above_ma{period}"] = (numer / denom * 100).round(2)
        result[f"denom_ma{period}"] = denom

    roll_max = close_df.rolling(NEW_HIGH_LOW_WINDOW, min_periods=NEW_HIGH_LOW_WINDOW).max()
    roll_min = close_df.rolling(NEW_HIGH_LOW_WINDOW, min_periods=NEW_HIGH_LOW_WINDOW).min()
    valid_hl = roll_max.notna() & close_df.notna()
    denom_hl = valid_hl.sum(axis=1)
    result["pct_new_52wh"] = (((close_df >= roll_max) & valid_hl).sum(axis=1) / denom_hl * 100).round(2)
    result["pct_new_52wl"] = (((close_df <= roll_min) & valid_hl).sum(axis=1) / denom_hl * 100).round(2)
    result["denom_52w"] = denom_hl

    return result


def classify_market_stage(breadth_tail: pd.DataFrame, marker_tail: list[str | None]) -> dict:
    dd_count_25d = sum(1 for m in marker_tail[-DD_MARKER_LOOKBACK_DAYS:] if m == "DD")
    pct_ma50_today = breadth_tail["pct_above_ma50"].iloc[-1]
    pct_ma50_today = None if pd.isna(pct_ma50_today) else float(pct_ma50_today)

    if dd_count_25d >= MARKET_STAGE_DD_CORRECTION:
        stage = "Correction"
    elif dd_count_25d >= MARKET_STAGE_DD_PRESSURE or (
        pct_ma50_today is not None and pct_ma50_today < MARKET_STAGE_MA50_PRESSURE_PCT
    ):
        stage = "Under Pressure"
    else:
        stage = "Uptrend"

    rule_text = (
        f"stage = 'Correction' ถ้า dd_count_25d >= {MARKET_STAGE_DD_CORRECTION}; "
        f"'Under Pressure' ถ้า dd_count_25d >= {MARKET_STAGE_DD_PRESSURE} หรือ "
        f"pct_above_ma50 วันล่าสุด < {MARKET_STAGE_MA50_PRESSURE_PCT}%; มิฉะนั้น 'Uptrend'. "
        "เกณฑ์นี้เป็นการตีความของทีมเรา ปรับได้ที่ config.py "
        "(MARKET_STAGE_DD_CORRECTION / MARKET_STAGE_DD_PRESSURE / MARKET_STAGE_MA50_PRESSURE_PCT)"
    )

    return {
        "stage": stage,
        "dd_count_25d": dd_count_25d,
        "pct_above_ma50_today": pct_ma50_today,
        "rule": rule_text,
    }


# ─── Sparklines ─────────────────────────────────────────────────────────────

def build_sparklines(closes: dict[str, pd.Series]) -> dict[str, list[float]]:
    out = {}
    for ticker, series in closes.items():
        tail = series.dropna().tail(SPARKLINE_DAYS)
        out[ticker] = [round(float(v), 3) for v in tail.tolist()]
    return out


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    print("📉 [Breadth] กำลังโหลด price history ทั้ง universe...")
    closes = load_universe_closes()
    print(f"  ✓ universe: {len(closes)} ticker (>= {BREADTH_MIN_HISTORY_DAYS} วัน)")

    if not closes:
        print("❌ [Breadth] ไม่พบ ticker ที่มีประวัติพอ ข้ามการคำนวณ")
        return

    print(f"📉 [Breadth] กำลังดึง SET Index ({SET_INDEX_YF_SYMBOL}) ผ่าน yfinance...")
    set_df = fetch_set_index()
    if set_df.empty:
        print("❌ [Breadth] ดึง SET Index ไม่สำเร็จ ข้ามการคำนวณ")
        return
    print(f"  ✓ SET Index: {len(set_df)} แท่ง ({set_df.index[0].date()} - {set_df.index[-1].date()})")

    marker = compute_ftd_dd(set_df)
    set_df = set_df.copy()
    set_df["marker"] = marker

    close_df = pd.concat(closes, axis=1).sort_index()
    close_df = close_df.reindex(set_df.index)  # บังคับ align วันที่ให้ตรงกับ SET Index ทุกกราฟ

    valid_rows = close_df.notna().any(axis=1)
    close_df = close_df[valid_rows]
    set_df = set_df.loc[close_df.index]

    breadth_df = compute_breadth_matrix(close_df)

    market_stage = classify_market_stage(breadth_df, set_df['marker'].tolist())
    print(f"  ✓ market_stage: {market_stage['stage']} (DD 25d: {market_stage['dd_count_25d']}, "
          f"%>MA50: {market_stage['pct_above_ma50_today']})")

    tail_n = min(BREADTH_BACKFILL_DAYS, len(set_df))
    set_tail = set_df.tail(tail_n)
    breadth_tail = breadth_df.tail(tail_n)

    set_index_records = []
    for d, row in set_tail.iterrows():
        set_index_records.append({
            "date": d.strftime("%Y-%m-%d"),
            "open": round(float(row["Open"]), 2),
            "high": round(float(row["High"]), 2),
            "low": round(float(row["Low"]), 2),
            "close": round(float(row["Close"]), 2),
            "volume": int(row["Volume"]),
            # compute_ftd_dd() only ever assigns None/"DD"/"FTD", but pandas
            # can silently upcast an object column of None+str to float64/NaN
            # during a later reindex/concat depending on the pandas version -
            # confirmed happening on the CI runner's pinned version but not
            # locally, which let a bare `NaN` token (invalid JSON - Python's
            # json module writes it anyway unless allow_nan=False) reach
            # breadth.json and break every Vercel build importing it as a
            # module (2026-07-21 incident). pd.isna() catches None either way.
            "marker": None if pd.isna(row["marker"]) else row["marker"],
        })

    breadth_records = []
    for d, row in breadth_tail.iterrows():
        rec = {"date": d.strftime("%Y-%m-%d")}
        for period in BREADTH_MA_PERIODS:
            v = row[f"pct_above_ma{period}"]
            rec[f"pct_above_ma{period}"] = None if pd.isna(v) else float(v)
            rec[f"denom_ma{period}"] = int(row[f"denom_ma{period}"])
        for key in ("pct_new_52wh", "pct_new_52wl"):
            v = row[key]
            rec[key] = None if pd.isna(v) else float(v)
        rec["denom_52w"] = int(row["denom_52w"])
        breadth_records.append(rec)

    out = {
        "generated_at": now_iso(),
        "universe": {
            "count": len(closes),
            "min_history_days": BREADTH_MIN_HISTORY_DAYS,
            "note": "ticker ที่มีข้อมูลราคาปิดย้อนหลัง >= min_history_days วัน ใน data/history/ "
                    "(universe เดียวกับที่ scan_*.py อื่นๆ ใช้กรอง MIN_HISTORY_DAYS)",
        },
        "config": {
            "ma_periods": BREADTH_MA_PERIODS,
            "new_high_low_window": NEW_HIGH_LOW_WINDOW,
            "dd_min_decline_pct": DD_MIN_DECLINE_PCT,
            "ftd_min_gain_pct": FTD_MIN_GAIN_PCT,
            "ftd_window_start_day": FTD_WINDOW_START_DAY,
            "ftd_window_end_day": FTD_WINDOW_END_DAY,
        },
        "ftd_dd_rule": FTD_DD_RULE_TEXT,
        "market_stage": market_stage,
        "set_index": set_index_records,
        "breadth": breadth_records,
    }

    # allow_nan=False turns any future stray NaN/Infinity into a loud crash
    # right here in the pipeline log, instead of writing invalid JSON that
    # only surfaces hours later as a broken Vercel build with no obvious
    # link back to this script (exactly what happened 2026-07-21).
    out_path = OUTPUT_DIR / "breadth.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(f"  ✓ breadth.json: {len(breadth_records)} วัน, {out_path.stat().st_size / 1024:.1f} KB")

    print("📉 [Breadth] กำลังสร้าง sparklines...")
    sparklines = build_sparklines(closes)
    sparklines_out = {
        "generated_at": now_iso(),
        "days": SPARKLINE_DAYS,
        "data": sparklines,
    }
    spark_path = OUTPUT_DIR / "sparklines.json"
    spark_path.write_text(json.dumps(sparklines_out, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    print(f"  ✓ sparklines.json: {len(sparklines)} ticker, {spark_path.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
