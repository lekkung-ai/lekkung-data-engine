"""
tools/news/news_sources.py — News Aggregator (SET + US Markets)
เพิ่ม source ใหม่ได้ง่ายๆ แค่เพิ่ม dict เข้าไปใน NEWS_SOURCES

วิธีใช้:
  python tools/news/news_sources.py              # ดึงข่าวทั้งหมด
  python tools/news/news_sources.py --market SET # เฉพาะหุ้นไทย
  python tools/news/news_sources.py --market US  # เฉพาะ US
  python tools/news/news_sources.py --ticker PTT # เฉพาะหุ้นนั้น
"""

import argparse
import re
import sys
import time
import warnings
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import feedparser
import pandas as pd
import requests
from bs4 import BeautifulSoup

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from config import NEWS_FILE, RESULTS_DIR, p
from tools.core.utils import get_logger, save_result

log = get_logger("news_sources")

REQUEST_TIMEOUT = 15
PAUSE_BETWEEN = 1.2  # วินาที pause ระหว่าง source (ป้องกันโดน block)
MAX_PER_SOURCE = 30  # ข่าวสูงสุดต่อ source
MAX_PER_TICKER = 5  # ข่าวสูงสุดต่อหุ้น 1 ตัว

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "th-TH,th;q=0.9,en-US;q=0.8,en;q=0.7",
}

# ============================================================
# SOURCE LIST — เพิ่ม/ลบ/ปิด source ได้ที่นี่
# ============================================================
NEWS_SOURCES = [
    # ──────────────────────────────────────────────────────
    # SET — ทางการ
    # ──────────────────────────────────────────────────────
    {
        "name": "SET Newsroom",
        "market": "SET",
        "type": "set_api",
        "url": "https://www.set.or.th/api/set/news/search?lang=th&keyword=&pageSize=50",
        "enabled": True,
        "note": "ข่าวแจ้งตลาด corporate action งบการเงิน ทางการ",
    },
    {
        "name": "SEC Thailand",
        "market": "SET",
        "type": "rss",
        "url": "https://www.sec.or.th/TH/Pages/News_Detail.aspx?SECID=",
        "rss_url": "https://www.sec.or.th/TH/RSSFeeds/Pages/News.aspx",
        "enabled": True,
        "note": "ข่าว กลต. insider, ผู้ถือหุ้นใหญ่",
    },
    # ──────────────────────────────────────────────────────
    # SET — วิเคราะห์และสื่อการเงินไทย
    # ──────────────────────────────────────────────────────
    {
        "name": "Finnomena",
        "market": "SET",
        "type": "rss",
        "rss_url": "https://www.finnomena.com/feed/",
        "enabled": True,
        "note": "บทวิเคราะห์ fund flow macro view",
    },
    {
        "name": "Krungsri Research",
        "market": "SET",
        "type": "rss",
        "rss_url": "https://www.krungsri.com/en/research/rss",
        "enabled": True,
        "note": "research report sector outlook",
    },
    {
        "name": "Bangkok Post Business",
        "market": "SET",
        "type": "rss",
        "rss_url": "https://www.bangkokpost.com/rss/data/business.xml",
        "enabled": True,
        "note": "ข่าวธุรกิจภาษาอังกฤษ cover SET ดี",
    },
    {
        "name": "Thairath Money",
        "market": "SET",
        "type": "scrape",
        "url": "https://www.thairath.co.th/money/markets/thai_stocks",
        "selector": "article.article-item",
        "title_sel": "h3",
        "link_sel": "a",
        "enabled": True,
        "note": "ข่าวหุ้นกระแสหลัก",
    },
    {
        "name": "Investing.com SET",
        "market": "SET",
        "type": "rss",
        "rss_url": "https://th.investing.com/rss/news_14.rss",
        "enabled": True,
        "note": "ข่าว SET จาก Investing.com",
    },
    # ──────────────────────────────────────────────────────
    # US — Official & Major Media
    # ──────────────────────────────────────────────────────
    {
        "name": "Reuters Markets",
        "market": "US",
        "type": "rss",
        "rss_url": "https://feeds.reuters.com/reuters/businessNews",
        "enabled": True,
        "note": "ข่าว US market, earnings, macro",
    },
    {
        "name": "MarketWatch",
        "market": "US",
        "type": "rss",
        "rss_url": "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
        "enabled": True,
        "note": "real-time US stock news",
    },
    {
        "name": "Investing.com US",
        "market": "US",
        "type": "rss",
        "rss_url": "https://www.investing.com/rss/news_25.rss",
        "enabled": True,
        "note": "US stocks general news",
    },
    {
        "name": "Seeking Alpha",
        "market": "US",
        "type": "rss",
        "rss_url": "https://seekingalpha.com/market_currents.xml",
        "enabled": True,
        "note": "analysis + earnings commentary",
    },
    {
        "name": "Yahoo Finance US",
        "market": "US",
        "type": "rss",
        "rss_url": "https://finance.yahoo.com/news/rssindex",
        "enabled": True,
        "note": "broad US market news",
    },
    # ──────────────────────────────────────────────────────
    # GLOBAL — Macro ที่กระทบทั้ง SET และ US
    # ──────────────────────────────────────────────────────
    {
        "name": "Reuters Asia",
        "market": "GLOBAL",
        "type": "rss",
        "rss_url": "https://feeds.reuters.com/reuters/asiaNews",
        "enabled": True,
        "note": "Asia macro, Fed, rate decision",
    },
    {
        "name": "CNBC Top News",
        "market": "GLOBAL",
        "type": "rss",
        "rss_url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
        "enabled": True,
        "note": "global macro, earnings season",
    },
    {
        "name": "FT Markets",
        "market": "GLOBAL",
        "type": "rss",
        "rss_url": "https://www.ft.com/markets?format=rss",
        "enabled": False,  # ปิดไว้ — FT อาจ require subscription
        "note": "เปิดถ้ามี FT subscription",
    },
]


