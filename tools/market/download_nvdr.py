"""
download_nvdr.py
สคริปต์ใช้ Playwright ในการดึงข้อมูล NVDR (Top Buy / Sell) จากเว็บ SET
หลีกเลี่ยง 403 Forbidden

หน้า SET มีแค่ "วันล่าสุดที่หน้าแสดง" (ปกติ = วันทำการก่อนหน้า · ดึงย้อนวันไม่ได้) → ประวัติหลายวันที่
convert_to_json.build_nvdr ใช้คิดยอด 5 วัน ต้องมาจากที่อื่น: สร้างใหม่ทุกรอบจาก archive ของ stockdesk
(data/history/<วันสแกน>/nvdr.json · แถว "today" = ค่ารายหุ้นของ latest_date ครบ Buy/Sell/Total/Net)
แล้วรวมกับวันที่ดึงได้รอบนี้ (วันซ้ำ = ใช้ของรอบนี้) → เขียน data/nvdr_history.csv (รูปแบบเดิม)

เดิมอ่านต่อจาก data/nvdr_history.csv ใน repo ซึ่งค้างที่ 2026-07-21 (CI ไม่ได้ commit กลับ) →
ยอด 5 วัน = วันล่าสุด + 4 วันของ ก.ค. ทุกรอบ (พบ 2026-11-23)

exit code (run_all.py ใส่ใน failed_steps เมื่อ != 0 · pipeline รันต่อเสมอ):
  0 = ดึงได้ + สร้างประวัติจาก archive ได้ครบ
  1 = ดึง SET ไม่ได้ / ไม่มี archive (ถอยไปใช้ nvdr_history.csv เดิม) / มีไฟล์ archive อ่านไม่ได้
"""
import sys
import json
import re
import pandas as pd
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import HISTORY_DIR  # noqa: E402,F401

_DATA_ENGINE_ROOT = Path(__file__).resolve().parents[2]
NVDR_CSV = _DATA_ENGINE_ROOT / 'data' / 'nvdr_history.csv'
COLUMNS = ["Date", "Symbol", "Buy", "Sell", "Total", "Net"]

# archive ของ stockdesk (หาแบบเดียวกับ sector_map.json ใน 4_calculate_sector_rs.py):
#   - CI: daily-scan.yml sparse-clone เฉพาะ data/history/<~12 วันล่าสุด>/nvdr.json ไว้ที่
#     data_engine/stockdesk_nvdr_check/ ก่อน run_all.py (checkout stockdesk หลักเกิดหลัง run_all)
#   - เครื่อง local: stockdesk เป็น repo พี่น้องในเครื่องเดียวกัน
ARCHIVE_CANDIDATES = [
    _DATA_ENGINE_ROOT / "stockdesk_nvdr_check" / "data" / "history",
    _DATA_ENGINE_ROOT.parent.parent / "Claude" / "dashboard" / "stockdesk" / "data" / "history",
]
# จำนวน latest_date ที่ต่างกันที่เก็บจาก archive (ยอด 5 วันใช้ 5 · เก็บเผื่อ)
ARCHIVE_DATES = 10
NVDR_5D_DAYS = 5
_DAY_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def clean_number(text):
    text = str(text).strip().replace(',', '')
    if text == '-' or text == '':
        return 0.0
    try:
        return float(text)
    except:
        return 0.0

def scrape_nvdr():
    from playwright.sync_api import sync_playwright

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


def find_archive_dir(candidates=None):
    """โฟลเดอร์ data/history แรกที่มี <วัน>/nvdr.json อย่างน้อย 1 ไฟล์ · ไม่เจอ = None"""
    for d in (ARCHIVE_CANDIDATES if candidates is None else candidates):
        d = Path(d)
        if d.is_dir() and any((x / "nvdr.json").is_file() for x in d.iterdir() if x.is_dir() and _DAY_DIR.match(x.name)):
            return d
    return None


def load_archive_history(archive_dir, max_dates=ARCHIVE_DATES):
    """อ่าน <archive_dir>/<วันสแกน>/nvdr.json จากโฟลเดอร์ใหม่สุดย้อนลงไป จนได้ latest_date ต่างกัน max_dates วัน

    คืน (DataFrame คอลัมน์ COLUMNS หรือ None ถ้าอ่านไม่ได้เลย, รายการปัญหาที่ต้องรายงาน, ข้อความ info)
    - dedupe ตาม latest_date (ไม่ใช่ชื่อโฟลเดอร์): หลายโฟลเดอร์ latest_date เดียวกัน (สแกนซ้ำ / snapshot
      วันหยุด) → ใช้ของโฟลเดอร์ใหม่สุด
    - ไฟล์ parse ไม่ได้ / โครงไม่ครบ → ข้ามวันนั้น + บันทึกเป็นปัญหา
    """
    problems, info, frames, seen = [], [], [], set()
    days = sorted((x for x in Path(archive_dir).iterdir() if x.is_dir() and _DAY_DIR.match(x.name)),
                  key=lambda x: x.name, reverse=True)
    for day in days:
        if len(seen) >= max_dates:
            break
        f = day / "nvdr.json"
        if not f.is_file():
            continue
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
            ld = str(raw["latest_date"])[:10]
            datetime.strptime(ld, "%Y-%m-%d")
            rows = raw["today"]
            if not isinstance(rows, list) or not rows:
                raise ValueError("today ว่าง")
            df = pd.DataFrame(rows)
            missing = [c for c in COLUMNS if c not in df.columns]
            if missing:
                raise ValueError(f"ไม่มีคอลัมน์ {missing}")
            df = df[COLUMNS].copy()
            if set(df["Date"].astype(str)) != {ld}:
                raise ValueError(f"Date ในแถวไม่ตรง latest_date {ld}")
            for c in ["Buy", "Sell", "Total", "Net"]:
                df[c] = df[c].astype(float)
        except Exception as e:  # noqa: BLE001 — ไฟล์เดียวเสียต้องไม่ทำให้ทั้งรอบล้ม
            problems.append(f"{day.name}/nvdr.json อ่านไม่ได้ ({type(e).__name__}: {e})")
            continue
        if ld in seen:
            info.append(f"{day.name}/nvdr.json latest_date {ld} ซ้ำกับโฟลเดอร์ที่ใหม่กว่า - ข้าม")
            continue
        seen.add(ld)
        frames.append(df)
    if not frames:
        return None, problems or [f"ไม่มี nvdr.json ที่ใช้ได้ใน {archive_dir}"], info
    return pd.concat(frames, ignore_index=True), problems, info


