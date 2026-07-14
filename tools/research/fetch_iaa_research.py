"""
fetch_iaa_research.py
ดึงบทวิเคราะห์จาก SETTrade "ศูนย์รวมบทวิเคราะห์" (IAA — Investment Analysts
Association consensus, หมวดหุ้นรายตัว) ผ่าน Playwright

ทำไมต้อง Playwright: settrade.com อยู่หลัง Incapsula bot-protection —
หน้าเว็บปกติ (SSR HTML) โหลดผ่านได้ แต่ endpoint JSON เบื้องหลัง
(api/cms/v1/research-settrade/search) ต้องมี header พิเศษ (x-client-uuid
ที่ผูกกับ session, x-channel) ที่ได้มาจากตัวหน้าเว็บเองหลัง JS รันแล้ว
เท่านั้น — ยิงตรงด้วย curl/requests ธรรมดาได้ 401/403 เสมอ (probe แล้ว
2026-07-14) ต้องเปิดเบราว์เซอร์จริงให้ JS รันก่อนแล้วค่อยยิง fetch()
จากใน page context — ตามแนวทางเดียวกับ tools/market/download_nvdr.py
ที่มีอยู่แล้วในโปรเจกต์นี้

Usage:
    python fetch_iaa_research.py                    # เขียนไฟล์จริง (default path)
    python fetch_iaa_research.py --dry-run           # ดึง+print เฉยๆ ไม่เขียนไฟล์
    python fetch_iaa_research.py --out <path>        # เขียนไปที่อื่น (ทดสอบ/ชี้ไป
                                                       # stockdesk_repo/data/scans/
                                                       # ตอนรันจริงใน pipeline)
    python fetch_iaa_research.py --pages 3           # จำนวนหน้า list ที่จะดึง (default 3)

Output: JSON เดียว (ไม่แบ่งตามวันแบบ news/research.json) เก็บสะสมทุกชิ้นที่
เจอ (dedupe ด้วย uuid) เพราะ IAA volume ต่ำกว่ามาก (~200 ชิ้นสะสมทั้งหมด
จาก probe) ไม่จำเป็นต้องแบ่งไฟล์ต่อวัน — merge เข้ากับของเดิมที่ --out
เสมอ (อ่านไฟล์เดิมที่ path เดียวกันก่อนเขียนทับ) เพื่อกัน gap วันหยุด/
วันที่ไม่ได้รัน (บทวิเคราะห์เก่ากว่าที่หลุดจาก 3 หน้าแรกในการรันครั้งถัด
ไปจะยังอยู่ในไฟล์ต่อไป ไม่หายไปไหน)

ไม่ดาวน์โหลด/เก็บไฟล์ PDF ใดๆ — เก็บแค่ fileUrl (ลิงก์ตรงไป media.settrade.com)
ให้ frontend ลิงก์ออกไปเอง
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "data" / "results" / "output" / "research_iaa.json"

LIST_URL = "https://www.settrade.com/th/research/analyst-research/main?category=stock&subCate=quote"
CATE_UUID = "c2b75714-6f90-4680-92cb-320c2492f099"       # "หุ้น"
SUBCATE_UUID = "aa8c0da4-8791-4b97-a4b4-ce3e5eb3a8ad"     # "หุ้นรายตัว"
PAGE_SIZE = 20
BANGKOK_TZ = timezone(timedelta(hours=7))

# ── Title parsing ────────────────────────────────────────────────────────────
# Title format observed (2026-07-14 probe), fairly consistent across brokers:
#   "{Rating} by/โดย {Broker} | {ชื่อบริษัท} ({TICKER}) | Research as of {date}"
#   "{Broker} | {free text} | บทวิเคราะห์ประจำวันที่ {date}"  (roundup/no single rating)
# `source` field already gives the broker authoritatively - title parsing here
# is only for rating + company name, both best-effort like the Kaohoon/
# มิติหุ้น parser (app/api/research/[ticker]/route.ts) - null is fine, the UI
# must not drop an item just because parsing failed.
RATING_SPLIT_RE = re.compile(r"^(.*?)\s+(?:by|โดย)\s+", re.IGNORECASE)


def extract_rating(title: str) -> str | None:
    m = RATING_SPLIT_RE.match(title)
    if not m:
        return None
    rating = m.group(1).strip()
    return rating or None


def extract_company_name(title: str) -> str | None:
    parts = [p.strip() for p in title.split("|")]
    if len(parts) < 2:
        return None
    # the segment right after the rating/broker segment usually holds
    # "ชื่อบริษัท (TICKER)" - strip the trailing "(TICKER)" part if present
    company_segment = parts[1]
    m = re.match(r"^(.*?)\s*\([A-Za-z0-9]+(?:,[A-Za-z0-9]+)*\)\s*$", company_segment)
    if m:
        return m.group(1).strip() or None
    return company_segment or None


def now_iso() -> str:
    return datetime.now(BANGKOK_TZ).strftime("%Y-%m-%dT%H:%M:%S+07:00")


# ── Playwright fetch ──────────────────────────────────────────────────────────

def fetch_list_pages(pages: int) -> list[dict]:
    today = datetime.now(BANGKOK_TZ)
    start = (today - timedelta(days=7)).strftime("%d/%m/%Y")
    end = today.strftime("%d/%m/%Y")

    items: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(LIST_URL, timeout=30000)
        page.wait_for_timeout(3000)

        client_id = page.evaluate("window.__NUXT__.state.clientId")
        if not client_id:
            print("  ⚠️ ไม่พบ clientId ใน __NUXT__ state - หน้าอาจโดนบล็อกหรือโครงสร้างเปลี่ยน")
            browser.close()
            return items

        for page_index in range(pages):
            url = (
                f"/api/cms/v1/research-settrade/search"
                f"?cate={CATE_UUID}&subCate={SUBCATE_UUID}"
                f"&startDate={start.replace('/', '%2F')}&endDate={end.replace('/', '%2F')}"
                f"&pageIndex={page_index}&pageSize={PAGE_SIZE}"
            )
            result = page.evaluate(
                """async (args) => {
                    const [url, clientId] = args;
                    const r = await fetch(url, {
                        headers: {
                            'x-client-uuid': clientId,
                            'x-channel': 'WEB_SETTRADE',
                            'accept': 'application/json, text/plain, */*',
                        },
                    });
                    return { status: r.status, text: r.ok ? await r.text() : null };
                }""",
                [url, client_id],
            )
            if result["status"] != 200 or not result["text"]:
                print(f"  ⚠️ page {page_index}: HTTP {result['status']} - ข้าม")
                continue
            data = json.loads(result["text"])
            page_items = (data.get("researchItems") or {}).get("items") or []
            print(f"  ✓ page {page_index}: {len(page_items)} items")
            items.extend(page_items)

        browser.close()

    return items


def fetch_pdf_links(uuids_to_urls: dict[str, str]) -> dict[str, str | None]:
    """เปิดหน้า detail ทีละชิ้น (เฉพาะ uuid ที่ยังไม่เคยมีใน archive) ดึง fileUrl
    (ลิงก์ PDF) จาก window.__NUXT__ state - ไม่มี API แยกสำหรับหน้า detail
    (probe แล้ว 2026-07-14: ไม่เจอ XHR อื่นนอกจาก page navigation เอง)
    """
    out: dict[str, str | None] = {}
    if not uuids_to_urls:
        return out

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        for uuid, url in uuids_to_urls.items():
            try:
                page.goto(url, timeout=20000)
                page.wait_for_timeout(1500)
                file_url = page.evaluate(
                    """() => {
                        function find(obj, seen) {
                            if (!obj || typeof obj !== 'object' || seen.has(obj)) return null;
                            seen.add(obj);
                            if (obj.researchDetail && obj.researchDetail.fileUrl) return obj.researchDetail.fileUrl;
                            for (const k in obj) {
                                const r = find(obj[k], seen);
                                if (r) return r;
                            }
                            return null;
                        }
                        return find(window.__NUXT__ ? window.__NUXT__.state : null, new Set());
                    }"""
                )
                out[uuid] = file_url
            except Exception as e:
                print(f"  ⚠️ detail fetch failed for {uuid}: {e}")
                out[uuid] = None
        browser.close()

    return out


def normalize_item(raw: dict, file_url: str | None) -> dict:
    title = raw.get("title", "").strip()
    tickers = [t.strip() for t in (raw.get("symbol") or "").split(",") if t.strip()]
    return {
        "uuid": raw.get("uuid"),
        "title": title,
        "url": raw.get("url"),
        "tickers": tickers,
        "symbolId": raw.get("symbolId"),
        "broker": raw.get("source"),
        "rating": extract_rating(title),
        "companyName": extract_company_name(title),
        "date": raw.get("stratDate"),
        "ts": int(datetime.fromisoformat(raw["stratDate"]).timestamp() * 1000) if raw.get("stratDate") else 0,
        "fileUrl": file_url,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="fetch and print counts, write nothing")
    parser.add_argument("--out", default=None, help="write into this file instead of the default output path")
    parser.add_argument("--pages", type=int, default=3, help="number of list pages to fetch (default 3)")
    args = parser.parse_args()

    out_path = Path(args.out) if args.out else DEFAULT_OUT
    if args.out:
        print(f"[--out] Writing into {out_path} instead of the default path")
    if args.dry_run:
        print("[--dry-run] Fetching only - nothing will be written")

    print(f"📊 [IAA] กำลังดึงบทวิเคราะห์จาก SETTrade ({args.pages} หน้า)...")
    raw_items = fetch_list_pages(args.pages)
    print(f"  รวม {len(raw_items)} รายการจาก list API")

    # โหลด archive เดิม (ถ้ามี) เพื่อ merge กัน gap และรู้ว่า uuid ไหนใหม่จริง
    existing: dict[str, dict] = {}
    if out_path.exists():
        try:
            old = json.loads(out_path.read_text(encoding="utf-8"))
            for it in old.get("items", []):
                if it.get("uuid"):
                    existing[it["uuid"]] = it
        except Exception as e:
            print(f"  ⚠️ อ่าน archive เดิมไม่ได้ ({e}) - เริ่มใหม่")

    new_uuids_to_urls = {
        raw["uuid"]: raw["url"]
        for raw in raw_items
        if raw.get("uuid") and raw["uuid"] not in existing
    }
    print(f"  พบใหม่ {len(new_uuids_to_urls)} รายการ (ที่เหลือมีใน archive แล้ว)")

    file_urls: dict[str, str | None] = {}
    if new_uuids_to_urls and not args.dry_run:
        print(f"  📄 กำลังดึงลิงก์ PDF จากหน้า detail ({len(new_uuids_to_urls)} หน้า)...")
        file_urls = fetch_pdf_links(new_uuids_to_urls)

    merged: dict[str, dict] = dict(existing)
    for raw in raw_items:
        uuid = raw.get("uuid")
        if not uuid:
            continue
        file_url = file_urls.get(uuid) if uuid in new_uuids_to_urls else existing.get(uuid, {}).get("fileUrl")
        merged[uuid] = normalize_item(raw, file_url)

    items = sorted(merged.values(), key=lambda x: x.get("ts", 0), reverse=True)

    out = {
        "generated_at": now_iso(),
        "source": "SETTrade IAA (ศูนย์รวมบทวิเคราะห์)",
        "note": "ข้อมูลจาก settrade.com/th/research/analyst-research - ไม่มีการดาวน์โหลด/เก็บไฟล์ PDF ใดๆ มีแค่ลิงก์ไปต้นทาง",
        "items": items,
    }

    if args.dry_run:
        print(f"\n🎉 [IAA] (dry-run) จะได้ {len(items)} รายการสะสม (เดิม {len(existing)} + ใหม่ {len(new_uuids_to_urls)})")
        for it in items[:3]:
            print(f"  - {it['date']} | {it['rating']} | {it['broker']} | {it['tickers']} | {it['title'][:60]}")
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n🎉 [IAA] เสร็จสิ้น: {len(items)} รายการสะสม (เดิม {len(existing)} + ใหม่ {len(new_uuids_to_urls)}) -> {out_path}")
    print(f"   ขนาดไฟล์: {out_path.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
