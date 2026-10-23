"""
set_holidays.py — วันหยุดทำการของ SET ที่ "ประกาศไว้ล่วงหน้า" ใช้แยกว่า NON_TRADING_DAY วันจันทร์–ศุกร์
เป็นวันหยุดจริง หรืออาจเป็นแหล่งข้อมูลล่ม (run_all.py ตัดสินจากข้อมูลล่าสุดอย่างเดียว ถ้า Yahoo + TradingView
ล่มพร้อมกัน ก็ดูเหมือนตลาดปิด แล้วข้ามทั้งรอบแบบ job เขียว)

ใช้เพื่อ "เตือน" เท่านั้น — ไม่ใช้ตัดสินว่าตลาดเปิดไหม (นั่นคือ trading_calendar.is_non_trading_day)

ที่มา 2026: SET Calendar 2026 (media.set.or.th/set/Documents/2025/Nov/SET-Calendar-2026-EN.pdf) +
วันหยุดพิเศษที่ประกาศภายหลัง (ยืนยันจาก SET_INDEX ไม่มีแท่งวันนั้น) ระบุไว้ใน SPECIAL
ต้องเพิ่มรายการของปีถัดไปเองเมื่อ SET ออกปฏิทิน — ปีที่ไม่มีในรายการจะเตือนทุกวันทำการที่ข้ามรอบ
(ดีกว่าเงียบ) และข้อความบอกว่ายังไม่มีรายการปีนั้น
"""

from datetime import date
from typing import Dict, FrozenSet, Optional

_HOLIDAYS: Dict[int, FrozenSet[date]] = {
    2026: frozenset(
        date(2026, m, d)
        for m, d in [
            (1, 1),    # วันขึ้นปีใหม่
            (1, 2),    # วันหยุดพิเศษเพิ่มเติม
            (3, 3),    # วันมาฆบูชา
            (4, 6),    # วันจักรี
            (4, 13), (4, 14), (4, 15),  # สงกรานต์
            (5, 1),    # วันแรงงานแห่งชาติ
            (5, 4),    # วันฉัตรมงคล
            (5, 13),   # วันพืชมงคล
            (6, 1),    # ชดเชยวันวิสาขบูชา (31 พ.ค. ตรงวันอาทิตย์)
            (6, 3),    # วันเฉลิมพระชนมพรรษาสมเด็จพระราชินีสุทิดา
            (7, 28),   # วันเฉลิมพระชนมพรรษา ร.10
            (7, 29),   # วันอาสาฬหบูชา
            (7, 30),   # วันเข้าพรรษา
            (8, 12),   # วันเฉลิมพระชนมพรรษาสมเด็จพระพันปีหลวง / วันแม่
            (10, 13),  # วันคล้ายวันสวรรคต ร.9
            (10, 23),  # วันปิยมหาราช
            (12, 7),   # ชดเชยวันคล้ายวันพระบรมราชสมภพ ร.9 (5 ธ.ค. ตรงวันเสาร์)
            (12, 10),  # วันรัฐธรรมนูญ
            (12, 31),  # วันสิ้นปี
        ]
    ),
}

# วันหยุดพิเศษที่ประกาศหลังปฏิทินออก — ใส่เหตุผล/หลักฐานไว้ตรงนี้
SPECIAL: Dict[date, str] = {
    date(2026, 10, 16): "SET ปิดทำการ (SET_INDEX ไม่มีแท่ง · daily-scan 2026-10-16 ข้ามรอบเป็น NON_TRADING_DAY)",
}


def is_set_holiday(d: date) -> bool:
    return d in _HOLIDAYS.get(d.year, frozenset()) or d in SPECIAL


def has_calendar(year: int) -> bool:
    return year in _HOLIDAYS


def non_trading_warning(today: date) -> Optional[str]:
    """ข้อความเตือนเมื่อ NON_TRADING_DAY ตรงกับวันจันทร์–ศุกร์ที่ไม่อยู่ในรายการวันหยุด SET · ไม่ต้องเตือน → None"""
    if today.weekday() >= 5 or is_set_holiday(today):
        return None
    note = "" if has_calendar(today.year) else f" (ยังไม่มีรายการวันหยุดปี {today.year} ใน tools/core/set_holidays.py)"
    return (f"NON_TRADING_DAY ในวัน{['จันทร์', 'อังคาร', 'พุธ', 'พฤหัสบดี', 'ศุกร์'][today.weekday()]}ที่ {today.isoformat()} "
            f"ซึ่งไม่อยู่ในรายการวันหยุด SET — อาจเป็นแหล่งข้อมูลล่ม (Yahoo + TradingView) ไม่ใช่วันหยุด "
            f"ถ้าตลาดเปิดจริงให้รัน workflow ใหม่ (ถ้าเป็นวันหยุดพิเศษที่ประกาศภายหลัง ให้เพิ่มใน SPECIAL){note}")
