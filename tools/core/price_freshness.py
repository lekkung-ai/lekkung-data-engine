"""
price_freshness.py — สรุป "ราคาหุ้นรอบนี้สดแค่ไหน" ที่ 2_download_history.py เขียนทิ้งไว้ให้ด่านก่อน push
(scripts/price_freshness_gate.py ใน daily-scan.yml) และ health_check H14 อ่านต่อ

ที่มา: ราคาหุ้นพึ่ง Yahoo แหล่งเดียว ถ้า Yahoo ล่ม ตัวที่ดึงไม่ได้จะใช้ไฟล์ราคาเมื่อวานแทน (fallback)
แล้ว pipeline ก็รันต่อ ติดเวลาสร้างข้อมูลเป็น "วันนี้" แล้ว push ขึ้นเว็บ — ป้ายเตือนบนเว็บดูแค่เวลาสร้างไฟล์
เลยไม่เตือนอะไร (audit 2026-10-23) ไฟล์นี้ทำให้ "จำนวนตัวที่ใช้ราคาเก่าแทน" เป็นตัวเลขที่ตรวจได้

ไฟล์: data/meta/price_freshness.json (config.PRICE_FRESHNESS_FILE · ไม่ commit · เขียนทุกรอบหลังขั้นราคาจบ
· ลบทิ้งตอนเริ่มขั้นราคา → ไม่มีไฟล์ = ขั้นราคาไม่จบรอบนี้)
  {
    "generated_at": "2026-10-23T18:05:00+07:00",
    "reference_date": "2026-10-22",       # แท่งล่าสุดของ SET_INDEX.csv (TradingView)
    "stock_data_date": "2026-10-22",      # แท่งล่าสุดในราคาหุ้นที่ดึงสดรอบนี้
    "universe_size": 873,
    "fresh_count": 866,
    "fallback_count": 0, "fallback_tickers": [],   # ใช้ไฟล์ราคาเก่าแทน
    "dropped_count": 7,  "dropped_tickers": [...],  # ถูกตัด (ไม่มีข้อมูลที่ใช้ได้ — ตัดประจำ 5–7 ตัว)
    "bulk_failed_count": 7,               # ดึงเป็นล็อตไม่ได้กี่ตัว (ก่อนดึงซ้ำทีละตัว)
    "retry_skipped": false,
    "yahoo_outage_suspected": false       # bulk_failed_count > config.PRICE_OUTAGE_BULK_FAILURES
  }

เกณฑ์ (สัดส่วนของ universe_size · เฉพาะ "ใช้ราคาเก่าแทน" — ตัวที่ถูกตัดไม่นับ เพราะไม่ได้ขึ้นเว็บอยู่แล้ว)
  ≤ 1%      ปกติ
  1% – 2%   WARN (H14) · ด่านปล่อยผ่าน
  > 2%      FAIL (H14) · ด่านไม่ push
  yahoo_outage_suspected = true → FAIL / ไม่ push เสมอ
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BANGKOK_TZ = timezone(timedelta(hours=7))

FALLBACK_WARN_SHARE = 0.01   # > 1% ของ universe ใช้ราคาเก่าแทน → WARN
FALLBACK_FAIL_SHARE = 0.02   # > 2% → FAIL (ด่านก่อน push ไม่ปล่อย)
EXAMPLE_COUNT = 10           # จำนวนชื่อหุ้นตัวอย่างในข้อความ error

OK, WARN, FAIL = "ok", "warn", "fail"


def build_payload(
    *,
    reference_date: Optional[str],
    stock_data_date: Optional[str],
    universe_size: int,
    fresh_count: int,
    fallback_tickers: List[str],
    dropped_tickers: List[str],
    bulk_failed_count: int,
    outage_suspected: bool,
    retry_skipped: bool,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    now = now or datetime.now(BANGKOK_TZ)
    fb, dr = sorted(set(fallback_tickers)), sorted(set(dropped_tickers))
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "reference_date": reference_date,
        "stock_data_date": stock_data_date,
        "universe_size": int(universe_size),
        "fresh_count": int(fresh_count),
        "fallback_count": len(fb),
        "fallback_tickers": fb,
        "dropped_count": len(dr),
        "dropped_tickers": dr,
        "bulk_failed_count": int(bulk_failed_count),
        "retry_skipped": bool(retry_skipped),
        "yahoo_outage_suspected": bool(outage_suspected),
    }


def write_payload(path: Path, payload: Dict[str, Any]) -> None:
    """เขียนแบบ atomic (tmp + os.replace) — ด่านที่อ่านพร้อมกันไม่เห็นไฟล์ครึ่งเดียว"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_payload(path: Path) -> Optional[Dict[str, Any]]:
    """None = ไม่มีไฟล์ / อ่านไม่ได้ / ไม่ใช่ object"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def fallback_share(payload: Dict[str, Any]) -> float:
    n = int(payload.get("universe_size") or 0)
    return (int(payload.get("fallback_count") or 0) / n) if n else 0.0


def _examples(payload: Dict[str, Any]) -> str:
    names = [str(t) for t in payload.get("fallback_tickers") or []]
    more = f" … (อีก {len(names) - EXAMPLE_COUNT} ตัว)" if len(names) > EXAMPLE_COUNT else ""
    return ", ".join(names[:EXAMPLE_COUNT]) + more if names else "-"


def judge(payload: Dict[str, Any]) -> Tuple[str, str]:
    """(ระดับ OK/WARN/FAIL, ข้อความภาษาไทยที่ผู้ใช้เห็นใน Actions)"""
    n_fb = int(payload.get("fallback_count") or 0)
    universe = int(payload.get("universe_size") or 0)
    share = fallback_share(payload)
    summary = (f"ใช้ราคาเก่าแทน {n_fb}/{universe} ตัว ({share:.1%}) · ดึงสดได้ {payload.get('fresh_count')} ตัว · "
               f"แท่งล่าสุดของหุ้น {payload.get('stock_data_date')} · SET_INDEX {payload.get('reference_date')}")
    if payload.get("yahoo_outage_suspected"):
        return FAIL, (
            f"Yahoo น่าจะล่ม: ดึงเป็นล็อตไม่ได้ {payload.get('bulk_failed_count')} ตัว "
            f"(เกินเกณฑ์) จึงข้ามการดึงซ้ำทีละตัว · {summary} · ตัวอย่างที่ใช้ราคาเก่า: {_examples(payload)} "
            f"· ไม่ push ขึ้นเว็บ เพื่อไม่ให้ราคาเก่าถูกแสดงเป็นราคาวันนี้ (เว็บคงข้อมูลรอบก่อน)")
    if share > FALLBACK_FAIL_SHARE:
        return FAIL, (
            f"ราคาค้างเกินเกณฑ์ {FALLBACK_FAIL_SHARE:.0%}: {summary} · ตัวอย่าง 10 ตัวแรก: {_examples(payload)} "
            f"· ไม่ push ขึ้นเว็บ เพื่อไม่ให้ราคาเก่าถูกแสดงเป็นราคาวันนี้ (เว็บคงข้อมูลรอบก่อน)")
    if share > FALLBACK_WARN_SHARE:
        return WARN, (
            f"ราคาค้างเล็กน้อย (เกิน {FALLBACK_WARN_SHARE:.0%} แต่ไม่เกิน {FALLBACK_FAIL_SHARE:.0%}): {summary} "
            f"· ตัวอย่าง: {_examples(payload)}")
    return OK, f"ราคาสดปกติ: {summary}"
