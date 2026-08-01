"""
check_universe.py - weekly (Monday) universe-drift detector & rename heuristic.

Fetches complete listed stocks (SET + mai) from SETTrade API (`https://www.settrade.com/api/set/stock/list`).
Compares against the current universe (`sector_map.json`) and categorizes changes into groups:
1. `new`: Real IPOs (new ticker, company name not matched to any delisted stock).
2. `delisted`: Confirmed delisted stocks (missing from list AND confirmed delisted/404 via per-ticker profile check).
3. `possible_delisted`: Tickers missing from main list but per-ticker profile still shows Listed.
4. `renamed`: Verified permanent ticker symbol rename ({ "old": ..., "new": ..., "company": ... }).
5. `temporary_symbol`: Temporary trading symbol during SP / restructuring (e.g. BANPU -> BANPUU) - MUST NOT remap history.
6. `possible_rename`: Suspected rename due to partial company name similarity ({ "old": ..., "new": ..., "old_company": ..., "new_company": ..., "reason": ... }).

Supports ignore list (`universe_ignore.json`) to exclude previously rejected tickers.
Outputs idempotent `universe_changes.json` for consumption by the Dashboard /settings page.
"""

import argparse
import difflib
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import requests

BANGKOK_TZ = timezone(timedelta(hours=7))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_SECTOR_MAP_PATH = os.path.normpath(
    os.path.join(SCRIPT_DIR, "..", "..", "..", "..", "Claude", "dashboard", "stockdesk", "data", "scans", "sector_map.json")
)
DEFAULT_IGNORE_PATH = os.path.normpath(
    os.path.join(SCRIPT_DIR, "..", "..", "..", "..", "Claude", "dashboard", "stockdesk", "data", "scans", "universe_ignore.json")
)
DEFAULT_OUTPUT_PATH = os.path.normpath(
    os.path.join(SCRIPT_DIR, "..", "..", "..", "..", "Claude", "dashboard", "stockdesk", "data", "scans", "universe_changes.json")
)

SETTRADE_STOCK_LIST_URL = "https://www.settrade.com/api/set/stock/list"
SETTRADE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.settrade.com/th/home",
}


def get_authenticated_session() -> requests.Session:
    """Initialize a requests Session with SETTrade session cookies."""
    session = requests.Session()
    session.headers.update(SETTRADE_HEADERS)
    try:
        session.get("https://www.settrade.com/th/home", timeout=10)
    except Exception as e:
        print(f"[check-universe] Warning: SETTrade home fetch failed: {e}")
    return session


def fetch_settrade_universe(session: requests.Session) -> dict:
    """Fetch full SET + mai stock universe from SETTrade API."""
    res = session.get(SETTRADE_STOCK_LIST_URL, timeout=15)
    res.raise_for_status()
    data = res.json()

    sec_list = data.get("securitySymbols", [])
    result = {}
    for item in sec_list:
        if item.get("securityType") == "S" and item.get("market") in ["SET", "mai"]:
            symbol = item.get("symbol")
            if not symbol:
                continue
            result[symbol] = {
                "symbol": symbol,
                "name_th": item.get("nameTH", "").strip(),
                "name_en": item.get("nameEN", "").strip(),
                "market": item.get("market"),
                "sector": item.get("sector") or "",
                "industry": item.get("industry") or "",
                "old_symbols": item.get("oldSymbols") or [],
            }
    return result


def fetch_ticker_profile_status(session: requests.Session, ticker: str) -> tuple[int, str, str]:
    """Fetch per-ticker profile using authenticated session to strictly verify status."""
    url = f"https://www.settrade.com/api/set/stock/{ticker}/profile"
    try:
        res = session.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            status = data.get("status") or "Listed"
            name_th = data.get("name") or data.get("companyNameTH") or ticker
            return 200, status, name_th
        return res.status_code, "NotFound", ticker
    except Exception:
        return 500, "Error", ticker


def normalize_company_name(name: str) -> str:
    """Normalize Thai company name for heuristic matching."""
    if not name:
        return ""
    s = name.strip()
    s = re.sub(r"^บริษัท\s*", "", s)
    s = re.sub(r"^บมจ\.\s*", "", s)
    s = re.sub(r"\s*จำกัด\s*\(มหาชน\)\s*$", "", s)
    s = re.sub(r"\s*\(มหาชน\)\s*$", "", s)
    s = re.sub(r"\s*จำกัด\s*$", "", s)
    s = re.sub(r"\s*\(ประเทศไทย\)\s*", "", s)
    s = re.sub(r"[^\wก-๙]", "", s)
    return s.lower()


