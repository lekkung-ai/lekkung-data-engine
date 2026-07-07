"""
4_update_warrants.py  —  Warrant Analysis Engine
=================================================
แหล่งข้อมูล:
  - รายชื่อ Warrant : warrants_master.csv (คอลัมน์เดียว ชื่อ "Warrant")
  - ราคา Warrant / Exercise Price / Ratio / Expire : scrape set.or.th
  - หุ้นแม่  : แกะจากชื่อ Warrant (TRUBB-W3 → TRUBB)
  - ราคาหุ้นแม่ : scrape set.or.th

วิธีเพิ่ม Warrant ใหม่:
  เปิด warrants_master.csv แล้วเพิ่มชื่อ Warrant ได้เลย ไม่ต้องแตะโค้ด
"""

import re
import sys
import time
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Force UTF-8 encoding for standard output/error on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import pandas as pd
import requests
from bs4 import BeautifulSoup

sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import RESULTS_DIR, WARRANTS_MASTER, DAILY_FILE

# ── CONFIG ────────────────────────────────────────────────────────────────────
MASTER_CSV = WARRANTS_MASTER  # 👈 2. ให้ชี้เป้าไปที่ไฟล์ในโฟลเดอร์ manual_input ทันที
OUTPUT_CSV = RESULTS_DIR / "warrant_analysis.csv"
WORKERS = 10

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "th,en;q=0.9",
}


# ── SCRAPE ────────────────────────────────────────────────────────────────────


def derive_underlying(symbol: str) -> str:
    """TRUBB-W3 → TRUBB, BTS-W8 → BTS"""
    return re.sub(r"-W\d+$", "", symbol.upper()).strip()


def scrape_page(symbol: str) -> str | None:
    url = f"https://www.set.or.th/th/market/product/stock/quote/{symbol}/price"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        return r.text if r.status_code == 200 else None
    except Exception:
        return None


def parse_price(html: str) -> float | None:
    soup = BeautifulSoup(html, "html.parser")
    for d in soup.find_all("div", class_=lambda c: c and "stock-info" in c):
        try:
            return float(d.get_text(strip=True))
        except ValueError:
            continue
    return None


