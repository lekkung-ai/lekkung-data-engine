"""
scan_lekkung_daytrade.py — Lekkung Daytrade Scan (TradingView Momentum Screener)

เกณฑ์ตรงกับ TradingView Screener "Lekkung Scan" ทุกข้อ (markets thailand · timeframe Daily):
  type = stock · typespecs has common · is_primary = true
  close > 1 · close > EMA200 · change > 0 · volume > 1,000,000
  RSI7 > 50 · average_volume_10d_calc ใน [1,000,000 , 50,000,000]
  ADX (14) > 20 · Recommend.All >= 0.1 (Tech rating = Buy / Strong buy · Strong buy คือ > 0.5)
  เรียง change มาก → น้อย · range [0, 200]

ไม่ใช้ ADTV_MIN_MB / config.py (blast radius 7 scanners) · ไม่ใช้ไฟล์ราคาใน HISTORY_DIR —
ค่าทุกตัว (รวม EMA200 / RSI7 / ADX) มาจาก TradingView ตรงๆ เพื่อให้เทียบกับหน้า Screener ได้ 1:1
เรียก scanner.tradingview.com เองด้วย requests.post เหมือน scan_sepa.py (ไม่ใช้ tradingview-screener)

Output: data/results/output/lekkung_daytrade.json
    {"generated_at": "...+07:00", "universe_count": 875, "total_count": 12, "data": [ {Ticker, ...}, ... ]}
  key "data" + "Ticker" + "Close" ให้ compute_scan_days / history / diff และ health_check H8 อ่านได้เหมือนสแกนอื่น

── Safety ────────────────────────────────────────────────────────────────────────────────────
ผิดปกติทุกกรณี = ไม่เขียนไฟล์ + log ERROR + exit 1 → run_all.py จดชื่อไฟล์ลง FAILED_STEPS แล้วรันสแกนถัดไปต่อ
(pipeline ไม่ล้ม · ไฟล์เดิมใน stockdesk ยืนเพราะ cp ไม่มี source · ไม่เงียบ — job แดงท้ายรอบ):
  · HTTP != 200 / exception / response ไม่ใช่ {data: [...], totalCount: int} / แถวคอลัมน์ไม่ครบ
  · field ที่ใช้กรอง (close, EMA200, RSI7, ADX, ...) เป็น null ในแถวที่ผ่าน — TradingView ไม่ error เมื่อชื่อ
    field ผิด แต่คืน null เงียบๆ จึงต้องเช็กเอง · แถวที่ไม่ผ่านเกณฑ์เมื่อเช็กซ้ำฝั่งเรา = filter ถูกเมิน
  · sanity check universe: query ที่ 2 (type / typespecs / is_primary เท่านั้น) ต้องได้ totalCount >= 500
  · load_fund_tickers() ได้ set ว่าง (หา sector_map.json ไม่เจอ) → กรองกองทุน/REIT ไม่ได้ จึงไม่เขียน
ผล 0 ตัวตอนที่ API ตอบปกติ = valid → เขียน {"data": []} พร้อม generated_at (ตลาดหมีที่ไม่มีหุ้นผ่านเป็นเรื่องจริง)

ไม่ใช้ drop-guard 30% กับไฟล์นี้ (แบบ scan ที่เทียบจำนวนกับรอบก่อน): จำนวนหุ้นที่ผ่านเกณฑ์โมเมนตัมวันต่อวัน
แกว่งได้ 0 → หลายสิบตัวเป็นเรื่องปกติ — ใช้ sanity check universe ด้านบนแทน ซึ่งจับ
"API ตอบผิดปกติ" ได้โดยไม่ตีความ "ตลาดไม่มีหุ้นเข้าเกณฑ์" ว่าเป็นความผิดพลาด

ทุกตัวเลขผ่าน math.isfinite() (ไม่ผ่าน → null) · json.dumps(..., allow_nan=False)
"""

import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore

# 🔌 เชื่อมต่อ config.py (ใช้แค่ RESULTS_DIR — ไม่แตะ ADTV_MIN_MB)
sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import RESULTS_DIR  # type: ignore
from utils import load_fund_tickers  # type: ignore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("scan_lekkung_daytrade")

SCAN_URL = "https://scanner.tradingview.com/thailand/scan"
OUT_FILE = RESULTS_DIR / "output" / "lekkung_daytrade.json"
BANGKOK_TZ = timezone(timedelta(hours=7))

TIMEOUT = 15
MAX_TRIES = 3  # เท่ากับ fetch_topmover_ranking.py · scan_sepa.py ไม่ retry
RANGE_END = 200  # range [0, 200] ตามเกณฑ์ของ Screener
MIN_UNIVERSE = 500  # universe หุ้นไทย ~875 · ต่ำกว่านี้ = API ผิดปกติ ไม่ใช่ตลาดว่าง

