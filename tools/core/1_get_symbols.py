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
from config import SYMBOLS_FILE


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
        symbols = [
            row["d"][0]
            for row in res["data"]
            if len(row["d"][0]) <= 7
            and not row["d"][0].endswith(("-R", ".R", "-F", ".F", "-W", "-P"))
            and "REIT" not in row["d"][0]
        ]

        df = pd.DataFrame(symbols, columns=["Ticker"])

        # 🎯 เซฟไฟล์โดยใช้ Path จาก config
        df.to_csv(SYMBOLS_FILE, index=False)
        print(
            f"✅ ดึงรายชื่อสำเร็จ: {len(symbols)} ตัว (เซฟไว้ที่ {SYMBOLS_FILE.name})"
        )
    except Exception as e:
        print(f"❌ ดึงข้อมูลล้มเหลว: {e}")


if __name__ == "__main__":
    fetch_active_symbols()
