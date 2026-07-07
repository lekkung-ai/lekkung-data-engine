import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import requests

# 🔌 เชื่อมต่อ config.py
sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import NEWS_FILE, SEPA_FILE


def fetch_set_official_news():
    # ใช้ Path จาก config โดยตรง
    if not SEPA_FILE.exists():
        print(f"❌ ไม่พบไฟล์ '{SEPA_FILE.name}' กรุณารันการสแกน SEPA ก่อน")
        return

    # อ่านรายชื่อหุ้นที่เราสนใจ (จาก SEPA)
    df_sepa = pd.read_csv(SEPA_FILE)
    tickers = set(df_sepa["Ticker"].str.upper().tolist())

    print(f"📡 กำลังดึงข่าวแจ้งตลาดล่าสุดจาก SET RSS Feed...")

    rss_url = "https://www.set.or.th/set/todaynews.do?period=all&language=th&country=TH&type=R"
    google_set_rss = (
        "https://news.google.com/rss/search?q=site:set.or.th&hl=th&gl=TH&ceid=TH:th"
    )

    all_news = []
    try:
        response = requests.get(google_set_rss, timeout=15)
        root = ET.fromstring(response.content)

        for item in root.findall(".//item"):
            title = item.find("title").text
            link = item.find("link").text
            pub_date = item.find("pubDate").text

            matched_ticker = None
            for t in tickers:
                if t in title.upper():
                    matched_ticker = t
                    break

            if matched_ticker:
                all_news.append(
                    {
                        "Ticker": matched_ticker,
                        "Date": pub_date,
                        "Title": title,
                        "Link": link,
                    }
                )

        if all_news:
            df_news = pd.DataFrame(all_news)
            # 🎯 เซฟลงโฟลเดอร์ News ผ่าน config
            df_news.to_csv(NEWS_FILE, index=False, encoding="utf-8-sig")
            print(
                f"✅ สำเร็จ! พบข่าวแจ้งตลาดสำหรับหุ้นที่คุณสนใจ {len(df_news)} รายการ"
            )
            print(f"📁 บันทึกลงไฟล์: {NEWS_FILE.name}")
        else:
            print("📉 ไม่พบข่าวแจ้งตลาดใหม่สำหรับหุ้นในกลุ่ม SEPA ของคุณ")

    except Exception as e:
        print(f"❌ เกิดข้อผิดพลาดในการดึงข่าว: {e}")


if __name__ == "__main__":
    fetch_set_official_news()
