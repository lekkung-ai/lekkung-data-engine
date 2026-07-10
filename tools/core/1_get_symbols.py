import json
import sys
from pathlib import Path

# Force UTF-8 encoding for standard output/error on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import pandas as pd
import requests

# 🔌 เชื่อมต่อ config.py ที่อยู่ข้างนอก (ถอย 2 ก้าว: core -> tools -> 1_data_engine)
sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import SYMBOLS_FILE, DATA_DIR

# TradingView's live scanner is re-queried every run, so its classification
# can blip for a single run (temporary trading suspension, reclassification
# lag, a pagination hiccup) and silently drop a real, still-listed stock out
# of the whole universe. A ticker now has to be missing for MISS_THRESHOLD
# consecutive runs before it's actually removed, instead of being dropped
# the instant TradingView doesn't return it once.
TRACKER_FILE = DATA_DIR / "symbol_tracker.json"
MISS_THRESHOLD = 3


def fetch_active_symbols():
    print("🔍 กำลังดึงรายชื่อหุ้นจาก TradingView...")
    url = "https://scanner.tradingview.com/thailand/scan"
    payload = {
        "filter": [
            {"left": "exchange", "operation": "equal", "right": "SET"},
            {"left": "type", "operation": "equal", "right": "stock"},
        ],
        "columns": ["name"],
        "sort": {"sortBy": "name", "sortOrder": "asc"},
    }

    try:
        res = requests.post(url, json=payload).json()
        # ตัดขยะ: กองทุน, ใบสำคัญแสดงสิทธิ, หุ้นต่างประเทศ
        fresh_symbols = {
            row["d"][0]
            for row in res["data"]
            if len(row["d"][0]) <= 7
            and not row["d"][0].endswith(("-R", ".R", "-F", ".F", "-W", "-P"))
            and "REIT" not in row["d"][0]
        }

        tracker = {}
        if TRACKER_FILE.exists():
            try:
                tracker = json.loads(TRACKER_FILE.read_text(encoding="utf-8"))
            except Exception:
                tracker = {}

        final_symbols = set(fresh_symbols)
        new_tracker = {}
        grace, dropped = [], []

        for ticker, miss_count in tracker.items():
            if ticker in fresh_symbols:
                continue  # seen again this run - miss streak resets (no entry needed)
            new_miss = miss_count + 1
            if new_miss < MISS_THRESHOLD:
                final_symbols.add(ticker)
                new_tracker[ticker] = new_miss
                grace.append(ticker)
            else:
                dropped.append(ticker)

        symbols = sorted(final_symbols)
        df = pd.DataFrame(symbols, columns=["Ticker"])
        df.to_csv(SYMBOLS_FILE, index=False)

        TRACKER_FILE.parent.mkdir(parents=True, exist_ok=True)
        TRACKER_FILE.write_text(
            json.dumps(new_tracker, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        if grace:
            print(f"⏳ หายจาก TradingView ชั่วคราว (ให้ grace period): {', '.join(sorted(grace))}")
        if dropped:
            print(f"⚠️  ตัดออกจาก universe แล้ว (หายติดต่อกัน {MISS_THRESHOLD} รอบ): {', '.join(sorted(dropped))}")
        print(
            f"✅ ดึงรายชื่อสำเร็จ: {len(symbols)} ตัว (เซฟไว้ที่ {SYMBOLS_FILE.name})"
        )
    except Exception as e:
        print(f"❌ ดึงข้อมูลล้มเหลว: {e}")


if __name__ == "__main__":
    fetch_active_symbols()
