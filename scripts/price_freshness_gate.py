"""
price_freshness_gate.py — ด่านก่อน push ขึ้นเว็บ (daily-scan.yml step "Price freshness gate")

อ่าน data/meta/price_freshness.json ที่ 2_download_history.py เขียนไว้ (tools/core/price_freshness.py):
  exit 1 (job แดง · ขั้น copy / push ถัดไปถูกข้าม · เว็บคงข้อมูลรอบก่อน) เมื่อ
    - ใช้ราคาเก่าแทน > 2% ของหุ้น หรือ yahoo_outage_suspected = true
    - ไม่มีไฟล์ (ขั้นราคาไม่จบรอบนี้ — ไม่รู้ว่าราคาสดไหม จึงไม่ push · ไฟล์ถูกลบตอนเริ่มขั้นราคา
      ไฟล์เก่าจึงไม่ค้างมาหลอก)
  exit 0 เมื่อผ่าน (1–2% ยังผ่าน แต่พิมพ์ ::warning::)

หุ้นที่ถูกตัดประจำ (dropped, 5–7 ตัว) ไม่นับในเกณฑ์ — นับเฉพาะ "ใช้ราคาเก่าแทน"
วันตลาดไม่เปิด (NON_TRADING_DAY) workflow ข้าม step นี้ทั้งก้อนด้วย `if` → ไม่ทำให้ job แดงในวันหยุด

Usage:
    python scripts/price_freshness_gate.py [--file data/meta/price_freshness.json]
"""

import argparse
import sys
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))
from config import PRICE_FRESHNESS_FILE  # noqa: E402
from tools.core.price_freshness import FAIL, WARN, judge, read_payload  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def run(path: Path) -> int:
    payload = read_payload(path)
    if payload is None:
        print(f"::error::Price freshness gate: ไม่พบ/อ่านไม่ได้ {path} — ขั้นดาวน์โหลดราคา (2_download_history.py) "
              "ไม่จบรอบนี้ จึงไม่รู้ว่าราคาสดไหม · ไม่ push ขึ้นเว็บ (เว็บคงข้อมูลรอบก่อน)")
        return 1
    level, message = judge(payload)
    if level == FAIL:
        print(f"::error::Price freshness gate: {message}")
        return 1
    if level == WARN:
        print(f"::warning::Price freshness gate: {message}")
    else:
        print(f"Price freshness gate: {message}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", type=Path, default=PRICE_FRESHNESS_FILE)
    return run(ap.parse_args(argv).file)


if __name__ == "__main__":
    sys.exit(main())
