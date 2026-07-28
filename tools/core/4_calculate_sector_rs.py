import json
import sys
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
import numpy as np

# Force UTF-8 encoding for standard output/error on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Connect config
sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import RESULTS_DIR

BANGKOK_TZ = timezone(timedelta(hours=7))

# File paths
RS_JSON_PATH = RESULTS_DIR / "output" / "rs_ranking.json"
ROOT_DIR = Path(__file__).resolve().parents[4]
SECTOR_MAP_PATH = ROOT_DIR / "Claude" / "dashboard" / "stockdesk" / "data" / "scans" / "sector_map.json"

# Output paths
OUTPUT_RESULTS_PATH = RESULTS_DIR / "output" / "sector_rs.json"
OUTPUT_STOCKDESK_PATH = ROOT_DIR / "Claude" / "dashboard" / "stockdesk" / "data" / "scans" / "sector_rs.json"

def main():
    logger.info("Calculating Sector RS (Median Stock RS method)...")

    # 1. Read rs_ranking.json
    if not RS_JSON_PATH.exists():
        logger.error(f"❌ Error: File not found at {RS_JSON_PATH}")
        sys.exit(1)

    try:
        with open(RS_JSON_PATH, "r", encoding="utf-8") as f:
            rs_data = json.load(f)
        if not rs_data or not isinstance(rs_data, list):
            logger.error(f"❌ Error: {RS_JSON_PATH} is empty or invalid structure.")
            sys.exit(1)
    except Exception as e:
        logger.error(f"❌ Error: Failed to read {RS_JSON_PATH}: {e}")
        sys.exit(1)

    logger.info(f"Loaded {len(rs_data)} stock RS records from {RS_JSON_PATH.name}")

    # 2. Read sector_map.json
    if not SECTOR_MAP_PATH.exists():
        logger.error(f"❌ Error: File not found at {SECTOR_MAP_PATH}")
        sys.exit(1)

    try:
        with open(SECTOR_MAP_PATH, "r", encoding="utf-8") as f:
            sector_map = json.load(f)
        t2s = sector_map.get("ticker_to_sector", {})
        if not t2s:
            logger.error(f"❌ Error: ticker_to_sector missing or empty in {SECTOR_MAP_PATH}")
            sys.exit(1)
    except Exception as e:
        logger.error(f"❌ Error: Failed to read {SECTOR_MAP_PATH}: {e}")
        sys.exit(1)

    # 3. Group and aggregate by (market, sector)
    grouped_data = {}  # key: (market, sector) -> list of RS_Rating
    unmapped_count = 0

    for item in rs_data:
        ticker = item.get("Ticker")
        rs_rating = item.get("RS_Rating")

        if not ticker or rs_rating is None:
            continue

        info = t2s.get(ticker)
        if not info:
            unmapped_count += 1
            continue

        market = info.get("market", "UNKNOWN")
        sector = info.get("sector", "UNKNOWN")
        key = (market, sector)

        if key not in grouped_data:
            grouped_data[key] = []
        grouped_data[key].append(rs_rating)

    if unmapped_count > 0:
        logger.info(f"Unmapped tickers count: {unmapped_count}")

    # 4. Construct output JSON structure
    sectors_output = {}

    for (market, sector), ratings in grouped_data.items():
        if market not in sectors_output:
            sectors_output[market] = {}

        median_val = float(np.median(ratings))
        rs_score = int(round(median_val))
        count_val = len(ratings)

        sectors_output[market][sector] = {
            "rsScore": rs_score,
            "count": count_val
        }

    # Count total sector entries across markets
    total_sector_entries = sum(len(sec_dict) for sec_dict in sectors_output.values())

    # 5. Drop Guard: Check if calculated results are valid before writing
    if total_sector_entries < 8:
        logger.error(f"❌ Drop Guard triggered: Only {total_sector_entries} sector entries calculated (expected >= 8). Aborting file write.")
        sys.exit(1)

    output_payload = {
        "generated_at": datetime.now(BANGKOK_TZ).isoformat(),
        "method": "median_stock_rs",
        "sectors": sectors_output
    }

    # 6. Write output files
    OUTPUT_RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, ensure_ascii=False, indent=2)
    logger.info(f"✅ Saved sector_rs.json to {OUTPUT_RESULTS_PATH}")

    if OUTPUT_STOCKDESK_PATH.parent.exists():
        with open(OUTPUT_STOCKDESK_PATH, "w", encoding="utf-8") as f:
            json.dump(output_payload, f, ensure_ascii=False, indent=2)
        logger.info(f"✅ Saved sector_rs.json to {OUTPUT_STOCKDESK_PATH}")
    else:
        logger.warning(f"⚠️ StockDesk path {OUTPUT_STOCKDESK_PATH.parent} does not exist. Skipped copy.")

if __name__ == "__main__":
    main()
