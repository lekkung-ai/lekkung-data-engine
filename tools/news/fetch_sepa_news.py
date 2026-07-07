import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import yfinance as yf

# 🔌 เชื่อมต่อ config.py
sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import SEPA_FILE, SEPA_NEWS_FILE


def fetch_news_for_sepa_stocks():
    # 1. เช็คว่ามีรายชื่อหุ้น SEPA หรือยัง (ใช้ Path จาก config)
    if not SEPA_FILE.exists():
        print("=" * 80)
        print(f"❌ ระบบหยุดทำงาน: ไม่พบไฟล์ '{SEPA_FILE.name}'")
        print(
            "💡 คำแนะนำ: กรุณารัน Step 4 (scan_sepa) ก่อน เพื่อหาหุ้นที่จะมาดึงข่าวครับ"
        )
        print("=" * 80)
        return

    try:
        df_sepa = pd.read_csv(SEPA_FILE)
        tickers = df_sepa["Ticker"].tolist()
    except Exception as e:
        print(f"❌ Error อ่านไฟล์ผลลัพธ์ SEPA: {e}")
        return

    if not tickers:
        print("📉 ไม่มีรายชื่อหุ้นให้ดึงข่าว (อาจจะไม่มีหุ้นผ่านเกณฑ์ SEPA ในรอบนี้)")
        return

    print("=" * 80)
    print(f"📰 กำลังค้นหาข่าวล่าสุดสำหรับหุ้น SEPA ทั้ง {len(tickers)} ตัว...")
    print("=" * 80)

    all_news = []

    # 2. วนลูปดึงข่าวทีละตัวผ่าน API ของ Yahoo Finance
    for ticker in tickers:
        try:
            yf_ticker = f"{ticker}.BK"
            stock = yf.Ticker(yf_ticker)
            news_list = stock.news

            if not news_list:
                continue

            for news in news_list[:3]:
                pub_date = datetime.fromtimestamp(
                    news.get("providerPublishTime", 0)
                ).strftime("%Y-%m-%d %H:%M:%S")

                all_news.append(
                    {
                        "Ticker": ticker,
                        "Publish_Date": pub_date,
                        "Publisher": news.get("publisher", "Unknown"),
                        "Title": news.get("title", "No Title"),
                        "Link": news.get("link", "#"),
                    }
                )

            print(f"✅ ดึงข่าว {ticker} สำเร็จ")

        except Exception as e:
            print(f"⚠️ ดึงข่าว {ticker} ล้มเหลว ({e})")
            continue

    # 3. บันทึกผลลัพธ์และแสดงผล
    print("\n" + "=" * 80)
    if all_news:
        df_news = pd.DataFrame(all_news)
        df_news = df_news.sort_values(by="Publish_Date", ascending=False)

        # 🎯 เซฟลงโฟลเดอร์ News ผ่าน config
        df_news.to_csv(SEPA_NEWS_FILE, index=False, encoding="utf-8-sig")

        print(f"✨ ค้นพบข่าวที่เกี่ยวข้องทั้งหมด: {len(df_news)} ข่าว")
        print(f"📁 บันทึกข้อมูลข่าวลงในไฟล์: {SEPA_NEWS_FILE.name} เรียบร้อยแล้ว")

        print("-" * 80)
        print("ตัวอย่างพาดหัวข่าวล่าสุด:")
        for idx, row in df_news.head(5).iterrows():
            print(f"[{row['Ticker']}] {row['Publish_Date']} - {row['Title']}")
    else:
        print("📉 ไม่พบข่าวอัปเดตสำหรับหุ้นในกลุ่มนี้เลย")
    print("=" * 80)


if __name__ == "__main__":
    fetch_news_for_sepa_stocks()
