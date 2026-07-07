import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

# Force UTF-8 encoding for standard output/error on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import feedparser
import pandas as pd

# ==========================================
# 📡 1. แหล่งข่าวแบบ "กวาดกว้าง" (Broad Sources)
# ==========================================
# เราใช้ RSS ที่เป็น "Top Stories" หรือ "Market News" รวมๆ ไม่เจาะจงกลุ่ม
RSS_FEEDS = [
    "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en",  # ข่าวธุรกิจโลก
    "https://news.google.com/rss/search?q=stock+market+most+active+when:24h&hl=en-US&gl=US&ceid=US:en",  # หุ้นที่ขยับแรง
    "https://www.investing.com/rss/news_25.rss",  # ข่าวหุ้นเรียลไทม์
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",  # CNBC Top News
]

# พิกัดเก็บไฟล์ (Cross-platform)
BASE_DIR = Path(__file__).resolve().parents[2]
TREND_DIR = BASE_DIR / "data" / "trends"
TREND_DIR.mkdir(parents=True, exist_ok=True)


class DiscoveryAgent:
    def __init__(self):
        # คำที่ไม่ใช่ชื่อหุ้นแต่โผล่บ่อย ต้องกรองออก (Blacklist)
        self.blacklist = {
            "NYSE",
            "NASDAQ",
            "FED",
            "US",
            "GDP",
            "CEO",
            "AI",
            "IPO",
            "USD",
            "REUTERS",
            "CNBC",
            "BLOOMBERG",
            "APP",
            "STOCK",
            "MARKET",
            "JAPAN",
        }

    def sweep_market(self):
        print(
            f"📡 กำลังเริ่มกวาดเรดาร์หาเทรนด์ล่าสุด... ({datetime.now().strftime('%H:%M')})"
        )
        titles = []
        for url in RSS_FEEDS:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                titles.append(entry.title)
        return titles

    def extract_trends(self, titles):
        # 1. สกัด Ticker (ตัวพิมพ์ใหญ่ 2-5 ตัว)
        potential_tickers = []
        # 2. สกัด Buzzwords (คำที่โผล่มาบ่อยๆ ในข่าว)
        buzzwords = []

        for title in titles:
            # หา Ticker เช่น $NVDA หรือ MU
            tickers = re.findall(r"\b[A-Z]{2,5}\b", title)
            potential_tickers.extend([t for t in tickers if t not in self.blacklist])

            # หาคำสำคัญ (คำที่ยาวกว่า 4 ตัวอักษร)
            words = re.findall(r"\b[A-Za-z]{5,}\b", title)
            buzzwords.extend(
                [w.lower() for w in words if w.upper() not in self.blacklist]
            )

        return Counter(potential_tickers), Counter(buzzwords)

    def run(self):
        titles = self.sweep_market()
        ticker_counts, word_counts = self.extract_trends(titles)

        # ผลลัพธ์ 5 อันดับแรก
        top_tickers = ticker_counts.most_common(10)
        top_words = word_counts.most_common(10)

        print("\n" + "=" * 50)
        print("🔥 TRENDING TICKERS (หุ้นที่มีกระแสข่าวมากที่สุด)")
        print("=" * 50)
        for ticker, count in top_tickers:
            print(f"📈 {ticker:8} | พบในข่าว {count} ครั้ง")

        print("\n" + "=" * 50)
        print("🗣️ MARKET BUZZWORDS (ธีมที่ตลาดกำลังพูดถึง)")
        print("=" * 50)
        for word, count in top_words:
            print(f"💬 {word:8} | พบ {count} ครั้ง")

        # บันทึกเป็น Report
        report_df = pd.DataFrame(top_tickers, columns=["Ticker", "Mentions"])
        report_file = (
            TREND_DIR / f"trend_report_{datetime.now().strftime('%Y%m%d')}.csv"
        )
        report_df.to_csv(report_file, index=False)
        print(f"\n✅ บันทึก Report ไปที่: {report_file}")


if __name__ == "__main__":
    agent = DiscoveryAgent()
    agent.run()
