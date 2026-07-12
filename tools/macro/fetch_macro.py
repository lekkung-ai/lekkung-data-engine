"""
fetch_macro.py
ดึงราคาปิดรายวัน 180 วันย้อนหลัง + ล่าสุด + %1D + %1M ของ commodity/FX futures
ที่ผูกกับหุ้นไทยกลุ่มพลังงาน/เกษตร/การเงิน ผ่าน yfinance

เขียนผลเป็น macro_commodities.json ไปที่ data/results/output/
(pipeline เดิม - update_stockdesk.bat และ daily-scan.yml copy *.json จากโฟลเดอร์นี้
ไป stockdesk/data/scans/ อยู่แล้ว ไม่ต้องแก้ path เพิ่ม)

รันเป็นส่วนหนึ่งของ run_all.py scan_scripts (ต่อจาก calculate_breadth.py)

หมายเหตุปาล์มน้ำมัน: ดู macro_mapping.py - probe พบว่า CPO=F มีข้อมูลจริงบน
Yahoo Finance จึงไม่ต้องใช้ ZL=F เป็น proxy ตามแผนเดิม

Rate limit: ดึงทีละ symbol ช้าๆ (sleep ระหว่างตัว) ตามคำเตือนเรื่อง Yahoo -
มีแค่ 10 symbol ต่อรอบ ความเสี่ยงโดนบล็อกต่ำอยู่แล้ว
"""
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import json
from datetime import datetime, timezone, timedelta

import yfinance as yf

# 🔌 เชื่อมต่อ config.py (tools/macro/fetch_macro.py -> parents[2] = root)
sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import RESULTS_DIR  # type: ignore

from macro_mapping import COMMODITIES, ZONE_LABELS  # type: ignore

OUTPUT_DIR = RESULTS_DIR / "output"
BKK_TZ = timezone(timedelta(hours=7))
HISTORY_PERIOD = "1y"      # ดึงเผื่อยาว แล้ว tail เอา 180 วันล่าสุด (yfinance ไม่รับ period="180d" ตรงๆ)
SERIES_DAYS = 180
FETCH_SLEEP_SEC = 1.5      # หน่วงระหว่าง symbol กัน Yahoo rate limit
PCT_1M_TRADING_DAYS = 21   # ~1 เดือนปฏิทิน = ~21 trading days (ธรรมเนียมทั่วไป)


def now_iso() -> str:
    return datetime.now(BKK_TZ).strftime("%Y-%m-%dT%H:%M:%S+07:00")


def fetch_symbol(symbol: str) -> dict | None:
    try:
        hist = yf.Ticker(symbol).history(period=HISTORY_PERIOD, interval="1d")
    except Exception as e:
        print(f"     ⚠️ error: {e}")
        return None

    hist = hist.dropna(subset=["Close"])
    if hist.empty:
        print("     ⚠️ no data returned")
        return None

    tail = hist.tail(SERIES_DAYS)
    closes = tail["Close"]

    series = [
        {"date": d.strftime("%Y-%m-%d"), "close": round(float(c), 4)}
        for d, c in closes.items()
    ]

    latest_close = float(closes.iloc[-1])
    latest_date = tail.index[-1].strftime("%Y-%m-%d")

    pct_1d = None
    if len(closes) >= 2:
        prev = float(closes.iloc[-2])
        if prev:
            pct_1d = round((latest_close - prev) / prev * 100, 2)

    pct_1m = None
    if len(closes) > PCT_1M_TRADING_DAYS:
        ref = float(closes.iloc[-1 - PCT_1M_TRADING_DAYS])
        if ref:
            pct_1m = round((latest_close - ref) / ref * 100, 2)

    return {
        "latest": {"date": latest_date, "close": round(latest_close, 4)},
        "pct_1d": pct_1d,
        "pct_1m": pct_1m,
        "series": series,
    }


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    symbols = list(COMMODITIES.items())
    print(f"🌾 [Macro] กำลังดึงราคา commodity/FX {len(symbols)} รายการ ผ่าน yfinance...")
    out_commodities: dict[str, dict] = {}

    for i, (symbol, meta) in enumerate(symbols):
        print(f"  ⏳ ({i + 1}/{len(symbols)}) {symbol} ({meta['name_en']})...")
        data = fetch_symbol(symbol)
        if data is None:
            continue
        out_commodities[symbol] = {**meta, **data}
        print(
            f"     ✓ latest={data['latest']['close']} ({data['latest']['date']}) "
            f"1D={data['pct_1d']} 1M={data['pct_1m']} series={len(data['series'])} วัน"
        )
        if i < len(symbols) - 1:
            time.sleep(FETCH_SLEEP_SEC)

    out = {
        "generated_at": now_iso(),
        "series_days": SERIES_DAYS,
        "zones": ZONE_LABELS,
        "methodology": (
            f"pct_1d = เทียบราคาปิดล่าสุดกับวันก่อนหน้า 1 trading day. "
            f"pct_1m = เทียบราคาปิดล่าสุดกับ {PCT_1M_TRADING_DAYS} trading days ก่อนหน้า "
            "(ประมาณ 1 เดือนปฏิทิน). ราคาทั้งหมดมาจาก Yahoo Finance ณ เวลาที่รัน pipeline "
            "รายวัน (close ล่าสุดที่ Yahoo มี ไม่ใช่ real-time)"
        ),
        "palm_oil_note": (
            "ใช้ CPO=F (USD Malaysian Crude Palm Oil, CME) เป็นข้อมูลจริง - probe แล้วพบว่ามี "
            "ข้อมูลบน Yahoo Finance จริง (verified 2026-07-12) ไม่ต้องใช้ ZL=F (soybean oil) "
            "เป็น proxy ตามแผนเดิม"
        ),
        "commodities": out_commodities,
    }

    out_path = OUTPUT_DIR / "macro_commodities.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"\n🎉 [Macro] เสร็จสิ้น: {len(out_commodities)}/{len(symbols)} รายการ -> {out_path}"
    )
    print(f"   ขนาดไฟล์: {out_path.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
