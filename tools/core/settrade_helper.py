"""
settrade_helper.py — Shared SETTrade authentication session & fundamental data helper.

Provides:
- get_authenticated_session(): Returns a requests.Session with initialised SETTrade cookies.
- fetch_settrade_fundamentals(session, ticker): Fetches fundamental metrics (PE, PBV, Dividend Yield, Market Cap)
  from SETTrade API (https://www.settrade.com/api/set/stock/{ticker}/info).
"""

import logging
import requests

logger = logging.getLogger(__name__)

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
        logger.warning(f"[settrade-helper] Warning: SETTrade home fetch failed: {e}")
    return session


def fetch_settrade_fundamentals(session: requests.Session, ticker: str) -> dict | None:
    """
    Fetch per-ticker fundamental metrics from SETTrade info API.
    URL: https://www.settrade.com/api/set/stock/{ticker}/info

    Returns dict with keys:
    - pe: float or None (None if loss-making or unavailable)
    - pbv: float or None
    - divYield: float or None
    - marketCap: float or None

    Returns None if request fails or times out. Non-fatal, caller decides fallback.
    """
    url = f"https://www.settrade.com/api/set/stock/{ticker}/info"
    try:
        res = session.get(url, timeout=5)
        if res.status_code != 200:
            return None
        data = res.json()
        if not isinstance(data, dict):
            return None
        return {
            "pe": data.get("peRatio"),
            "pbv": data.get("pbRatio"),
            "divYield": data.get("dividendYield"),
            "marketCap": data.get("marketCap"),
        }
    except Exception as e:
        logger.warning(f"[settrade-helper] Error fetching fundamentals for {ticker}: {e}")
        return None
