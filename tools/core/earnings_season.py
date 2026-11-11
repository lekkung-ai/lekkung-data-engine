"""
earnings_season.py — ช่วง "ฤดูงบ" (วันที่บริษัทจดทะเบียนทยอยส่งงบ) ค่าคงที่จุดเดียว

ใช้โดย iaa-research-morning.yml และ earnings-evening.yml: นอกฤดูงบข้ามขั้นดึงงบ/push ทั้งหมดแล้วจบเขียว
(daily-scan.yml ไม่ใช้ไฟล์นี้ — ยังดึงงบทุกวันทำการตลอดปี)

กำหนดส่งงบของ SET (ข้อบังคับตลาดหลักทรัพย์ฯ เรื่องการส่งงบการเงิน):
  - งบรายไตรมาส (Q1–Q3): ภายใน 45 วันนับจากวันสิ้นไตรมาส → Q1 ≈ 15 พ.ค. · Q2 ≈ 14 ส.ค. · Q3 ≈ 14 พ.ย.
  - งบประจำปี: ภายใน 3 เดือนนับจากวันสิ้นรอบปีบัญชี (ปีบัญชี 31 ธ.ค.) → 31 มี.ค.
  - ส่งงบ Q4 แยกต่างหากไม่มี — ปีบัญชีนับเป็นงบปี
ช่วงที่ตั้งไว้เผื่อบริษัทที่ส่งก่อนกำหนดและส่งช้า/แก้ไขงบหลังกำหนดไว้ประมาณ 1 สัปดาห์:
  งบปี 1 ก.พ. – 31 มี.ค. · Q1 15 เม.ย. – 20 พ.ค. · Q2 15 ก.ค. – 20 ส.ค. · Q3 15 ต.ค. – 20 พ.ย.
  (รวมวันแรกและวันสุดท้าย · ปีบัญชีที่ไม่ใช่ 31 ธ.ค. ส่งงบนอกช่วงนี้ได้ — ไปได้รอบ daily-scan หรือสั่งรันมือด้วย ignore_season)

ช่วงเก็บเป็น (เดือน, วัน) ไม่ผูกปี ใช้ได้ทุกปี รวมปีอธิกสุรทิน (29 ก.พ. อยู่ในช่วงงบปีเสมอ)
เปลี่ยนช่วงที่นี่ที่เดียว — workflow ทั้งสองอ่านจากไฟล์นี้
"""

from datetime import date, datetime, timedelta, timezone
from typing import Optional, Tuple

BANGKOK = timezone(timedelta(hours=7))  # Asia/Bangkok = UTC+7 ตลอดปี (ไม่มี DST)

# (ชื่อ, (เดือน, วัน) เริ่ม, (เดือน, วัน) สิ้นสุด) — รวมทั้งสองปลาย
EARNINGS_SEASONS: Tuple[Tuple[str, Tuple[int, int], Tuple[int, int]], ...] = (
    ("งบปี", (2, 1), (3, 31)),
    ("Q1", (4, 15), (5, 20)),
    ("Q2", (7, 15), (8, 20)),
    ("Q3", (10, 15), (11, 20)),
)


def bangkok_today(now: Optional[datetime] = None) -> date:
    """วันที่ปัจจุบันตามเวลา Asia/Bangkok (now ต้องมี tzinfo ถ้าส่งมาเอง · ไม่ส่ง = เวลาปัจจุบัน)"""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now ต้องมี tzinfo — วันที่ตามเวลาไทยคำนวณจากเวลา UTC ไม่ได้ถ้าไม่รู้เขตเวลา")
    return now.astimezone(BANGKOK).date()


def earnings_season_name(d: date) -> Optional[str]:
    """ชื่อฤดูงบที่วันที่นี้อยู่ ('งบปี'/'Q1'/'Q2'/'Q3') · นอกฤดู → None
    d = วันที่ตามเวลาไทยแล้ว (ใช้ bangkok_today())"""
    md = (d.month, d.day)
    for name, start, end in EARNINGS_SEASONS:
        if start <= md <= end:
            return name
    return None


def is_earnings_season(d: date) -> bool:
    return earnings_season_name(d) is not None


def season_gate(today: date, ignore_season: bool = False) -> Tuple[bool, str]:
    """ผลตัดสินของขั้นเช็คฤดูงบใน workflow → (skip, ข้อความสำหรับ log/summary)
    ignore_season = สั่งรันมือนอกฤดู (ข้ามเฉพาะเช็คฤดู — ไม่เกี่ยวกับเช็ควันหยุด)"""
    name = earnings_season_name(today)
    if name:
        return False, f"{today}: อยู่ในฤดูงบ ({name}) - รันดึงงบตามปกติ"
    if ignore_season:
        return False, f"{today}: นอกฤดูงบ แต่สั่ง ignore_season - รันดึงงบ"
    return True, f"{today}: นอกฤดูงบ - ข้ามขั้นดึงงบ/push ทั้งหมด (ไม่ดึง SET ไม่ push) · สั่งรันมือด้วย ignore_season=true ได้"
