"""
fetch_sector_metrics.py - sector-level "key numbers" card data (Banking,
Tourism & Leisure to start) from official sources only (BOT, tourism
ministry) - never per-company, always a sector-wide aggregate/rate.

STATUS: Skeleton only - series codes are TBD pending two things the user is
handling themselves:
  1. A BOT (Bank of Thailand) API key (registration in progress).
  2. Confirming which exact series codes to pull, found via the new BOT
     data portal's Search Stat API - this needs to happen WITH the key in
     hand (the search/discovery endpoint itself may require the same
     authenticated access as the data-pull endpoints; unconfirmed until the
     key exists), then reported back for explicit sign-off before any real
     series ID is wired in below. Do not guess a series code and ship it -
     a wrong series silently mislabeled as "NPL ratio" is worse than no
     card at all.

Planned metrics:
  - Banking:  NPL ratio, NIM (net interest margin), LDR (loan-to-deposit
              ratio), สินเชื่อโต (loan growth YoY) - all BOT, monthly/quarterly.
  - Tourism & Leisure: hotel occupancy rate, monthly foreign visitor count -
              BOT tourism statistics or Ministry of Tourism & Sports, monthly.

Caching: these series update monthly (BOT) at most - re-fetching on every
daily pipeline run would be pointless load against BOT's API for data that
hasn't changed. Re-fetch only once the cached copy is more than
CACHE_MAX_AGE_DAYS old; otherwise skip and leave the existing output file
in place untouched (so a fetch failure never wipes out the last good read).

Output: data/results/output/sector_metrics.json (picked up by the existing
"cp -r data/results/output/*.json stockdesk_repo/data/scans/" step in
daily-scan.yml, same as every other pipeline JSON output), shape:

    {
      "generated_at": "2026-07-20T09:00:00+07:00",
      "banking": {
        "source": "ธนาคารแห่งประเทศไทย (ธปท.) - รายเดือน",
        "as_of": "2026-06",           # the period the figures describe, not fetch time
        "npl_ratio_pct": null,        # TODO: series code TBD
        "nim_pct": null,              # TODO: series code TBD
        "ldr_pct": null,              # TODO: series code TBD
        "loan_growth_yoy_pct": null,  # TODO: series code TBD
      },
      "tourism": {
        "source": "ธปท./กระทรวงการท่องเที่ยวฯ - รายเดือน",
        "as_of": "2026-06",
        "occupancy_rate_pct": null,   # TODO: series code TBD
        "foreign_visitors": null,     # TODO: series code TBD
      },
    }

Usage (once series codes are confirmed and filled in below):
    python fetch_sector_metrics.py                # writes real output (skips if cache still fresh)
    python fetch_sector_metrics.py --force         # re-fetch regardless of cache age
    python fetch_sector_metrics.py --dry-run       # fetch + print, write nothing
    python fetch_sector_metrics.py --out <dir>     # write into <dir> instead of the real results/output tree
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.append(str(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
from config import RESULTS_DIR  # noqa: E402

BANGKOK_TZ = timezone(timedelta(hours=7))
CACHE_MAX_AGE_DAYS = 7  # re-check monthly-updated data at most weekly, not daily

# BOT's new data portal (placeholder - confirm exact base URL + auth scheme
# once the key exists; the "Search Stat API" the user referenced is how the
# actual series codes below get discovered, not guessed here).
BOT_API_BASE = "https://apiportal.bot.or.th"  # UNCONFIRMED - verify against the real portal docs

# ── Series codes: ALL of these are TBD - do not fill in a guess. ──────────
# Each entry should become {"series_id": "...", "label": "..."} once
# confirmed via the portal's Search Stat API + explicit user sign-off.
BANKING_SERIES = {
    "npl_ratio": None,
    "nim": None,
    "ldr": None,
    "loan_growth_yoy": None,
}
TOURISM_SERIES = {
    "occupancy_rate": None,
    "foreign_visitors": None,
}


def cache_is_fresh(out_path: str, max_age_days: int) -> bool:
    if not os.path.exists(out_path):
        return False
    try:
        with open(out_path, encoding="utf-8") as f:
            existing = json.load(f)
        generated_at = existing.get("generated_at")
        if not generated_at:
            return False
        age = datetime.now(BANGKOK_TZ) - datetime.fromisoformat(generated_at)
        return age < timedelta(days=max_age_days)
    except Exception:
        return False


def fetch_banking_metrics() -> dict:
    """TODO: implement once BANKING_SERIES codes are confirmed. Returns the
    "banking" dict shape documented in this file's docstring."""
    missing = [k for k, v in BANKING_SERIES.items() if v is None]
    if missing:
        print(f"[sector-metrics] Banking series not yet configured: {', '.join(missing)} - skipping banking fetch.")
        return {"source": "ธนาคารแห่งประเทศไทย (ธปท.) - รายเดือน", "as_of": None,
                "npl_ratio_pct": None, "nim_pct": None, "ldr_pct": None, "loan_growth_yoy_pct": None}
    raise NotImplementedError("Fill in the actual BOT API call once series codes are confirmed.")


def fetch_tourism_metrics() -> dict:
    """TODO: implement once TOURISM_SERIES codes are confirmed. Returns the
    "tourism" dict shape documented in this file's docstring."""
    missing = [k for k, v in TOURISM_SERIES.items() if v is None]
    if missing:
        print(f"[sector-metrics] Tourism series not yet configured: {', '.join(missing)} - skipping tourism fetch.")
        return {"source": "ธปท./กระทรวงการท่องเที่ยวฯ - รายเดือน", "as_of": None,
                "occupancy_rate_pct": None, "foreign_visitors": None}
    raise NotImplementedError("Fill in the actual BOT/tourism-ministry API call once series codes are confirmed.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="re-fetch even if the cached output is still fresh")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    out_dir = args.out if args.out else os.path.join(RESULTS_DIR, "output")
    out_path = os.path.join(out_dir, "sector_metrics.json")

    if not args.force and cache_is_fresh(out_path, CACHE_MAX_AGE_DAYS):
        print(f"[sector-metrics] SKIP - {out_path} is less than {CACHE_MAX_AGE_DAYS} days old. Use --force to re-fetch.")
        return

    output = {
        "generated_at": datetime.now(BANGKOK_TZ).isoformat(),
        "banking": fetch_banking_metrics(),
        "tourism": fetch_tourism_metrics(),
    }

    if args.dry_run:
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"[sector-metrics] Wrote {out_path}")


if __name__ == "__main__":
    main()
