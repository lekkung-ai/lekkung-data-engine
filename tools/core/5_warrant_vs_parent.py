"""
5_warrant_vs_parent.py
======================
เปรียบเทียบราคา Warrant vs หุ้นแม่จาก daily_prices.csv

สูตร:
  cost_per_parent = (ราคา Warrant + ราคาใช้สิทธิ) / ratio
  diff_thb        = cost_per_parent - ราคาหุ้นแม่
  diff_pct        = (diff_thb / ราคาหุ้นแม่) * 100

Output:
  - warrant_vs_parent.csv  (ทุกตัว)
  - แสดง 3 ตารางใน terminal
"""

import sys
from datetime import datetime
from pathlib import Path

# Force UTF-8 encoding for standard output/error on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import RESULTS_DIR

HERE = Path(__file__).resolve().parent
WARRANT_CSV = RESULTS_DIR / "warrant_analysis.csv"
DAILY_PRICES_CSV = HERE / "data" / "daily_prices.csv"
OUTPUT_CSV = RESULTS_DIR / "warrant_vs_parent.csv"

COLS = [
    "Warrant",
    "Underlying",
    "Parent฿",
    "W฿",
    "Ex฿",
    "Ratio",
    "Cost/Parent฿",
    "Diff฿",
    "Diff%",
    "Status",
]


def run():
    print("=" * 60)
    print("  Warrant vs Parent Analysis")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 1. โหลดไฟล์
    if not WARRANT_CSV.exists():
        print(f"[ERROR] ไม่พบ {WARRANT_CSV} — รัน 4_update_warrants.py ก่อน")
        return
    if not DAILY_PRICES_CSV.exists():
        print(f"[ERROR] ไม่พบ {DAILY_PRICES_CSV}")
        return

    df_w = pd.read_csv(WARRANT_CSV)
    df_p = pd.read_csv(DAILY_PRICES_CSV)

    print(f"\n[1] โหลด warrant_analysis.csv  : {len(df_w)} แถว")
    print(f"    โหลด daily_prices.csv       : {len(df_p)} แถว")

    # 2. ดึงราคาล่าสุดของแต่ละ Ticker
    df_p["Date"] = pd.to_datetime(df_p["Date"])
    df_latest = (
        df_p.sort_values("Date", ascending=False)
        .groupby("Ticker")
        .first()
        .reset_index()
    )[["Ticker", "Date", "Close"]]
    df_latest.columns = ["underlying", "price_date", "parent_close"]

    price_date = df_latest["price_date"].max().strftime("%Y-%m-%d")
    print(f"\n[2] ราคาล่าสุดใน daily_prices  : {price_date}")

    # 3. Join
    df = df_w.merge(df_latest, on="underlying", how="left")

    # 4. คำนวณ
    rows = []
    for _, r in df.iterrows():
        w_price = r.get("warrant_price")
        p_close = r.get("parent_close")
        ex_price = r.get("exercise_price")
        ratio = r.get("ratio") if pd.notna(r.get("ratio")) else 1.0

        cost_per_parent = diff_thb = diff_pct = itm_otm = None

        if (
            pd.notna(w_price)
            and pd.notna(p_close)
            and pd.notna(ex_price)
            and ex_price > 0
            and ratio > 0
        ):
            cost_per_parent = round((w_price + ex_price) / ratio, 4)
            diff_thb = round(cost_per_parent - p_close, 4)
            diff_pct = round((diff_thb / p_close) * 100, 2)
            itm_otm = "ITM" if p_close > ex_price else "OTM"

        rows.append(
            {
                "Warrant": r["warrant"],
                "Underlying": r["underlying"],
                "Parent฿": round(p_close, 2) if pd.notna(p_close) else None,
                "Price_Date": str(r.get("price_date", ""))[:10],
                "W฿": w_price,
                "Ex฿": ex_price,
                "Ratio": ratio,
                "Cost/Parent฿": cost_per_parent,
                "Diff฿": diff_thb,
                "Diff%": diff_pct,
                "Status": itm_otm or "N/A",
            }
        )

    df_result = pd.DataFrame(rows)

    # 5. บันทึก CSV
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df_result.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    print(f"\n[3] บันทึก → {OUTPUT_CSV}  ({len(df_result)} แถว)")

    # 6. แบ่ง 3 กลุ่ม
    df_itm_cheap = df_result[
        (df_result["Status"] == "ITM") & (df_result["Diff%"] < 0)
    ].sort_values("Diff%")
    df_itm_exp = df_result[
        (df_result["Status"] == "ITM") & (df_result["Diff%"] >= 0)
    ].sort_values("Diff%")
    df_otm = df_result[df_result["Status"] == "OTM"].sort_values("Diff%")
    df_na = df_result[df_result["Status"] == "N/A"]

    sep = "=" * 105

    # ── ตาราง 1 ──
    print(f"\n{sep}")
    print(f"  ตาราง 1 — ITM + ถูกกว่าหุ้นแม่  ({len(df_itm_cheap)} ตัว)")
    print(f"  ซื้อ Warrant แล้วใช้สิทธิ ถูกกว่าซื้อหุ้นตรงๆ  ← น่าสนใจที่สุด")
    print(sep)
    if not df_itm_cheap.empty:
        print(df_itm_cheap[COLS].to_string(index=False))
    else:
        print("  ไม่มี Warrant ในกลุ่มนี้")

    # ── ตาราง 2 ──
    print(f"\n{sep}")
    print(f"  ตาราง 2 — ITM + แพงกว่าหุ้นแม่  ({len(df_itm_exp)} ตัว)")
    print(f"  มีมูลค่าในตัวแต่ premium ยังแพงกว่าซื้อหุ้นตรงๆ")
    print(sep)
    if not df_itm_exp.empty:
        print(df_itm_exp[COLS].to_string(index=False))
    else:
        print("  ไม่มี Warrant ในกลุ่มนี้")

    # ── ตาราง 3 ──
    print(f"\n{sep}")
    print(f"  ตาราง 3 — OTM  ({len(df_otm)} ตัว)")
    print(f"  หุ้นแม่ยังไม่ถึง Exercise Price")
    print(sep)
    if not df_otm.empty:
        print(df_otm[COLS].to_string(index=False))
    else:
        print("  ไม่มี Warrant ในกลุ่มนี้")

    # ── สรุป ──
    print(f"\n{sep}")
    print(
        f"  สรุป  |  ITM ถูกกว่า {len(df_itm_cheap)} ตัว  |  ITM แพงกว่า {len(df_itm_exp)} ตัว  |  OTM {len(df_otm)} ตัว  |  N/A {len(df_na)} ตัว"
    )
    print(sep)


if __name__ == "__main__":
    run()
