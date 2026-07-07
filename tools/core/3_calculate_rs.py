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

# IBD-proxy RS: 3/6/9/12-month returns weighted 0.4/0.2/0.2/0.2 (latest
# quarter counts double). Stocks with less than the full 252 days still get
# scored on whichever of these periods actually fit, as long as at least the
# 126-day period is available (below that, exclude entirely).
PERIODS = [63, 126, 189, 252]
WEIGHTS = [0.4, 0.2, 0.2, 0.2]
MIN_DAYS = 126


def calculate_rs():
    if not HISTORY_DIR.exists():
        print("❌ ไม่พบโฟลเดอร์ history กรุณารัน Step 2 ก่อนครับ")
        return

    print("🧮 กำลังคำนวณ RS Score จากไฟล์ Local...")
    rs_data = []

    # วนลูปอ่านไฟล์ CSV ในโฟลเดอร์ HISTORY_DIR
    for file_path in HISTORY_DIR.glob("*.csv"):
        ticker = file_path.stem

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                first_line = f.readline()
            df = pd.read_csv(file_path, header=[0, 1] if "Price" in first_line else 0)
            if isinstance(df.columns, pd.MultiIndex):
                close_col = [col for col in df.columns if "Close" in col[0]][0]
                series = pd.to_numeric(df[close_col], errors="coerce").dropna()
            else:
                series = pd.to_numeric(df["Close"], errors="coerce").dropna()

            n = len(series)
            if n < MIN_DAYS:
                continue

            current_close = float(series.iloc[-1])

            # Use whichever lookback periods actually fit in the available
            # history, renormalizing the remaining weights so they still sum
            # to 1.0 — keeps RS_raw a fair weighted average regardless of how
            # many periods a newer listing can supply.
            weighted_sum = 0.0
            weight_total = 0.0
            for period, weight in zip(PERIODS, WEIGHTS):
                if n < period:
                    continue
                c_ago = float(series.iloc[-period])
                r = (current_close - c_ago) / c_ago
                weighted_sum += r * weight
                weight_total += weight

            if weight_total == 0:
                continue

            rs_raw = weighted_sum / weight_total
            rs_data.append({"Ticker": ticker, "RS_Raw": rs_raw})

        except Exception:
            continue

    if not rs_data:
        print("❌ คำนวณไม่ได้ ข้อมูลอาจจะไม่เพียงพอ")
        return

    df_rs = pd.DataFrame(rs_data)
    # IBD-style 1-99 percentile rank. rank(pct=True) tops out at exactly 1.0
    # for the single highest-ranked stock, which would land on 100 — clip it
    # back down to 99 so the whole scale stays 1-99.
    raw_rating = df_rs["RS_Raw"].rank(pct=True) * 99 + 1
    df_rs["RS_Rating"] = raw_rating.round().clip(upper=99).astype(int)
    df_rs = df_rs.sort_values(by="RS_Rating", ascending=False)

    # 🎯 เซฟไฟล์ RS Ranking
    df_rs[["Ticker", "RS_Rating"]].to_csv(RS_FILE, index=False, encoding="utf-8")
    print(f"✅ คำนวณ RS Score เสร็จสิ้น (เซฟลง {RS_FILE.name})")


if __name__ == "__main__":
    calculate_rs()
