"""
convert_to_json.py
แปลงผลลัพธ์ scan (CSV) เป็น JSON สำหรับให้ StockDesk dashboard อ่านใช้

วิธีใช้:
1. วางไฟล์นี้ไว้ใน folder ที่มีผลลัพธ์ CSV (เช่น Scan/results/)
2. แก้ INPUT_DIR / OUTPUT_DIR ด้านล่างให้ตรงกับ path จริงของคุณ
3. รัน: python convert_to_json.py
4. จะได้โฟลเดอร์ output ที่มีไฟล์ json พร้อม copy ไปวางใน dashboard/data/scans/
"""

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd
import json
import math
from pathlib import Path
from datetime import datetime, timezone, timedelta
import numpy as np

# ปรับ path ตรงนี้ให้ตรงกับเครื่องจริง
INPUT_DIR = Path(__file__).parent          # โฟลเดอร์ที่มีไฟล์ CSV ผลสแกน
OUTPUT_DIR = Path(__file__).parent / "output"  # โฟลเดอร์ที่จะเก็บไฟล์ JSON ที่แปลงแล้ว
DAILY_PRICES_FILE = INPUT_DIR / "daily_prices.csv"  # universe เต็ม พร้อม Revenue/NetProfit growth ราย ticker

FILES = {
    "sepa": "sepa_result.csv",
    "oliver_kell": "oliver_kell_signals.csv",
    "market_stage": "pine_stages.csv",   # แก้บั๊กแล้ว verify ตรงกับต้นฉบับ Pine Script "Market Stage"
    "breakout": "accumulation_breakout.csv",
    "rs_ranking": "rs_ranking.csv",
    "stage_all": "stage_all.csv",        # Stage ทุกหุ้น (ไม่มี ADTV filter) สำหรับ SectorTickerGrid
    "ppbp": "ppbp_result.csv",           # Pocket Pivot Buy Point
    "lekkung": "lekkung_growth_scan.csv",
    "lekkung_incomplete": "lekkung_incomplete.csv",  # tickers past price/liquidity screen but with incomplete fundamentals - transparency list, not a scan result
    "oneil": "canslim_result.csv",
    "weinstein": "weinstein_stages_result.csv",
}

# หมายเหตุ: ไฟล์ต่อไปนี้ "พักไว้ก่อน" ไม่ใช้ในรอบนี้
#   - wyckoff_stages.csv           -> logic คนละแบบกับ pine_stages, เก็บไว้เผื่ออนาคตแต่ไม่ใช้เป็น stage หลัก
#   - sepa_stage2_combo.csv        -> เป็นแค่ subset ของ sepa ∩ stage2 คำนวณเองได้จากไฟล์อื่น


def read_csv_safe(path: Path) -> pd.DataFrame | None:
    """อ่าน CSV โดยจัดการ BOM (﻿) ที่ Excel/Windows มักแถมมาให้อัตโนมัติ"""
    if not path.exists():
        print(f"  ⚠️  ไม่พบไฟล์: {path.name} (ข้าม)")
        return None
    return pd.read_csv(path, encoding="utf-8-sig")


def df_to_records(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.to_json(orient="records"))


def build_individual_jsons() -> dict[str, list[dict]]:
    """แปลงแต่ละไฟล์ CSV เป็น JSON แยก เก็บไว้ใช้ในหน้า scanner sub-view"""
    results = {}
    for key, filename in FILES.items():
        df = read_csv_safe(INPUT_DIR / filename)
        if df is None:
            results[key] = []
            continue
        records = df_to_records(df)
        results[key] = records
        print(f"  ✓ {filename}: {len(records)} แถว")
    return results


