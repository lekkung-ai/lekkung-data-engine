import json
import math
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

# sector_map.json lives in stockdesk, so it's looked up the same way
# scan_lekkung_growth.py finds earnings_feed.json - a couple of candidate
# locations for the two contexts this script runs in:
#   - CI (daily-scan.yml): an early sparse-checkout puts it at
#     data_engine/stockdesk_sector_map_check/ - the main stockdesk checkout
#     only happens after run_all.py has finished
#   - local dev: stockdesk is a sibling repo checkout on the same machine
_DATA_ENGINE_ROOT = Path(__file__).resolve().parents[2]
SECTOR_MAP_CANDIDATES = [
    _DATA_ENGINE_ROOT / "stockdesk_sector_map_check" / "data" / "scans" / "sector_map.json",
    _DATA_ENGINE_ROOT.parent.parent / "Claude" / "dashboard" / "stockdesk" / "data" / "scans" / "sector_map.json",
]

# Output paths
OUTPUT_RESULTS_PATH = RESULTS_DIR / "output" / "sector_rs.json"
OUTPUT_STOCKDESK_PATH = ROOT_DIR / "Claude" / "dashboard" / "stockdesk" / "data" / "scans" / "sector_rs.json"

# Drop Guard: a market losing more than this fraction of its sectors versus the
# baseline (previous sector_rs.json, else the sectors defined in sector_map.json)
# means a broken input, not a real market change -> abort, keep the old file.
MAX_SECTOR_DROP_PCT = 0.30


def resolve_sector_map_path() -> Path | None:
    for path in SECTOR_MAP_CANDIDATES:
        if path.exists():
            return path
    return None


def load_baseline_sector_counts(t2s: dict) -> tuple[dict[str, int], str]:
    """Sectors per market to compare the new result against: the previous
    sector_rs.json if one is readable, otherwise what sector_map.json defines
    (fresh CI runner has no previous output)."""
    for path in (OUTPUT_RESULTS_PATH, OUTPUT_STOCKDESK_PATH):
        if not path.exists():
            continue
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
            prev_sectors = prev.get("sectors") or {}
            if prev_sectors:
                return {m: len(s) for m, s in prev_sectors.items()}, str(path)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"⚠️ Could not read previous {path} for Drop Guard baseline: {e}")

    expected: dict[str, set] = {}
    for info in t2s.values():
        expected.setdefault(info.get("market", "UNKNOWN"), set()).add(info.get("sector", "UNKNOWN"))
    return {m: len(s) for m, s in expected.items()}, "sector_map.json"

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

    # 2. Read sector_map.json - no silent fallback: without it every ticker is
    #    unmapped, so fail loudly with every path that was tried.
    SECTOR_MAP_PATH = resolve_sector_map_path()
    if SECTOR_MAP_PATH is None:
        tried = "\n".join(f"  - {c}" for c in SECTOR_MAP_CANDIDATES)
        logger.error(f"❌ ERROR: sector_map.json not found. Tried:\n{tried}")
        sys.exit(1)
    logger.info(f"Using sector_map.json at {SECTOR_MAP_PATH}")

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
    non_finite = []

    for item in rs_data:
        ticker = item.get("Ticker")
        rs_rating = item.get("RS_Rating")

        if not ticker or rs_rating is None:
            continue

        if not isinstance(rs_rating, (int, float)) or not math.isfinite(rs_rating):
            non_finite.append(f"{ticker}={rs_rating!r}")
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

    if non_finite:
        # Abort rather than skip: skipping would quietly change each sector's count.
        logger.error(f"❌ ERROR: {len(non_finite)} non-finite RS_Rating value(s) in {RS_JSON_PATH.name}: "
                     f"{', '.join(non_finite[:20])}. Aborting file write.")
        sys.exit(1)

    if unmapped_count > 0:
        logger.info(f"Unmapped tickers count: {unmapped_count}")

    # 4. Construct output JSON structure
    sectors_output = {}

    for (market, sector), ratings in grouped_data.items():
        if market not in sectors_output:
            sectors_output[market] = {}

        median_val = float(np.median(ratings))
        if not math.isfinite(median_val):
            logger.error(f"❌ ERROR: non-finite median for {market}/{sector}: {median_val}. Aborting file write.")
            sys.exit(1)
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

    baseline, baseline_src = load_baseline_sector_counts(t2s)
    for market, base_count in baseline.items():
        new_count = len(sectors_output.get(market, {}))
        if base_count > 0 and new_count < base_count * (1 - MAX_SECTOR_DROP_PCT):
            logger.error(f"❌ Drop Guard triggered: {market} sectors {base_count} -> {new_count} "
                         f"(> {MAX_SECTOR_DROP_PCT:.0%} drop vs {baseline_src}). Aborting file write, keeping previous file.")
            sys.exit(1)

    output_payload = {
        "generated_at": datetime.now(BANGKOK_TZ).isoformat(),
        "method": "median_stock_rs",
        "sectors": sectors_output
    }

    # Serialize once up front: allow_nan=False raises here, before any existing file is truncated.
    output_text = json.dumps(output_payload, ensure_ascii=False, indent=2, allow_nan=False)

    # 6. Write output files
    OUTPUT_RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_RESULTS_PATH.write_text(output_text, encoding="utf-8")
    logger.info(f"✅ Saved sector_rs.json to {OUTPUT_RESULTS_PATH}")

    if OUTPUT_STOCKDESK_PATH.parent.exists():
        OUTPUT_STOCKDESK_PATH.write_text(output_text, encoding="utf-8")
        logger.info(f"✅ Saved sector_rs.json to {OUTPUT_STOCKDESK_PATH}")
    else:
        logger.warning(f"⚠️ StockDesk path {OUTPUT_STOCKDESK_PATH.parent} does not exist. Skipped copy.")

if __name__ == "__main__":
    main()