def parse_warrant_detail(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    result = {"exercise_price": None, "ratio": None, "expire_date": ""}

    # exercise price — หา label แล้วดึง span ถัดไป
    for label in soup.find_all("label"):
        if "ราคาใช้สิทธิ" in label.get_text():
            span = label.find_next_sibling("span")
            if span:
                val = span.get_text(strip=True).replace(",", "")
                if val and val != "-":
                    try:
                        result["exercise_price"] = float(val)
                    except ValueError:
                        pass
            break

    # ratio — หา span ที่มีรูปแบบ "X : Y"
    for span in soup.find_all("span"):
        m = re.match(r"^\s*(\d+)\s*:\s*(\d+)\s*$", span.get_text(strip=True))
        if m:
            result["ratio"] = round(float(m.group(2)) / float(m.group(1)), 4)
            break

    # expire date — หา label วันหมดอายุ แล้วดึง span ถัดไป
    for label in soup.find_all("label"):
        if "วันหมดอายุ" in label.get_text():
            span = label.find_next_sibling("span")
            if span:
                val = span.get_text(strip=True)
                if val and val != "-":
                    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
                        try:
                            result["expire_date"] = datetime.strptime(
                                val[:10], fmt
                            ).strftime("%Y-%m-%d")
                            break
                        except ValueError:
                            continue
            break

    return result


# ── CALCULATE ─────────────────────────────────────────────────────────────────


def calculate(row: dict) -> dict:
    w_price = row.get("warrant_price")
    p_price = row.get("underlying_price")
    ex_price = row.get("exercise_price")
    ratio = row.get("ratio") or 1.0
    expire = row.get("expire_date", "")

    days_left = None
    if expire:
        try:
            days_left = (
                datetime.strptime(expire[:10], "%Y-%m-%d") - datetime.today()
            ).days
        except Exception:
            pass

    intrinsic = cost = premium = None
    status = "N/A"

    if w_price and p_price and ex_price:
        intrinsic = round(max(0.0, (p_price - ex_price) * ratio), 4)
        cost = round((w_price / ratio) + ex_price, 2)
        premium = round(((cost - p_price) / p_price) * 100, 2)
        status = "ITM" if p_price > ex_price else "OTM"

    row.update(
        {
            "days_left": days_left,
            "intrinsic_value": intrinsic,
            "cost_to_exercise": cost,
            "premium_pct": premium,
            "status": status,
        }
    )
    return row


# ── DISPLAY ───────────────────────────────────────────────────────────────────


def display(rows: list[dict]):
    df = pd.DataFrame(rows)
    df_show = df[df["status"] != "N/A"].copy()

    if df_show.empty:
        print("\n[!] ไม่มีแถวที่คำนวณได้ครบ")
        return

    df_show["_ord"] = df_show["status"].map({"ITM": 0, "OTM": 1}).fillna(2)
    df_show = df_show.sort_values(["_ord", "premium_pct"]).drop(columns="_ord")

    cols = [
        "warrant",
        "underlying",
        "underlying_price",
        "warrant_price",
        "exercise_price",
        "ratio",
        "premium_pct",
        "days_left",
        "status",
    ]
    rename = {
        "warrant": "Warrant",
        "underlying": "Underlying",
        "underlying_price": "Parent Price",
        "warrant_price": "W Price",
        "exercise_price": "Ex Price",
        "ratio": "Ratio",
        "premium_pct": "Premium%",
        "days_left": "Days Left",
        "status": "Status",
    }

    itm = (df_show["status"] == "ITM").sum()
    otm = (df_show["status"] == "OTM").sum()
    na = (df["status"] == "N/A").sum()
    sep = "=" * 100

    print(f"\n{sep}")
    print(f"  WARRANT ANALYSIS  —  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(
        f"  ทั้งหมด {len(df)} ตัว  |  คำนวณได้ {len(df_show)} ตัว  |  ITM {itm}  OTM {otm}  N/A {na}"
    )
    print(sep)
    print(df_show[cols].rename(columns=rename).to_string(index=False))
    print(sep)


# ── MAIN ──────────────────────────────────────────────────────────────────────

def process_warrant(sym: str, parent_prices: dict) -> dict:
    html = scrape_page(sym)
    parent = derive_underlying(sym)
    
    if not html:
        return {"warrant": sym, "underlying": parent, "status": "N/A"}

    w_price = parse_price(html)
    detail = parse_warrant_detail(html)
    
    p_price = parent_prices.get(parent)
    if pd.isna(p_price):
        p_price = None

    if p_price is None:
        p_html = scrape_page(parent)
        p_price = parse_price(p_html) if p_html else None

    row = {
        "warrant": sym,
        "underlying": parent,
        "warrant_price": w_price,
        "underlying_price": p_price,
        **detail,
    }
    return calculate(row)


def update_warrant_data():
    print("=" * 60)
    print("  Warrant Analysis Engine (Optimized)")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 1. โหลด CSV
    if not MASTER_CSV.exists():
        print(f"[ERROR] ไม่พบ {MASTER_CSV}")
        print(f"        วางไฟล์ warrants_master.csv ไว้ที่ {MASTER_CSV}")
        return

    symbols = pd.read_csv(MASTER_CSV)["Warrant"].dropna().str.strip().tolist()
    symbols = [s for s in symbols if s]
    print(f"\n[1] โหลด {len(symbols)} Warrant จาก {MASTER_CSV.name}")

    # 2. โหลดราคาหุ้นแม่จาก cache (daily_prices.csv)
    print(f"\n[2] โหลดราคาหุ้นแม่จาก {DAILY_FILE.name}...")
    parent_prices = {}
    if DAILY_FILE.exists():
        try:
            df_daily = pd.read_csv(DAILY_FILE)
            if "Ticker" in df_daily.columns and "Close" in df_daily.columns:
                parent_prices = dict(zip(df_daily["Ticker"], df_daily["Close"]))
                print(f"  ✅ ดึงราคาหุ้นแม่สำเร็จ {len(parent_prices)} ตัว")
        except Exception as e:
            print(f"  ⚠️ โหลดไม่ได้ ({e}) จะใช้วิธีดึงใหม่ผ่านเว็บแทน")
    else:
        print("  ⚠️ ไม่พบไฟล์ daily_prices.csv จะใช้วิธีดึงผ่านเว็บแทน")

    # 3. Scrape and Calculate
    print(f"\n[3] Scrape ข้อมูล Warrant จาก set.or.th (Multithreading: {WORKERS} workers)...")
    rows: list[dict] = []
    
    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(process_warrant, sym, parent_prices): sym for sym in symbols}
        
        completed = 0
        for future in as_completed(futures):
            sym = futures[future]
            try:
                result = future.result()
                rows.append(result)
            except Exception as e:
                print(f"\n  [!] Error processing {sym}: {e}")
                rows.append({"warrant": sym, "underlying": derive_underlying(sym), "status": "N/A"})
            
            completed += 1
            print(f"\r  ▶ ดึงข้อมูลเสร็จแล้ว {completed}/{len(symbols)} ตัว", end="", flush=True)

    # 4. บันทึก CSV
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df_out = pd.DataFrame(rows).sort_values("warrant")
    df_out.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    print(f"\n\n[4] บันทึก → {OUTPUT_CSV}  ({len(rows)} แถว)")

    # 5. แสดงผล
    display(df_out.to_dict("records"))


if __name__ == "__main__":
    update_warrant_data()