# ============================================================
# PARSERS
# ============================================================


def _normalize_date(raw: str) -> str:
    """แปลง date string หลายรูปแบบให้เป็น YYYY-MM-DD HH:MM UTC"""
    if not raw:
        return ""
    try:
        dt = parsedate_to_datetime(raw)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        pass
    try:
        # ISO format
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return raw[:19]


def _find_tickers(text: str, tickers: set[str]) -> list[str]:
    """
    หา ticker ที่ match ใน text
    ใช้ word boundary ป้องกัน false positive (เช่น BJC ติดใน RBJC)
    คืน list ของ ticker ที่พบ (อาจมีมากกว่า 1)
    """
    found = []
    text_upper = text.upper()
    for ticker in tickers:
        pattern = rf"(?<![A-Z0-9]){re.escape(ticker)}(?![A-Z0-9])"
        if re.search(pattern, text_upper):
            found.append(ticker)
    return found


def _fetch_rss(source: dict, tickers: set[str]) -> list[dict]:
    """ดึง RSS feed และ match กับ tickers"""
    url = source.get("rss_url") or source.get("url", "")
    news = []
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries[:MAX_PER_SOURCE]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            pub_raw = entry.get("published", "") or entry.get("updated", "")
            summary = entry.get("summary", "")
            full_text = f"{title} {summary}"

            matched = _find_tickers(full_text, tickers)

            # ถ้าหา ticker ไม่เจอ ให้เก็บเป็น "MARKET" (ข่าว macro ทั่วไป)
            if not matched:
                matched = ["MARKET"]

            for ticker in matched:
                news.append(
                    {
                        "Ticker": ticker,
                        "Market": source["market"],
                        "Source": source["name"],
                        "Date": _normalize_date(pub_raw),
                        "Title": title,
                        "Summary": summary[:200] if summary else "",
                        "Link": link,
                    }
                )
    except Exception as e:
        log.warning(f"[{source['name']}] RSS error: {e}")
    return news