def _sorted_history(df):
    """เรียงแบบเดียวกับไฟล์เดิม: Date ใหม่→เก่า, Symbol ก→ฮ"""
    df = df.copy()
    df['Date_Obj'] = pd.to_datetime(df['Date'])
    df = df.sort_values(['Date_Obj', 'Symbol'], ascending=[False, True])
    return df.drop(columns=['Date_Obj'])


def _legacy_update(df_today):
    """พฤติกรรมเดิม (ใช้เมื่อไม่มี archive): ต่อจาก nvdr_history.csv เดิมใน repo"""
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

    return df_combined.drop(columns=['Date_Obj'])


def update_nvdr_history(df_today=None, archive_candidates=None, scrape=True):
    """คืน exit code (0 = ปกติ · 1 = มีปัญหาที่ต้องขึ้น failed_steps) · ไม่ raise"""
    if scrape:
        df_today = scrape_nvdr()
    scrape_ok = df_today is not None and not df_today.empty
    if not scrape_ok:
        print("❌ NVDR: ดึงข้อมูลจาก SET ไม่ได้ - รอบนี้ไม่มีวันใหม่ (จะขึ้นใน failed_steps)")

    failed = not scrape_ok
    archive_dir = find_archive_dir(archive_candidates)
    archive = None
    if archive_dir is None:
        print("⚠️ NVDR: ไม่พบ archive stockdesk data/history/*/nvdr.json "
              f"(ลอง {', '.join(str(c) for c in (ARCHIVE_CANDIDATES if archive_candidates is None else archive_candidates))})"
              " - ถอยไปใช้ nvdr_history.csv เดิม (ยอด 5 วันอาจผิด) · จะขึ้นใน failed_steps")
        failed = True
    else:
        try:
            archive, problems, info = load_archive_history(archive_dir)
        except Exception as e:  # noqa: BLE001
            archive, problems, info = None, [f"อ่าน archive ไม่ได้ ({type(e).__name__}: {e})"], []
        for msg in info:
            print(f"ℹ️ NVDR archive: {msg}")
        for msg in problems:
            print(f"⚠️ NVDR archive: {msg}")
        if problems:
            failed = True
        if archive is None:
            print("⚠️ NVDR: สร้างประวัติจาก archive ไม่ได้ - ถอยไปใช้ nvdr_history.csv เดิม (ยอด 5 วันอาจผิด)")

    if archive is None:
        if not scrape_ok:
            print("ไม่สามารถอัพเดท NVDR ได้ (คง nvdr_history.csv เดิม)")
            return 1
        df_combined = _legacy_update(df_today)
    else:
        if scrape_ok:
            # วันเดียวกับที่ดึงได้รอบนี้ → ใช้ของรอบนี้
            archive = archive[~archive["Date"].isin(df_today["Date"].unique())]
            df_combined = pd.concat([archive, df_today[COLUMNS]], ignore_index=True)
        else:
            df_combined = archive
        df_combined = _sorted_history(df_combined)
        dates = sorted(df_combined["Date"].unique(), reverse=True)
        print(f"NVDR history (archive {archive_dir}): {len(dates)} วัน {dates[0]} … {dates[-1]}")
        if len(dates) < NVDR_5D_DAYS:
            print(f"⚠️ NVDR: มีข้อมูลแค่ {len(dates)} วัน (< {NVDR_5D_DAYS}) - ยอด 5 วันคิดจากเท่าที่มี")
        else:
            print(f"   ยอด 5 วัน = {', '.join(dates[:NVDR_5D_DAYS])}")

    NVDR_CSV.parent.mkdir(parents=True, exist_ok=True)
    df_combined.to_csv(NVDR_CSV, index=False)
    print(f"บันทึกข้อมูลสะสม NVDR เรียบร้อย: {NVDR_CSV}")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        code = update_nvdr_history()
    except Exception as e:  # noqa: BLE001 — กันทุกกรณี: ล้มแบบมี exit code ให้ failed_steps เห็น
        print(f"❌ download_nvdr.py ล้ม: {type(e).__name__}: {e}")
        code = 1
    sys.exit(code)