def load_sector_map(path: str) -> tuple[dict, dict]:
    """Load current mapped universe from sector_map.json."""
    if not os.path.exists(path):
        print(f"[check-universe] Warning: sector_map not found at {path}")
        return {}, {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    ticker_to_sector = data.get("ticker_to_sector", {})
    return data, ticker_to_sector


def load_ignore_list(path: str) -> set:
    """Load previously rejected tickers from universe_ignore.json."""
    if not os.path.exists(path):
        return set()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        rejected = data.get("rejected", [])
        return set(rejected)
    except Exception as e:
        print(f"[check-universe] Warning: Failed to load ignore list from {path}: {e}")
        return set()


def detect_with_custom_candidates(
    session: requests.Session,
    live_universe: dict,
    current_mapped: dict,
    ignored_tickers: set,
    custom_old_companies: dict = None
) -> dict:
    """Core detection engine with strict delisted verification & SP temporary symbol guard."""
    custom_old_companies = custom_old_companies or {}
    live_tickers = set(live_universe.keys())
    current_tickers = set(current_mapped.keys())

    live_tickers_filtered = live_tickers - ignored_tickers
    current_tickers_filtered = current_tickers - ignored_tickers

    new_candidate_keys = sorted(live_tickers_filtered - current_tickers_filtered)
    delisted_candidate_keys = sorted(current_tickers_filtered - live_tickers_filtered)

    unmapped_live = {k: live_universe[k] for k in new_candidate_keys}

    renamed = []
    temporary_symbols = []
    possible_rename = []
    resolved_new = set()
    resolved_delisted = set()

    # Pass 1: Rename Detection & SP Temporary Symbol Guard
    for old_t in delisted_candidate_keys:
        if old_t in resolved_delisted:
            continue

        old_company = custom_old_companies.get(old_t, old_t)
        norm_old = normalize_company_name(old_company)

        for new_t in new_candidate_keys:
            if new_t in resolved_new:
                continue

            new_info = unmapped_live[new_t]
            old_syms = [s.upper() for s in new_info.get("old_symbols") or []]
            new_company = new_info.get("name_th") or new_info.get("name_en") or new_t
            norm_new = normalize_company_name(new_company)

            # Check if matching oldSymbols or exact normalized company name
            if old_t.upper() in old_syms or (norm_old and norm_new and norm_old == norm_new):
                # Verify old_t trading status via per-ticker profile check
                code, status, comp_name = fetch_ticker_profile_status(session, old_t)
                is_old_still_active = (code == 200 and status.lower() != "delisted")

                if is_old_still_active:
                    # Old symbol is still active/suspended in SET (e.g. BANPU during SP -> BANPUU)
                    # This is a TEMPORARY trading symbol, NOT a permanent rename!
                    temporary_symbols.append({
                        "old": old_t,
                        "new": new_t,
                        "company": new_company,
                        "status": status,
                        "reason": f"ชื่อชั่วคราวช่วง SP/พักเทรด ({status}) - ห้าม remap ประวัติเดิม"
                    })
                    resolved_delisted.add(old_t)
                    resolved_new.add(new_t)
                    break
                else:
                    # Old symbol profile is 404 or Delisted -> Permanent Rename
                    renamed.append({
                        "old": old_t,
                        "new": new_t,
                        "company": new_company,
                        "reason": "Official permanent rename match"
                    })
                    resolved_delisted.add(old_t)
                    resolved_new.add(new_t)
                    break

    # Pass 2: Fuzzy company name match (possible_rename)
    for old_t in delisted_candidate_keys:
        if old_t in resolved_delisted:
            continue
        old_company = custom_old_companies.get(old_t, old_t)
        norm_old = normalize_company_name(old_company)
        if not norm_old or len(norm_old) < 4:
            continue

        for new_t in new_candidate_keys:
            if new_t in resolved_new:
                continue
            new_info = unmapped_live[new_t]
            new_company = new_info.get("name_th") or new_info.get("name_en") or new_t
            norm_new = normalize_company_name(new_company)

            ratio = difflib.SequenceMatcher(None, norm_old, norm_new).ratio()
            if ratio >= 0.75:
                possible_rename.append({
                    "old": old_t,
                    "new": new_t,
                    "old_company": old_company,
                    "new_company": new_company,
                    "reason": f"High name similarity ({int(ratio * 100)}%)",
                })
                resolved_delisted.add(old_t)
                resolved_new.add(new_t)
                break

    # Pass 3: Remaining New IPOs
    final_new = []
    for new_t in new_candidate_keys:
        if new_t not in resolved_new:
            info = unmapped_live[new_t]
            final_new.append({
                "ticker": new_t,
                "company_name": info.get("name_th") or info.get("name_en") or new_t,
                "market": info.get("market"),
                "guessed_sector": info.get("sector") or "OTHER",
                "guessed_subsector": info.get("industry") or "OTHER",
            })

    # Pass 4: Strict per-ticker profile check for Delisted vs Possible Delisted
    final_delisted = []
    possible_delisted = []
    for old_t in delisted_candidate_keys:
        if old_t in resolved_delisted:
            continue
        code, profile_status, comp_name = fetch_ticker_profile_status(session, old_t)
        company = custom_old_companies.get(old_t) or comp_name or old_t

        if code == 404 or profile_status.lower() == "delisted":
            final_delisted.append({
                "ticker": old_t,
                "company_name": company,
                "market": "SET",
                "status": profile_status
            })
        else:
            possible_delisted.append({
                "ticker": old_t,
                "company_name": company,
                "market": "SET",
                "status": profile_status,
                "reason": f"Missing from full list but profile still active ({profile_status})"
            })

    now_iso = datetime.now(BANGKOK_TZ).isoformat()
    return {
        "generated_at": now_iso,
        "summary": {
            "total_live": len(live_universe),
            "total_current": len(current_mapped),
            "new_count": len(final_new),
            "delisted_count": len(final_delisted),
            "possible_delisted_count": len(possible_delisted),
            "renamed_count": len(renamed),
            "temporary_symbols_count": len(temporary_symbols),
            "possible_rename_count": len(possible_rename),
        },
        "new": final_new,
        "delisted": final_delisted,
        "possible_delisted": possible_delisted,
        "renamed": renamed,
        "temporary_symbols": temporary_symbols,
        "possible_rename": possible_rename,
    }


def main():
    parser = argparse.ArgumentParser(description="Universe drift detector & rename heuristic")
    parser.add_argument("--dry-run", action="store_true", help="Print result, write nothing")
    parser.add_argument("--force", action="store_true", help="Run regardless of day of week")
    parser.add_argument("--sector-map", default=DEFAULT_SECTOR_MAP_PATH, help="Path to sector_map.json")
    parser.add_argument("--ignore", default=DEFAULT_IGNORE_PATH, help="Path to universe_ignore.json")
    parser.add_argument("--out", default=DEFAULT_OUTPUT_PATH, help="Path to output universe_changes.json")
    args = parser.parse_args()

    today = datetime.now(BANGKOK_TZ)
    if today.weekday() != 0 and not args.force:
        print(f"[check-universe] SKIP - today ({today.strftime('%A')}) is not Monday. Use --force to run.")
        return

    session = get_authenticated_session()

    print("[check-universe] Fetching live universe from SETTrade API...")
    live_universe = fetch_settrade_universe(session)
    print(f"[check-universe] Live SETTrade universe fetched: {len(live_universe)} common stocks (SET + mai)")

    _, current_mapped = load_sector_map(args.sector_map)
    print(f"[check-universe] Current mapped universe in sector_map.json: {len(current_mapped)} stocks")

    ignored_tickers = load_ignore_list(args.ignore)
    if ignored_tickers:
        print(f"[check-universe] Ignored tickers loaded: {len(ignored_tickers)}")

    changes = detect_with_custom_candidates(session, live_universe, current_mapped, ignored_tickers)

    print("\n" + "=" * 50)
    print("📊 UNIVERSE DRIFT SUMMARY")
    print("=" * 50)
    print(f"Total Live Market (SET + mai): {changes['summary']['total_live']}")
    print(f"Total Current Universe:        {changes['summary']['total_current']}")
    print(f"New IPOs (new):                {changes['summary']['new_count']}")
    print(f"Confirmed Delisted (delisted): {changes['summary']['delisted_count']}")
    print(f"Suspected Delisted (review):   {changes['summary']['possible_delisted_count']}")
    print(f"Renamed Tickers (renamed):     {changes['summary']['renamed_count']}")
    print(f"Temporary Symbols (SP guard):  {changes['summary']['temporary_symbols_count']}")
    print(f"Possible Renames (review):     {changes['summary']['possible_rename_count']}")
    print("=" * 50)

    if args.dry_run:
        print("\n[--dry-run] Output JSON:")
        print(json.dumps(changes, ensure_ascii=False, indent=2))
        return

    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(changes, f, ensure_ascii=False, indent=2)
    print(f"[check-universe] Wrote output to {out_path}")

    engine_output_dir = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "data", "results", "output"))
    if os.path.exists(engine_output_dir):
        engine_out_file = os.path.join(engine_output_dir, "universe_changes.json")
        with open(engine_out_file, "w", encoding="utf-8") as f:
            json.dump(changes, f, ensure_ascii=False, indent=2)
        print(f"[check-universe] Also synced output to {engine_out_file}")


if __name__ == "__main__":
    main()