def _fetch_set_api(source: dict, tickers: set[str]) -> list[dict]:
    """ดึง SET Newsroom JSON API"""
    news = []
    try:
        resp = requests.get(source["url"], headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("newsItems") or data.get("data") or []
        for item in items[:MAX_PER_SOURCE]:
            symbol = str(item.get("symbol") or item.get("ticker") or "").upper().strip()
            if not symbol:
                continue
            # เก็บทุก symbol จาก SET ไม่ filter ตาม tickers เพื่อให้ครบ
            link = item.get("url") or item.get("link") or ""
            news.append(
                {
                    "Ticker": symbol if symbol in tickers else "MARKET",
                    "Market": "SET",
                    "Source": "SET Newsroom",
                    "Date": item.get("datetime") or item.get("publishedDate") or "",
                    "Title": item.get("headline") or item.get("title") or "",
                    "Summary": "",
                    "Link": (
                        f"https://www.set.or.th{link}" if link.startswith("/") else link
                    ),
                }
            )
    except Exception as e:
        log.warning(f"[SET API] error: {e}")
    return news


def _fetch_scrape(source: dict, tickers: set[str]) -> list[dict]:
    """Scrape HTML สำหรับ site ที่ไม่มี RSS"""
    news = []
    try:
        resp = requests.get(source["url"], headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        articles = soup.select(source.get("selector", "article"))[:MAX_PER_SOURCE]
        for art in articles:
            title_el = art.select_one(source.get("title_sel", "h3"))
            link_el = art.select_one(source.get("link_sel", "a"))
            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            link = link_el.get("href", "") if link_el else ""
            if link and not link.startswith("http"):
                # relative URL → absolute
                from urllib.parse import urljoin

                link = urljoin(source["url"], link)

            matched = _find_tickers(title, tickers)
            if not matched:
                matched = ["MARKET"]

            for ticker in matched:
                news.append(
                    {
                        "Ticker": ticker,
                        "Market": source["market"],
                        "Source": source["name"],
                        "Date": datetime.now(timezone.utc).strftime(
                            "%Y-%m-%d %H:%M UTC"
                        ),
                        "Title": title,
                        "Summary": "",
                        "Link": link,
                    }
                )
    except Exception as e:
        log.warning(f"[{source['name']}] scrape error: {e}")
    return news


# ============================================================
# LOAD TICKERS
# ============================================================


def _load_tickers(market_filter: str | None = None) -> set[str]:
    """
    โหลด ticker จาก results ต่างๆ
    SET  — จาก active_symbols.csv
    US   — จาก us_watchlist.csv (ถ้ามี) หรือ hardcode ไว้ก่อน
    """
    tickers: set[str] = set()

    if market_filter in (None, "SET"):
        for fname in [
            "active_symbols.csv",
            "sepa_stage2_combo.csv",
            "sepa_result.csv",
            "rs_ranking.csv",
        ]:
            fpath = RESULTS_DIR / fname
            try:
                df = pd.read_csv(p(fpath))
                tickers.update(df["Ticker"].str.upper().tolist())
            except Exception:
                pass

    if market_filter in (None, "US"):
        # โหลดจาก us_watchlist.csv ถ้ามี
        us_path = RESULTS_DIR / "us_watchlist.csv"
        try:
            df = pd.read_csv(p(us_path))
            tickers.update(df["Ticker"].str.upper().tolist())
        except Exception:
            # fallback: หุ้น US ยอดนิยม
            us_default = {
                "AAPL",
                "MSFT",
                "NVDA",
                "GOOGL",
                "AMZN",
                "META",
                "TSLA",
                "AMD",
                "INTC",
                "TSM",
                "ASML",
                "AVGO",  # tech / semi
                "JPM",
                "GS",
                "MS",  # finance
                "SPY",
                "QQQ",
                "IWM",  # ETF
            }
            tickers.update(us_default)
            log.info(f"ไม่พบ us_watchlist.csv — ใช้ default {len(us_default)} ตัว")

    return tickers


# ============================================================
# MAIN RUNNER
# ============================================================


def run(market_filter: str | None = None, ticker_filter: str | None = None):
    tickers = _load_tickers(market_filter)
    if ticker_filter:
        tickers = {ticker_filter.upper()}

    # filter sources
    sources = [
        s
        for s in NEWS_SOURCES
        if s["enabled"]
        and (market_filter is None or s["market"] in (market_filter, "GLOBAL"))
    ]

    log.info(f"ดึงข่าว | tickers: {len(tickers)} | sources: {len(sources)}")

    all_news: list[dict] = []

    for src in sources:
        log.info(f"  [{src['market']}] {src['name']}...")
        try:
            if src["type"] == "rss":
                items = _fetch_rss(src, tickers)
            elif src["type"] == "set_api":
                items = _fetch_set_api(src, tickers)
            elif src["type"] == "scrape":
                items = _fetch_scrape(src, tickers)
            else:
                items = []

            log.info(f"  → {len(items)} ข่าว")
            all_news.extend(items)
        except Exception as e:
            log.warning(f"  error: {e}")

        time.sleep(PAUSE_BETWEEN)

    if not all_news:
        log.warning("ไม่ได้ข่าวเลย — ตรวจสอบ network หรือ source URLs")
        return pd.DataFrame()

    df = pd.DataFrame(all_news)

    # dedup + sort
    df = (
        df.drop_duplicates(subset=["Ticker", "Title"])
        .sort_values(["Date"], ascending=False)
        .reset_index(drop=True)
    )

    # จำกัด MAX_PER_TICKER ต่อหุ้น (ยกเว้น MARKET)
    df_ticker = df[df["Ticker"] != "MARKET"]
    df_market = df[df["Ticker"] == "MARKET"]
    df_ticker = (
        df_ticker.groupby("Ticker", group_keys=False)
        .apply(lambda g: g.head(MAX_PER_TICKER))
        .reset_index(drop=True)
    )
    df = pd.concat([df_ticker, df_market], ignore_index=True)

    # บันทึกแยก market
    if market_filter:
        out_path = ROOT / "data" / "news" / f"news_{market_filter.lower()}.csv"
    else:
        out_path = NEWS_FILE

    save_result(df, out_path, label="news_sources")

    # summary
    print(f"\n{'='*60}")
    print(f"📰 ข่าวรวม: {len(df)} รายการ")
    print(f"\nแยกตาม Market:")
    print(df.groupby("Market")["Title"].count().rename("จำนวน").to_string())
    print(f"\nแยกตาม Source:")
    print(df.groupby("Source")["Title"].count().rename("จำนวน").to_string())
    non_market = df[df["Ticker"] != "MARKET"]["Ticker"].nunique()
    print(f"\nหุ้นที่มีข่าว (ไม่นับ MARKET): {non_market} ตัว")
    print(f"{'='*60}")

    return df


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="News Aggregator")
    parser.add_argument(
        "--market",
        choices=["SET", "US", "GLOBAL"],
        default=None,
        help="กรองเฉพาะ market",
    )
    parser.add_argument(
        "--ticker", default=None, help="กรองเฉพาะ ticker เดียว เช่น PTT"
    )
    parser.add_argument(
        "--list-sources", action="store_true", help="แสดงรายชื่อ source ทั้งหมด"
    )
    args = parser.parse_args()

    if args.list_sources:
        print(f"\n{'Source':<25} {'Market':<8} {'Enabled':<8} Note")
        print("-" * 70)
        for s in NEWS_SOURCES:
            status = "✅" if s["enabled"] else "⭕"
            print(f"{s['name']:<25} {s['market']:<8} {status:<8} {s.get('note','')}")
        sys.exit(0)

    run(market_filter=args.market, ticker_filter=args.ticker)
