"""
check_universe.py - weekly (Monday) universe-drift detector.

active_symbols.csv (RESULTS_DIR/active_symbols.csv) is refreshed EVERY
pipeline run by 1_get_symbols.py from TradingView's live SET-stock scanner
(with a 3-run grace period before a ticker is actually dropped) - it is
already an up-to-date "what's really listed right now" list.
stockdesk's data/scans/sector_map.json, by contrast, is hand-curated and
does NOT auto-update (see stockdesk/scripts/validate_sector_mapping.py's own
docstring) - a real newly-listed ticker shows up in active_symbols.csv
immediately but stays sector "N/A" on the dashboard until a human manually
patches sector_map.json. validate_sector_mapping.py already detects this
drift, but only as a console warning nobody reliably reads.

This script does the same diff but writes it as a structured, actionable
file for the /settings page's pending-approval UI to render - and goes
further by asking TradingView for a name/sector/industry guess for each new
ticker, so a human has something to approve/correct instead of a bare ticker
symbol. It NEVER touches sector_map.json or active_symbols.csv itself -
purely detects and reports; a human approves via /settings, which generates
a copy-paste prompt for a separate Claude Code session to actually apply
the edit (see /settings page for that flow).

Usage:
    python check_universe.py                          # writes real output (only runs on Monday, Bangkok time)
    python check_universe.py --force                   # run regardless of weekday (manual testing)
    python check_universe.py --dry-run                  # print only, write nothing
    python check_universe.py --sector-map <path>         # override the default sibling-repo sector_map.json path
    python check_universe.py --out <dir>                 # write into <dir> instead of the real results/output tree

Output: data/results/output/universe_changes.json (picked up by the
existing "cp -r data/results/output/*.json stockdesk_repo/data/scans/" step
in daily-scan.yml, same as every other pipeline JSON output)
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd
import requests

sys.path.append(str(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
from config import SYMBOLS_FILE, RESULTS_DIR  # noqa: E402

BANGKOK_TZ = timezone(timedelta(hours=7))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Sibling-repo layout assumed elsewhere in this codebase (see stockdesk's
# scripts/calculate_report_card.py DATA_ENGINE_HISTORY_DIR) - both repos
# checked out under a common parent. Overridable via --sector-map for the
# CI job, where stockdesk gets checked out to a differently-named path.
DEFAULT_SECTOR_MAP_PATH = os.path.normpath(
    os.path.join(SCRIPT_DIR, "..", "..", "..", "..", "Claude", "dashboard", "stockdesk", "data", "scans", "sector_map.json")
)

TV_SCAN_URL = "https://scanner.tradingview.com/thailand/scan"


def load_active_symbols() -> set:
    if not os.path.exists(SYMBOLS_FILE):
        print(f"[check-universe] SKIP - {SYMBOLS_FILE} not found (run 1_get_symbols.py first)")
        return set()
    df = pd.read_csv(SYMBOLS_FILE)
    return set(df["Ticker"].astype(str))


def is_plain_stock_ticker(ticker: str) -> bool:
    """Mirrors 1_get_symbols.py's own TradingView-scanner exclusion filter
    (REITs, funds, warrants, foreign-board lines) - active_symbols.csv only
    ever contains this narrower "plain stock" universe by design (see
    fetch_active_symbols() in 1_get_symbols.py), so comparing sector_map.json's
    full ticker set (which does include REITs/funds, e.g. CPNREIT, DIF,
    3BBIF) against it directly would flag every REIT as "delisted" on every
    single run - confirmed against real data before this filter was added.
    Only tickers that would have qualified for active_symbols.csv in the
    first place are meaningful candidates for the delisted-ticker check."""
    return (
        len(ticker) <= 7
        and not ticker.endswith(("-R", ".R", "-F", ".F", "-W", "-P"))
        and "REIT" not in ticker
    )


def load_sector_map_tickers(path: str) -> set:
    if not os.path.exists(path):
        print(f"[check-universe] SKIP - sector_map not found at {path}")
        return set()
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return set(data.get("ticker_to_sector", {}).keys())


def guess_company_info(tickers: list) -> dict:
    """Best-effort name/sector/industry guess from TradingView's scanner for
    specific new tickers - same 'thailand' scan endpoint 1_get_symbols.py
    already uses, just with richer columns and an explicit symbol filter
    instead of a bulk market-wide pull. This is explicitly a GUESS in
    TradingView's own sector/industry taxonomy (not SET's official Thai
    sector/subsector names) - the /settings approve UI lets a human correct
    it before it's ever applied to sector_map.json. Never raises - a failed
    guess just means the pending entry shows blank fields for manual fill-in."""
    if not tickers:
        return {}
    payload = {
        "filter": [
            {"left": "exchange", "operation": "equal", "right": "SET"},
            {"left": "name", "operation": "in_range", "right": tickers},
        ],
        "columns": ["name", "description", "sector", "industry"],
        "range": [0, len(tickers)],
    }
    try:
        res = requests.post(TV_SCAN_URL, json=payload, timeout=15)
        res.raise_for_status()
        rows = res.json().get("data", [])
        out = {}
        for row in rows:
            d = row.get("d", [])
            if len(d) < 4:
                continue
            out[d[0]] = {"company_name": d[1], "guessed_sector": d[2], "guessed_subsector": d[3]}
        return out
    except Exception as e:
        print(f"[check-universe] WARNING: TradingView company-info lookup failed: {e}")
        return {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="print the diff, write nothing")
    parser.add_argument("--force", action="store_true", help="run regardless of today's weekday")
    parser.add_argument("--sector-map", default=DEFAULT_SECTOR_MAP_PATH, help="path to stockdesk's data/scans/sector_map.json")
    parser.add_argument("--out", default=None, help="write into this dir instead of the real results/output tree")
    args = parser.parse_args()

    today = datetime.now(BANGKOK_TZ)
    if today.weekday() != 0 and not args.force:  # 0 = Monday
        print(f"[check-universe] SKIP - today ({today.strftime('%A')}) is not Monday. Use --force to override.")
        return

    universe = load_active_symbols()
    mapped = load_sector_map_tickers(args.sector_map)
    if not universe or not mapped:
        print("[check-universe] Aborting - could not load one or both ticker sets.")
        return

    new_tickers = sorted(universe - mapped)
    mapped_plain_stocks = {t for t in mapped if is_plain_stock_ticker(t)}
    delisted_tickers = sorted(mapped_plain_stocks - universe)

    print(f"[check-universe] Universe (active_symbols.csv): {len(universe)} tickers")
    print(f"[check-universe] Mapped (sector_map.json): {len(mapped)} tickers")
    print(f"[check-universe] New (unmapped): {len(new_tickers)} -> {', '.join(new_tickers) or '(none)'}")
    print(f"[check-universe] Possibly delisted (mapped but not in active universe): {len(delisted_tickers)} -> {', '.join(delisted_tickers) or '(none)'}")

    guesses = guess_company_info(new_tickers)

    output = {
        "generated_at": today.isoformat(),
        "new_tickers": [
            {
                "ticker": t,
                "company_name": guesses.get(t, {}).get("company_name"),
                "guessed_sector": guesses.get(t, {}).get("guessed_sector"),
                "guessed_subsector": guesses.get(t, {}).get("guessed_subsector"),
            }
            for t in new_tickers
        ],
        "delisted_tickers": [{"ticker": t} for t in delisted_tickers],
    }

    if args.dry_run:
        print("\n[--dry-run] Not writing output file.")
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    out_dir = args.out if args.out else os.path.join(RESULTS_DIR, "output")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "universe_changes.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"[check-universe] Wrote {out_path}")


if __name__ == "__main__":
    main()
