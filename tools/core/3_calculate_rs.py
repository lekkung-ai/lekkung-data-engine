import sys
from pathlib import Path

# Force UTF-8 encoding for standard output/error on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import pandas as pd

# 🔌 เชื่อมต่อ config.py
sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import HISTORY_DIR, RS_FILE
from tools.core.rs_score import load_close_series, rs_raw, rs_ratings  # สูตร RS ที่เดียว (scan_rs7 ใช้ร่วม)
from tools.scanner.utils import iter_stock_files, load_fund_tickers


def calculate_rs():
    if not HISTORY_DIR.exists():
        print("❌ ไม่พบโฟลเดอร์ history กรุณารัน Step 2 ก่อนครับ")
        return

    print("🧮 กำลังคำนวณ RS Score จากไฟล์ Local...")
    rs_data = []

    # วนลูปอ่านไฟล์ CSV ในโฟลเดอร์ HISTORY_DIR
    for file_path in iter_stock_files(HISTORY_DIR):
        ticker = file_path.stem

        try:
            raw = rs_raw(load_close_series(file_path))
            if raw is None:
                continue
            rs_data.append({"Ticker": ticker, "RS_Raw": raw})

        except Exception:
            continue

    if not rs_data:
        print("❌ คำนวณไม่ได้ ข้อมูลอาจจะไม่เพียงพอ")
        return

    df_rs = pd.DataFrame(rs_data)
    # กองทุน / REIT (sector_map) ไม่ร่วมจัดอันดับ: RS_Rating = null แต่ยังอยู่ในไฟล์พร้อม RS_Raw
    # (weinstein_stage_analysis.py ใช้รายชื่อในไฟล์นี้เป็น universe)
    is_fund = df_rs["Ticker"].str.upper().isin(load_fund_tickers())
    df_rs["RS_Rating"] = rs_ratings(df_rs["RS_Raw"], is_fund)
    df_rs = df_rs.sort_values(by="RS_Rating", ascending=False, na_position="last")

    # 🎯 เซฟไฟล์ RS Ranking
    df_rs[["Ticker", "RS_Rating", "RS_Raw"]].to_csv(RS_FILE, index=False, encoding="utf-8")
    print(f"   กองทุน/REIT ไม่จัดอันดับ (RS_Rating = null): {int(is_fund.sum())} ตัว")
    print(f"✅ คำนวณ RS Score เสร็จสิ้น (เซฟลง {RS_FILE.name})")


if __name__ == "__main__":
    calculate_rs()