def build_growth_map() -> dict[str, dict]:
    """
    Revenue_Growth_YoY / NetProfit_Growth_QoQY ราย ticker จาก daily_prices.csv (universe เต็ม
    ทุกหุ้นที่ผ่าน step ดาวน์โหลดราคา ไม่ใช่แค่ตัวที่ผ่าน scan_lekkung_growth.py ซึ่งกรองเฉพาะหุ้นที่
    Growth เป็นบวกทั้งคู่แล้ว - ใช้อันนี้แทนเวลาต้องแยก แดง/ม่วง/เขียว ของหุ้นทั้ง universe)
    NaN ของ Yahoo (รู้ปัญหาอยู่แล้ว) ถูกเก็บเป็น None ไม่ปัดเป็น 0 เพื่อไม่ให้ปนกับ "ไม่โต"
    """
    df = read_csv_safe(DAILY_PRICES_FILE)
    if df is None or "Ticker" not in df.columns:
        return {}
    growth_map = {}
    for _, row in df.iterrows():
        yoy = row.get("Revenue_Growth_YoY")
        qoq = row.get("NetProfit_Growth_QoQY")
        growth_map[row["Ticker"]] = {
            "growth_yoy": None if pd.isna(yoy) else float(yoy),
            "growth_qoq": None if pd.isna(qoq) else float(qoq),
        }
    return growth_map


def build_fundamentals_map() -> dict[str, dict]:
    """
    อ่าน PE_Ratio และ ROE ราย ticker จาก daily_prices.csv
    หาก EPS <= 0, EPS เป็น None, PE เป็น NaN/null, <= 0 (ขาดทุน) หรือ inf/nan จะเก็บเป็น None (null ใน JSON)
    หาก ROE เป็น NaN, null หรือ inf/nan จะเก็บเป็น None (null ใน JSON)
    """
    df = read_csv_safe(DAILY_PRICES_FILE)
    if df is None or "Ticker" not in df.columns:
        print("  ⚠️ WARNING: ไม่พบไฟล์ daily_prices.csv หรือคอลัมน์ Ticker — กำหนด PE_Ratio/ROE เป็น null")
        return {}

    fund_map = {}
    valid_pe_count = 0

    for _, row in df.iterrows():
        ticker = row["Ticker"]
        pe_val = row.get("PE_Ratio")
        roe_val = row.get("ROE")
        eps_val = row.get("EPS") if "EPS" in row else None

        # EPS Guard: ถ้า EPS <= 0 หรือ EPS เป็น None/NaN/inf ให้ถือว่าไม่ผ่าน PE guard
        eps_valid = True
        if eps_val is not None and pd.notna(eps_val):
            try:
                eps_float = float(eps_val)
                if eps_float <= 0 or not math.isfinite(eps_float):
                    eps_valid = False
            except (ValueError, TypeError):
                eps_valid = False

        # PE Guard: ถ้าเป็น NaN, null, <= 0 (ขาดทุน) หรือ inf/nan ให้เป็น None
        pe = None
        if eps_valid and pd.notna(pe_val):
            try:
                pe_float = float(pe_val)
                if pe_float > 0 and math.isfinite(pe_float):
                    pe = pe_float
                    valid_pe_count += 1
            except (ValueError, TypeError):
                pe = None

        # ROE Guard: ถ้าเป็น NaN, null หรือ inf/nan ให้เป็น None
        roe = None
        if pd.notna(roe_val):
            try:
                roe_float = float(roe_val)
                if math.isfinite(roe_float):
                    roe = roe_float
            except (ValueError, TypeError):
                roe = None

        fund_map[ticker] = {
            "PE_Ratio": pe,
            "ROE": roe,
        }

    if valid_pe_count == 0:
        print("  ⚠️ WARNING: ไม่พบข้อมูล PE ที่มีค่า > 0 จาก daily_prices.csv เลย — PE_Ratio ทุกตัวจะเป็น null")

    return fund_map


