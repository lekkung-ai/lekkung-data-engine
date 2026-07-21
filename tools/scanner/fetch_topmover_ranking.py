"""
fetch_topmover_ranking.py
Snapshot today's SETTrade ranking (top gainers/losers, most active by value
and by volume - the same 4 categories app/top-movers/page.tsx shows live)
for both SET and mai, at pipeline run time (~18:11 ICT, after market close,
once ranking has settled for the day).

This does NOT do any history/accumulation itself - it only writes today's
snapshot, same as every other script feeding data/results/output/. The
existing pipeline already:
  1. copies data/results/output/*.json -> stockdesk_repo/data/scans/ (today's
     live copy, same as every other scan JSON)
  2. copies the same files -> stockdesk_repo/data/history/{DATESTAMP}/ (the
     permanent daily archive compute_scan_history.py already reads for the
     "วันนี้ | ย้อนหลัง" toggle on scan pages)
so this file gets archived for free once it exists - no separate history
logic needed here. stockdesk_repo/scripts/compute_topmover_history.py (a
new script, run after the stockdesk_repo checkout, same point as
compute_scan_history.py) aggregates the archived daily copies of this file
into the rolling-90-day topmover_history.json the /top-movers page reads
for its historical view.

Same SETTrade session-cookie dance as app/api/settrade/route.ts (the live
proxy) - Incapsula requires a fresh cookie from /th/home before the ranking
endpoint will answer.

Output: data/results/output/topmover_ranking.json
    {
      "generated_at": "2026-07-21T18:11:00+07:00",
      "date": "2026-07-21",
      "markets": {
        "set": {
          "topGainer": [{"symbol": "GEL", "last": 0.04, "percentChange": 33.33,
                         "totalVolume": 6254884, "totalValue": null}, ...],
          "topLoser": [...],
          "mostActiveValue": [...],
          "mostActiveVolume": [...]
        },
        "mai": { ...same 4 keys... }
      }
    }

Usage:
    python fetch_topmover_ranking.py                # writes real output
    python fetch_topmover_ranking.py --dry-run       # fetch + print, write nothing
    python fetch_topmover_ranking.py --out <dir>     # write into <dir> instead of the real results/output tree
"""
import argparse
import json
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import RESULTS_DIR  # noqa: E402

BKK_TZ = timezone(timedelta(hours=7))
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
RANKING_TYPES = ["topGainer", "topLoser", "mostActiveValue", "mostActiveVolume"]
MARKETS = ["set", "mai"]
COUNT = 30
MAX_TRIES = 3


def make_ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def http_get(url, headers, timeout=10):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout, context=make_ssl_ctx()) as resp:
        raw_cookies = resp.headers.get_all("Set-Cookie") or []
        cookie = "; ".join(c.split(";")[0].strip() for c in raw_cookies if c.strip())
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, body, cookie


def get_session_cookie():
    try:
        _, _, cookie = http_get(
            "https://www.settrade.com/th/home",
            {
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "th-TH,th;q=0.9,en-US;q=0.8",
            },
        )
        return cookie
    except Exception:
        return ""


def fetch_ranking(ranking_type, market):
    url = f"https://www.settrade.com/api/set/ranking/{ranking_type}/{market}/S?count={COUNT}"
    for attempt in range(1, MAX_TRIES + 1):
        cookie = get_session_cookie()
        headers = {
            "User-Agent": UA,
            "Accept": "*/*",
            "Accept-Language": "th-TH,th;q=0.9,en-US;q=0.8",
            "Referer": "https://www.settrade.com/th/equities/market-summary/top-ranking/most-active-value",
            "Origin": "https://www.settrade.com",
        }
        if cookie:
            headers["Cookie"] = cookie
        try:
            status, body, _ = http_get(url, headers)
            if status == 200:
                data = json.loads(body)
                stocks = data.get("stocks", [])
                if stocks:
                    return [
                        {
                            "symbol": s.get("symbol"),
                            "last": s.get("last"),
                            "percentChange": s.get("percentChange"),
                            "totalVolume": s.get("totalVolume"),
                            "totalValue": s.get("totalValue"),
                        }
                        for s in stocks
                    ]
                # valid response, genuinely no rows yet (market not open) -
                # no point retrying, later attempts won't change that.
                return []
        except Exception:
            pass
        if attempt < MAX_TRIES:
            time.sleep(0.6 * attempt)
    return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    out_dir = Path(args.out) if args.out else (RESULTS_DIR / "output")

    now = datetime.now(BKK_TZ)
    markets_out = {}
    for market in MARKETS:
        markets_out[market] = {}
        for rtype in RANKING_TYPES:
            rows = fetch_ranking(rtype, market)
            markets_out[market][rtype] = rows
            print(f"  {market}/{rtype}: {len(rows)} หุ้น")

    out = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S+07:00"),
        "date": now.strftime("%Y-%m-%d"),
        "markets": markets_out,
    }

    if args.dry_run:
        print(json.dumps(out, ensure_ascii=False, indent=2)[:2000])
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "topmover_ranking.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"  ✓ topmover_ranking.json: {out_path.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
