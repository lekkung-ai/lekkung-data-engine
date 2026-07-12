"""
macro_mapping.py — commodity/FX metadata + which Thai stock tickers each one
affects. Single file, edit here to add/remove commodities or change mappings.

หมายเหตุปาล์มน้ำมัน: แผนเดิมตั้งใจใช้ ZL=F (soybean oil) เป็น proxy เพราะไม่คิดว่า
จะมีข้อมูลปาล์มจริงบน Yahoo — probe แล้วพบว่า CPO=F (USD Malaysian Crude Palm
Oil, CME) มีข้อมูลจริง (verified 2026-07-12, 165 แท่ง/200 วันปฏิทิน ไม่มีช่องว่าง)
จึงย้าย mapping ปาล์ม (UVAN/UPOIC/VPO) ไปผูกกับ CPO=F แทน ZL=F เหลือแค่ข้อมูล
ถั่วเหลืองเฉยๆ ไม่มี ticker ผูกแยก (ไม่ได้อยู่ในตารางที่ผู้ใช้ให้)
"""

COMMODITIES = {
    "BZ=F": {
        "name_th": "น้ำมันดิบเบรนท์",
        "name_en": "Brent Crude Oil",
        "unit": "USD/บาร์เรล",
        "zone": "energy",
        "tickers": ["PTT", "PTTEP", "TOP", "SPRC", "BCP"],
    },
    "CL=F": {
        "name_th": "น้ำมันดิบ WTI",
        "name_en": "WTI Crude Oil",
        "unit": "USD/บาร์เรล",
        "zone": "energy",
        "tickers": [],  # ไม่มีในตาราง mapping ที่ผู้ใช้ให้ - แสดงเป็นข้อมูลเฉยๆ ไม่มี chip หุ้น
    },
    "NG=F": {
        "name_th": "ก๊าซธรรมชาติ",
        "name_en": "Natural Gas (Henry Hub)",
        "unit": "USD/MMBtu",
        "zone": "energy",
        "tickers": ["GULF", "BGRIM", "GPSC"],
    },
    "SB=F": {
        "name_th": "น้ำตาลทรายดิบ #11",
        "name_en": "Sugar #11",
        "unit": "เซนต์/ปอนด์",
        "zone": "agri",
        "tickers": ["KSL", "KTIS", "KBS", "BRR"],
    },
    "ZS=F": {
        "name_th": "ถั่วเหลือง",
        "name_en": "Soybean",
        "unit": "เซนต์/บุชเชล",
        "zone": "agri",
        "tickers": ["CPF", "TFG", "GFPT", "TVO"],
    },
    "ZM=F": {
        "name_th": "กากถั่วเหลือง",
        "name_en": "Soybean Meal",
        "unit": "USD/short ton",
        "zone": "agri",
        "tickers": ["CPF", "TFG", "GFPT", "TVO"],
    },
    "ZL=F": {
        "name_th": "น้ำมันถั่วเหลือง",
        "name_en": "Soybean Oil",
        "unit": "เซนต์/ปอนด์",
        "zone": "agri",
        "tickers": [],  # เดิมกันไว้เป็น palm proxy - ไม่ต้องใช้แล้ว ดู docstring ด้านบน
    },
    "GC=F": {
        "name_th": "ทองคำ",
        "name_en": "Gold",
        "unit": "USD/ออนซ์",
        "zone": "financial",
        "tickers": [],  # ไม่มีในตาราง mapping ที่ผู้ใช้ให้ - แสดงเป็นข้อมูลเฉยๆ ไม่มี chip หุ้น
    },
    "THB=X": {
        "name_th": "อัตราแลกเปลี่ยน ดอลลาร์/บาท",
        "name_en": "USD/THB",
        "unit": "บาท/ดอลลาร์",
        "zone": "financial",
        "tickers": ["DELTA", "KCE", "HANA", "AOT"],
    },
    "CPO=F": {
        "name_th": "น้ำมันปาล์มดิบ (มาเลเซีย)",
        "name_en": "Malaysian Crude Palm Oil",
        "unit": "USD/เมตริกตัน",
        "zone": "agri",
        "tickers": ["UVAN", "UPOIC", "VPO"],
    },
}

# ธนาคารทุกตัวใน sector Financials > Banking (อ้างอิง stockdesk/data/scans/
# sector_map.json ณ 2026-07-12) - แก้ตรงนี้ถ้ามีธนาคารเข้า/ออก sector
BANK_TICKERS = [
    "BAY", "BBL", "CIMBT", "CREDIT", "KBANK", "KKP",
    "KTB", "LHFG", "SCB", "TCAP", "TISCO", "TTB",
]

ZONE_LABELS = {
    "energy": "พลังงาน",
    "agri": "เกษตร-อาหาร",
    "financial": "การเงิน",
}
