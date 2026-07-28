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

# Paths for sector_map.json and universe_ignore.json (relative to AI Agent root)
ROOT_DIR = Path(__file__).resolve().parents[4]
SECTOR_MAP_PATH = ROOT_DIR / "Claude" / "dashboard" / "stockdesk" / "data" / "scans" / "sector_map.json"
IGNORE_LIST_PATH = ROOT_DIR / "Claude" / "dashboard" / "stockdesk" / "data" / "scans" / "universe_ignore.json"

# TradingView's live scanner is re-queried every run, so its classification
# can blip for a single run (temporary trading suspension, reclassification
# lag, a pagination hiccup) and silently drop a real, still-listed stock out
# of the whole universe. A ticker now has to be missing for MISS_THRESHOLD
# consecutive runs before it's actually removed, instead of being dropped
# the instant TradingView doesn't return it once.
TRACKER_FILE = DATA_DIR / "symbol_tracker.json"
MISS_THRESHOLD = 3


def load_ignored_tickers(ignore_path: Path) -> set:
    """Load previously rejected tickers from universe_ignore.json safely."""
    if not ignore_path.exists():
        return set()
    try:
        with open(ignore_path, encoding="utf-8") as f:
            data = json.load(f)
        return set(t.strip().upper() for t in data.get("rejected", []))
    except Exception as e:
        print(f"⚠️ [1_get_symbols] Warning: Failed to load ignore list ({e})")
        return set()


def load_sector_map_candidates(sector_map_path: Path, ignored_tickers: set) -> set:
    """Extract clean tradeable stock candidates from sector_map.json safely."""
    if not sector_map_path.exists():
        print(f"⚠️ [1_get_symbols] WARNING: sector_map union skipped (file not found at {sector_map_path}), using TradingView only")
        return set()
    try:
        with open(sector_map_path, encoding="utf-8") as f:
            data = json.load(f)

        t2s = data.get("ticker_to_sector", {})
        candidates = set()
        for ticker, info in t2s.items():
            t_upper = ticker.strip().upper()
            subsector = str(info.get("subsector", "")).lower()

            # Rule 1: Exclude if subsector contains "reit" or "fund"
            if "reit" in subsector or "fund" in subsector:
                continue

            # Rule 2: Exclude if ticker ends with Infra Fund suffixes (IF, IFF, GIF) or contains REIT
            if t_upper.endswith(("IF", "IFF", "GIF")) or "REIT" in t_upper:
                continue

            # Rule 3: Exclude if in universe_ignore.json
            if t_upper in ignored_tickers:
                continue

            # Rule 4: Python consistency filter
            if (
                len(t_upper) <= 7
                and not t_upper.endswith(("-R", ".R", "-F", ".F", "-W", "-P"))
            ):
                candidates.add(t_upper)

        return candidates
    except Exception as e:
        print(f"⚠️ [1_get_symbols] WARNING: sector_map union skipped ({e}), using TradingView only")
        return set()


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
        tv_count = len(fresh_symbols)

        # 🤝 Union with sector_map.json (fallback safe)
        ignored_tickers = load_ignored_tickers(IGNORE_LIST_PATH)
        sector_map_candidates = load_sector_map_candidates(SECTOR_MAP_PATH, ignored_tickers)

        added_from_sector_map = sorted(sector_map_candidates - fresh_symbols)
        union_count = len(added_from_sector_map)

        effective_fresh_symbols = fresh_symbols | sector_map_candidates

        print(f"📊 TradingView returned: {tv_count} tickers")
        print(f"🔗 sector_map union added: {union_count} new tickers")
        if added_from_sector_map:
            print(f"   └─ Added tickers: {', '.join(added_from_sector_map)}")

        tracker = {}
        if TRACKER_FILE.exists():
            try:
                tracker = json.loads(TRACKER_FILE.read_text(encoding="utf-8"))
            except Exception:
                tracker = {}

        final_symbols = set(effective_fresh_symbols)
        new_tracker = {}
        grace, dropped = [], []

        for ticker, miss_count in tracker.items():
            if ticker in effective_fresh_symbols:
                continue  # seen again this run - miss streak resets (no entry needed)
            new_miss = miss_count + 1
            if new_miss < MISS_THRESHOLD:
                final_symbols.add(ticker)
                new_tracker[ticker] = new_miss
                grace.append(ticker)
            else:
                dropped.append(ticker)

        symbols = sorted(final_symbols)
        total_count = len(symbols)
        print(f"📈 Total active_symbols: {total_count}")

        # 🛡️ Drop Guard: Abort if count drops abnormally (< 800)
        if total_count < 800:
            print(f"❌ [1_get_symbols] ERROR: Aborting save! active_symbols count ({total_count}) < 800 (Drop Guard Triggered)")
            return

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