# ลำดับคอลัมน์ = ลำดับ d[] ในผลลัพธ์
COLUMNS = [
    "name", "description", "close", "change", "volume", "relative_volume_10d_calc", "market_cap_basic",
    "price_earnings_ttm", "earnings_per_share_diluted_ttm", "earnings_per_share_diluted_yoy_growth_ttm",
    "dividends_yield_current", "AnalystRating", "Recommend.All", "EMA200", "RSI7", "ADX",
    "average_volume_10d_calc",
]
IDX = {c: i for i, c in enumerate(COLUMNS)}

# universe ฐาน (ใช้ทั้งใน sanity check และเป็นส่วนแรกของ filter หลัก)
BASE_FILTER = [
    {"left": "type", "operation": "equal", "right": "stock"},
    {"left": "typespecs", "operation": "has", "right": ["common"]},
    {"left": "is_primary", "operation": "equal", "right": True},
]
SCAN_FILTER = BASE_FILTER + [
    {"left": "close", "operation": "greater", "right": 1},
    {"left": "close", "operation": "greater", "right": "EMA200"},
    {"left": "change", "operation": "greater", "right": 0},
    {"left": "volume", "operation": "greater", "right": 1_000_000},
    {"left": "RSI7", "operation": "greater", "right": 50},
    {"left": "average_volume_10d_calc", "operation": "in_range", "right": [1_000_000, 50_000_000]},
    {"left": "ADX", "operation": "greater", "right": 20},
    {"left": "Recommend.All", "operation": "egreater", "right": 0.1},
]
# field ที่ใช้กรอง — ต้องไม่เป็น null ในทุกแถวที่ผ่าน (ถ้า null = TradingView เมินชื่อ field)
FILTER_FIELDS = ["name", "close", "change", "volume", "EMA200", "RSI7", "ADX", "Recommend.All",
                 "average_volume_10d_calc"]


class ScanError(Exception):
    """API / ข้อมูลผิดปกติ — main() จับแล้ว log ERROR, ไม่เขียนไฟล์, exit 1"""


def post_scan(payload: dict, what: str) -> dict:
    """POST ตรงไป scanner.tradingview.com · retry MAX_TRIES ครั้ง · คืน JSON ที่ตรวจโครงสร้างแล้ว"""
    last = "unknown"
    for attempt in range(1, MAX_TRIES + 1):
        try:
            res = requests.post(SCAN_URL, json=payload, timeout=TIMEOUT)
            if res.status_code != 200:
                last = f"HTTP {res.status_code}: {res.text[:200]}"
            else:
                body = res.json()
                total = body.get("totalCount") if isinstance(body, dict) else None
                if not isinstance(body, dict) or not isinstance(body.get("data"), list) \
                        or not isinstance(total, int) or isinstance(total, bool):
                    last = f"unexpected response shape: {str(body)[:200]}"
                else:
                    return body
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        logger.warning("%s attempt %d/%d failed: %s", what, attempt, MAX_TRIES, last)
        if attempt < MAX_TRIES:
            time.sleep(2 * attempt)
    raise ScanError(f"{what} failed after {MAX_TRIES} tries: {last}")