def enrich_market_stage(records: list[dict], fund_map: dict[str, dict]) -> list[dict]:
    """แนบ PE_Ratio และ ROE ต่อท้ายแต่ละ record ของ market_stage พร้อมตรวจสอบ inf/nan"""
    enriched = []
    for r in records:
        ticker = r.get("Ticker")
        fund = fund_map.get(ticker, {})
        new_rec = dict(r)

        pe = fund.get("PE_Ratio")
        if pe is not None:
            try:
                pe_float = float(pe)
                if pe_float <= 0 or not math.isfinite(pe_float):
                    pe = None
            except (ValueError, TypeError):
                pe = None

        roe = fund.get("ROE")
        if roe is not None:
            try:
                roe_float = float(roe)
                if not math.isfinite(roe_float):
                    roe = None
            except (ValueError, TypeError):
                roe = None

        new_rec["PE_Ratio"] = pe
        new_rec["ROE"] = roe
        enriched.append(new_rec)
    return enriched


def build_combined(individual: dict[str, list[dict]], growth_map: dict[str, dict]) -> list[dict]:
    """
    รวมผลทุก scan เป็นตารางเดียวต่อ 1 ticker (สำหรับหน้า Scanner 'ทั้งหมด')
    universe = หุ้นที่ผ่านอย่างน้อย 1 scan (sepa / kell / wyckoff_stage / breakout)
    """
    sepa_set = {r["Ticker"] for r in individual["sepa"]}
    kell_set = {r["Ticker"] for r in individual["oliver_kell"]}
    breakout_set = {r["Ticker"] for r in individual["breakout"]}
    lekkung_set = {r["Ticker"] for r in individual.get("lekkung", [])}
    oneil_set = {r["Ticker"] for r in individual.get("oneil", [])}
    weinstein_set = {r["Ticker"] for r in individual.get("weinstein", [])}

    stage_map = {r["Ticker"]: r["Stage"] for r in individual["market_stage"]}
    price_map_stage = {r["Ticker"]: r["Price"] for r in individual["market_stage"]}
    price_map_sepa = {r["Ticker"]: r["Price"] for r in individual["sepa"]}
    price_map_kell = {r["Ticker"]: r["Price"] for r in individual["oliver_kell"]}
    price_map_breakout = {r["Ticker"]: r["Price"] for r in individual["breakout"]}
    price_map_lekkung = {r["Ticker"]: r["Price"] for r in individual.get("lekkung", []) if "Price" in r}
    price_map_oneil = {r["Ticker"]: r["Price"] for r in individual.get("oneil", []) if "Price" in r}
    rs_map = {r["Ticker"]: r["RS_Rating"] for r in individual["rs_ranking"]}
    price_map_weinstein = {r["Ticker"]: r["Price"] for r in individual.get("weinstein", []) if "Price" in r}

    universe = sepa_set | kell_set | breakout_set | lekkung_set | oneil_set | weinstein_set | set(stage_map.keys())

    combined = []
    for ticker in sorted(universe):
        stage = stage_map.get(ticker)
        # pine_stages ใช้ชื่อ Bull / S.Bull แทน "Stage 2" แบบ Weinstein/Wyckoff
        is_uptrend = stage in ("Bull", "S.Bull")

        price = (
            price_map_stage.get(ticker)
            or price_map_sepa.get(ticker)
            or price_map_kell.get(ticker)
            or price_map_breakout.get(ticker)
            or price_map_lekkung.get(ticker)
            or price_map_oneil.get(ticker)
            or price_map_weinstein.get(ticker)
            or 0.0
        )

        flags = [ticker in sepa_set, ticker in kell_set, ticker in breakout_set, is_uptrend, ticker in weinstein_set]
        combo_score = sum(flags)

        combined.append({
            "ticker": ticker,
            "price": price,
            "sepa": ticker in sepa_set,
            "kell": ticker in kell_set,
            "breakout": ticker in breakout_set,
            "lekkung": ticker in lekkung_set,
            "oneil": ticker in oneil_set,
            "weinstein": ticker in weinstein_set,
            "stage": stage,
            "rs_score": rs_map.get(ticker),
            "combo_score": combo_score,
            "growth_yoy": growth_map.get(ticker, {}).get("growth_yoy"),
            "growth_qoq": growth_map.get(ticker, {}).get("growth_qoq"),
        })

    # เรียงตาม combo_score มาก -> น้อย แล้วตาม rs_score มาก -> น้อย
    combined.sort(key=lambda r: (-r["combo_score"], -(r["rs_score"] or 0)))
    return combined

