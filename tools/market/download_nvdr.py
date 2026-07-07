"""
download_nvdr.py
สคริปต์ใช้ Playwright ในการดึงข้อมูล NVDR (Top Buy / Sell) จากเว็บ SET
หลีกเลี่ยง 403 Forbidden
"""
import sys
import json
import pandas as pd
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import HISTORY_DIR

NVDR_CSV = Path(__file__).resolve().parents[2] / 'data' / 'nvdr_history.csv'

def clean_number(text):
    text = str(text).strip().replace(',', '')
    if text == '-' or text == '':
        return 0.0
    try:
        return float(text)
    except:
        return 0.0

def scrape_nvdr():
    url = "https://www.set.or.th/th/market/statistics/nvdr/trading-by-stock"
    print(f"Scraping NVDR from {url}...")
    data = []
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url)
            # รอโหลด
            page.wait_for_timeout(5000)
            
            nuxt_str = page.evaluate('JSON.stringify(window.__NUXT__)')
            browser.close()
            
            if not nuxt_str:
                print("⚠️ ไม่พบข้อมูล NUXT (อาจจะโดนบล็อก หรือเว็บโหลดไม่ขึ้น)")
                return None
                
            nuxt_data = json.loads(nuxt_str)
            tradings = nuxt_data.get('state', {}).get('statistics', {}).get('Nvdr', {}).get('tradingStockDefault', {}).get('nvdrTradings', [])
            
            if not tradings:
                print("⚠️ ไม่พบข้อมูลการเทรดใน NUXT")
                return None
                
            for item in tradings:
                symbol = item.get('symbol', '').strip()
                if not symbol:
                    continue
                    
                # Use date from API, fallback to today_str if not available
                api_date = item.get('date')
                if api_date:
                    record_date = api_date.split('T')[0]
                else:
                    record_date = today_str
                    
                buy = clean_number(item.get('buyValue'))
                sell = clean_number(item.get('sellValue'))
                total = clean_number(item.get('totalValue'))
                net = clean_number(item.get('netValue'))
                
                data.append({
                    "Date": record_date,
                    "Symbol": symbol,
                    "Buy": buy,
                    "Sell": sell,
                    "Total": total,
                    "Net": net
                })
                
    except Exception as e:
        print(f"❌ Scraping failed: {e}")
        return None
        
    if not data:
        return None
        
    df = pd.DataFrame(data)
    print(f"ดึงข้อมูลสำเร็จ {len(df)} รายการ")
    return df

def update_nvdr_history():
    df_today = scrape_nvdr()
    if df_today is None or df_today.empty:
        print("ไม่สามารถอัพเดท NVDR ได้")
        return

    if NVDR_CSV.exists():
        df_hist = pd.read_csv(NVDR_CSV)
        # ลบข้อมูลของวันที่เพิ่งดึงมาออกก่อน (เผื่อรันซ้ำ)
        scraped_dates = df_today["Date"].unique().tolist()
        df_hist = df_hist[~df_hist["Date"].isin(scraped_dates)]
        
        # ถ้ารันผิดวันแล้วได้ข้อมูลของเมื่อวานซ้ำ แต่บันทึกเป็นวันนี้ไปแล้ว
        # ลบวันที่เป็นอนาคตหรือวันปัจจุบันที่เกินมาเพื่อความชัวร์ (Optional)
        today_str = datetime.now().strftime("%Y-%m-%d")
        if today_str not in scraped_dates:
            df_hist = df_hist[df_hist["Date"] != today_str]
            
        df_combined = pd.concat([df_hist, df_today], ignore_index=True)
    else:
        df_combined = df_today
        
    # ลบข้อมูลเก่าเกิน 30 วัน
    df_combined['Date_Obj'] = pd.to_datetime(df_combined['Date'])
    df_combined = df_combined.sort_values(['Date_Obj', 'Symbol'], ascending=[False, True])
    
    unique_dates = df_combined['Date_Obj'].dt.date.unique()
    if len(unique_dates) > 30:
        cutoff_date = unique_dates[30]
        df_combined = df_combined[df_combined['Date_Obj'].dt.date >= cutoff_date]
        
    df_combined = df_combined.drop(columns=['Date_Obj'])
    
    NVDR_CSV.parent.mkdir(parents=True, exist_ok=True)
    df_combined.to_csv(NVDR_CSV, index=False)
    print(f"บันทึกข้อมูลสะสม NVDR เรียบร้อย: {NVDR_CSV}")

if __name__ == "__main__":
    update_nvdr_history()