def num(v, nd: int = 4):
    """ตัวเลขจำกัด (finite) หรือ None — ไม่รับ bool / string / NaN / Infinity"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return round(f, nd) if math.isfinite(f) else None


def whole(v):
    f = num(v, 0)
    return None if f is None else int(f)


def meets_criteria(d: list) -> bool:
    """เช็กเกณฑ์ซ้ำฝั่งเรา — แถวที่ TradingView คืนมาต้องผ่านทุกข้อ ไม่งั้น filter ถูกเมิน"""
    g = lambda c: d[IDX[c]]  # noqa: E731
    return (
        g("close") > 1 and g("close") > g("EMA200") and g("change") > 0 and g("volume") > 1_000_000
        and g("RSI7") > 50 and 1_000_000 <= g("average_volume_10d_calc") <= 50_000_000
        and g("ADX") > 20 and g("Recommend.All") >= 0.1
    )


def build_row(d: list) -> dict:
    close, ema200 = num(d[IDX["close"]]), num(d[IDX["EMA200"]])
    above = None
    if close is not None and ema200 is not None and ema200 > 0:
        above = num((close / ema200 - 1) * 100)
    rating = d[IDX["AnalystRating"]]
    return {
        "Ticker": str(d[IDX["name"]]).strip().upper(),
        "Name": d[IDX["description"]] if isinstance(d[IDX["description"]], str) else None,
        "Close": close,
        "Change_Pct": num(d[IDX["change"]]),
        "Volume": whole(d[IDX["volume"]]),
        "Rel_Volume": num(d[IDX["relative_volume_10d_calc"]]),
        "Market_Cap": whole(d[IDX["market_cap_basic"]]),
        "PE_Ratio": num(d[IDX["price_earnings_ttm"]]),
        "EPS_TTM": num(d[IDX["earnings_per_share_diluted_ttm"]]),
        "EPS_Growth_YoY": num(d[IDX["earnings_per_share_diluted_yoy_growth_ttm"]]),
        "Div_Yield": num(d[IDX["dividends_yield_current"]]),
        "RSI7": num(d[IDX["RSI7"]]),
        "ADX": num(d[IDX["ADX"]]),
        "EMA200": ema200,
        "Pct_Above_EMA200": above,
        "Tech_Rating": num(d[IDX["Recommend.All"]]),  # -1..1 · >= 0.1 Buy · > 0.5 Strong buy
        "Analyst_Rating": rating if isinstance(rating, str) else None,  # ความเห็นนักวิเคราะห์ (คนละตัวกับ Tech_Rating)
        "Avg_Volume_10D": whole(d[IDX["average_volume_10d_calc"]]),
    }


def scan() -> dict:
    # 1) sanity check universe — ยิง query แยก ก่อนเชื่อผลสแกน
    universe = post_scan(
        {"markets": ["thailand"], "columns": ["name"], "filter": BASE_FILTER, "range": [0, 1]},
        "universe sanity check",
    )["totalCount"]
    if universe < MIN_UNIVERSE:
        raise ScanError(f"universe totalCount={universe} < {MIN_UNIVERSE} — TradingView ตอบผิดปกติ ไม่เขียนผล")
    logger.info("universe (stock / common / is_primary): %d", universe)

    # 2) สแกนจริง
    body = post_scan(
        {
            "markets": ["thailand"],
            "options": {"lang": "en"},
            "columns": COLUMNS,
            "filter": SCAN_FILTER,
            "sort": {"sortBy": "change", "sortOrder": "desc"},
            "range": [0, RANGE_END],
        },
        "lekkung daytrade scan",
    )
    total = body["totalCount"]
    raw = body["data"]
    if total > len(raw):
        logger.warning("totalCount=%d มากกว่าที่ได้ %d แถว (range [0,%d]) — ผลถูกตัด", total, len(raw), RANGE_END)

    fund_tickers = load_fund_tickers()
    if not fund_tickers:
        raise ScanError("load_fund_tickers() ว่าง (sector_map.json หาไม่เจอ?) — กรองกองทุน/REIT ไม่ได้ ไม่เขียนผล")

    rows, excluded_funds, seen = [], [], set()
    for item in raw:
        d = item.get("d") if isinstance(item, dict) else None
        if not isinstance(d, list) or len(d) != len(COLUMNS):
            raise ScanError(f"แถวคอลัมน์ไม่ครบ: {str(item)[:200]}")
        null_fields = [c for c in FILTER_FIELDS if d[IDX[c]] is None]
        if null_fields:
            raise ScanError(f"field {null_fields} เป็น null ในแถวที่ผ่าน filter ({d[0]}) — ชื่อ field เปลี่ยน?")
        wrong = [c for c in FILTER_FIELDS[1:] if isinstance(d[IDX[c]], bool) or not isinstance(d[IDX[c]], (int, float))
                 or not math.isfinite(d[IDX[c]])]
        if wrong:
            raise ScanError(f"field {wrong} ไม่ใช่ตัวเลขจำกัดใน {d[0]}")
        if not meets_criteria(d):
            raise ScanError(f"{d[0]} ไม่ผ่านเกณฑ์เมื่อเช็กซ้ำ — TradingView เมิน filter?")

        row = build_row(d)
        ticker = row["Ticker"]
        if ticker in fund_tickers:  # กองทุน/REIT ไม่เข้า scanner หุ้น (กฎกลาง is_fund_ticker ผ่าน load_fund_tickers)
            excluded_funds.append(ticker)
            continue
        if ticker in seen:
            continue
        seen.add(ticker)
        rows.append(row)

    logger.info("passed criteria: %d · excluded fund/REIT: %s · written: %d",
                len(raw), excluded_funds or "none", len(rows))
    return {
        "generated_at": datetime.now(BANGKOK_TZ).strftime("%Y-%m-%dT%H:%M:%S+07:00"),
        "source": "TradingView scanner (thailand, daily)",
        "universe_count": universe,
        "total_count": total,
        "excluded_funds": excluded_funds,
        "data": rows,
    }


def write_atomic(out: dict) -> None:
    text = json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False)  # NaN/Infinity → ValueError ก่อนแตะไฟล์
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_FILE.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, OUT_FILE)  # เขียนสำเร็จทั้งไฟล์เท่านั้นถึงทับของเดิม


def main() -> int:
    try:
        out = scan()
        write_atomic(out)
    except Exception as e:  # noqa: BLE001
        logger.error("Lekkung Daytrade Scan ล้มเหลว — ไม่เขียน %s (คงไฟล์เดิม): %s", OUT_FILE.name, e)
        return 1
    logger.info("✓ %s: %d ตัว (generated_at %s)", OUT_FILE.name, len(out["data"]), out["generated_at"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