def build_nvdr(output_dir: Path):
    nvdr_path = Path(__file__).resolve().parents[1] / 'nvdr_history.csv'
    if not nvdr_path.exists():
        print("  ⚠️ ไม่พบไฟล์ nvdr_history.csv (ข้ามการสร้าง nvdr.json)")
        return
        
    df = pd.read_csv(nvdr_path)
    if df.empty:
        return
        
    # หาวันที่ล่าสุด
    latest_date = df['Date'].max()
    df_today = df[df['Date'] == latest_date].copy()
    
    # 5-day aggregate (all rows in the file up to 30 days are there, we just take the last 5 unique dates)
    unique_dates = sorted(df['Date'].unique(), reverse=True)
    dates_5d = unique_dates[:5]
    df_5d = df[df['Date'].isin(dates_5d)].copy()
    
    agg_5d = df_5d.groupby('Symbol').agg({
        'Buy': 'sum',
        'Sell': 'sum',
        'Total': 'sum',
        'Net': 'sum'
    }).reset_index()
    
    out = {
        "latest_date": latest_date,
        "today": json.loads(df_today.to_json(orient="records")),
        "5d": json.loads(agg_5d.to_json(orient="records"))
    }
    
    out_file = output_dir / "nvdr.json"
    out_file.write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(f"  ✓ nvdr.json: {len(df_today)} ticker (today), {len(agg_5d)} ticker (5d)")



def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    print("กำลังแปลง CSV เป็น JSON...\n")

    # Single timestamp for every file this run produces. Individual scan
    # files (lekkung.json etc.) stay bare arrays for backward compatibility -
    # their own freshness is tracked in the sibling generated_at.json
    # manifest below instead of a wrapper object, so each scan page can show
    # ITS OWN data's actual generation time instead of borrowing
    # combined.json's (which used to silently drift out of sync whenever a
    # partial/manual copy updated one but not the other - see the
    # 2026-07-14/15 "header says yesterday, table says today" incident).
    generated_at = datetime.now(timezone(timedelta(hours=7))).strftime("%Y-%m-%dT%H:%M:%S+07:00")

    individual = build_individual_jsons()

    # แนบข้อมูล PE_Ratio และ ROE ลงใน market_stage
    fund_map = build_fundamentals_map()
    if "market_stage" in individual and individual["market_stage"]:
        individual["market_stage"] = enrich_market_stage(individual["market_stage"], fund_map)

    for key, records in individual.items():
        out_path = OUTPUT_DIR / f"{key}.json"
        out_path.write_text(json.dumps(records, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")

    generated_at_manifest = {key: generated_at for key in individual.keys()}
    (OUTPUT_DIR / "generated_at.json").write_text(
        json.dumps(generated_at_manifest, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    print(f"  ✓ generated_at.json: {len(generated_at_manifest)} scan keys stamped {generated_at}")

    print("\nกำลังรวมข้อมูลทุก scan เข้าตารางเดียว (combined.json)...")
    growth_map = build_growth_map()
    combined = build_combined(individual, growth_map)
    combined_path = OUTPUT_DIR / "combined.json"
    combined_out = {
        "generated_at": generated_at,
        "data": combined,
    }
    combined_path.write_text(json.dumps(combined_out, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(f"  ✓ combined.json: {len(combined)} ticker (ผ่านอย่างน้อย 1 scan)")
    
    print("\nกำลังประมวลผล NVDR...")
    build_nvdr(OUTPUT_DIR)

    print(f"\n✅ เสร็จแล้ว ไฟล์ JSON ทั้งหมดอยู่ที่: {OUTPUT_DIR.resolve()}")
    print("ขั้นต่อไป: copy ไฟล์ทั้งหมดในโฟลเดอร์ output/ ไปวางที่ dashboard/data/scans/")


if __name__ == "__main__":
    main()
