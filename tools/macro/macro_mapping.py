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
        "tickers": [],
    },
    "NG=F": {
        "name_th": "ก๊าซธรรมชาติ",
        "name_en": "Natural Gas (Henry Hub)",
        "unit": "USD/MMBtu",
        "zone": "energy",
        "tickers": ["GULF", "BGRIM", "GPSC"],
    },
    "HRC=F": {
        "name_th": "เหล็กแผ่นม้วนร้อน",
        "name_en": "US HRC Steel Futures",
        "unit": "USD/ตัน",
        "zone": "industrial",
        "tickers": ["TSTH", "TMT", "BSBM", "PAP", "MILL"],
    },
    "SB=F": {
        "name_th": "น้ำตาลทรายดิบ #11",
        "name_en": "Sugar #11",
        "unit": "เซนต์/ปอนด์",
        "zone": "agri",
        "tickers": ["KSL", "KTIS", "KBS", "BRR"],
    },
    "ZC=F": {
        "name_th": "ข้าวโพดเลี้ยงสัตว์",
        "name_en": "Corn Futures",
        "unit": "เซนต์/บุชเชล",
        "zone": "agri",
        "tickers": ["CPF", "TFG", "GFPT"],
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
    "ZW=F": {
        "name_th": "ข้าวสาลี",
        "name_en": "Wheat Futures",
        "unit": "เซนต์/บุชเชล",
        "zone": "agri",
        "tickers": ["NSL", "PB", "SNNP", "TFM"],
    },
    "CPO=F": {
        "name_th": "น้ำมันปาล์มดิบ (มาเลเซีย)",
        "name_en": "Malaysian Crude Palm Oil",
        "unit": "USD/เมตริกตัน",
        "zone": "agri",
        "tickers": ["UVAN", "UPOIC", "VPO"],
    },
    "ZL=F": {
        "name_th": "น้ำมันถั่วเหลือง",
        "name_en": "Soybean Oil",
        "unit": "เซนต์/ปอนด์",
        "zone": "agri",
        "tickers": [],
    },
    "HG=F": {
        "name_th": "ทองแดง",
        "name_en": "Copper Futures",
        "unit": "USD/ปอนด์",
        "zone": "industrial",
        "tickers": ["KCE", "HANA", "DELTA", "SVI"],
    },
    "ALI=F": {
        "name_th": "อลูมิเนียม",
        "name_en": "Aluminum Futures",
        "unit": "USD/เมตริกตัน",
        "zone": "industrial",
        "tickers": ["CBG", "OSP"],
    },
    "GC=F": {
        "name_th": "ทองคำ",
        "name_en": "Gold",
        "unit": "USD/ออนซ์",
        "zone": "financial",
        "tickers": [],
    },
    "THB=X": {
        "name_th": "อัตราแลกเปลี่ยน ดอลลาร์/บาท",
        "name_en": "USD/THB",
        "unit": "บาท/ดอลลาร์",
        "zone": "financial",
        "tickers": ["DELTA", "KCE", "HANA", "AOT"],
    },
    "DX-Y.NYB": {
        "name_th": "ดัชนีดอลลาร์สหรัฐ",
        "name_en": "US Dollar Index (DXY)",
        "unit": "ดัชนี",
        "zone": "financial",
        "tickers": ["DELTA", "KCE", "HANA"],
    },
    "^TNX": {
        "name_th": "อัตราผลตอบแทนพันธบัตร US 10 ปี",
        "name_en": "US 10-Year Treasury Yield",
        "unit": "%",
        "zone": "financial",
        "tickers": ["TLI", "BLA", "KBANK", "BBL", "SCB"],
    },
    "BDRY": {
        "name_th": "ค่าระวางเรือสินค้าแห้ง (BDI ETF)",
        "name_en": "Dry Bulk Freight ETF (BDRY)",
        "unit": "USD/หุ้น",
        "zone": "financial",
        "tickers": ["PSL", "TTA"],
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
    "industrial": "โลหะ-อุตสาหกรรม",
    "financial": "การเงิน-ดอกเบี้ย",
}
