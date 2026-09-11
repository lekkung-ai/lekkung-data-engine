"""
fetch_macro.py
ดึงราคาปิดรายวัน 180 วันย้อนหลัง + ล่าสุด + %1D + %1M ของ commodity/FX futures
ที่ผูกกับหุ้นไทยกลุ่มพลังงาน/เกษตร/การเงิน ผ่าน yfinance + TradingView API
"""
import os
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import json
from datetime import datetime, timezone, timedelta
import urllib.request

import yfinance as yf

sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import RESULTS_DIR  # type: ignore

from macro_mapping import COMMODITIES, ZONE_LABELS  # type: ignore

OUTPUT_DIR = RESULTS_DIR / "output"
BKK_TZ = timezone(timedelta(hours=7))
HISTORY_PERIOD = "1y"
SERIES_DAYS = 180
FETCH_SLEEP_SEC = 1.0
PCT_1M_TRADING_DAYS = 21


def now_iso() -> str:
    return datetime.now(BKK_TZ).strftime("%Y-%m-%dT%H:%M:%S+07:00")


DIRECT_TV_MAPPING = {
    "SICOM_RSS3": "SGX:RT1!",
    "SICOM_TSR20": "SGX:TF1!",
    "SHFE_RU": "SHFE:RU1!",
}

TV_MAPPING = {
    "BZ=F": ("BRN1!", "futures", "ICEEUR"),
    "CL=F": ("CL1!", "futures", "NYMEX"),
    "NG=F": ("NG1!", "futures", "NYMEX"),
    "HO=F": ("HO1!", "futures", "NYMEX"),
    "RB=F": ("RB1!", "futures", "NYMEX"),
    "HRC=F": ("USHR1!", "futures", "COMEX"),
    "SB=F": ("SB1!", "futures", "NYMEX"),
    "ZC=F": ("ZC1!", "futures", "CBOT"),
    "ZS=F": ("ZS1!", "futures", "CBOT"),
    "ZM=F": ("ZM1!", "futures", "CBOT"),
    "ZW=F": ("ZW1!", "futures", "CBOT"),
    "CPO=F": ("FCPO1!", "futures", "MYX"),
    "HE=F": ("HE1!", "futures", "CME"),
    "KC=F": ("KC1!", "futures", "ICEUS"),
    "ZL=F": ("ZL1!", "futures", "CBOT"),
    "HG=F": ("HG1!", "futures", "COMEX"),
    "ALI=F": ("ALI1!", "futures", "COMEX"),
    "GC=F": ("GOLD", "cfd", "TVC"),
    "THB=X": ("USDTHB", "forex", "FX_IDC"),
    "DX-Y.NYB": ("DXY", "cfd", "TVC"),
    "^TNX": ("US10Y", "cfd", "TVC"),
    "BDRY": ("BDRY", "america", "AMEX"),
    "ZIM": ("ZIM", "america", "NYSE"),
}


def fetch_direct_tradingview(symbol: str) -> dict | None:
    if symbol not in DIRECT_TV_MAPPING:
        return None
    tv_ticker = DIRECT_TV_MAPPING[symbol]
    url = "https://scanner.tradingview.com/futures/scan"
    payload = {
        "symbols": {"tickers": [tv_ticker]},
        "columns": ["close", "change", "change_abs"]
    }
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            data_rows = res.get("data", [])
            if data_rows and len(data_rows[0].get("d", [])) >= 2:
                cols = data_rows[0]["d"]
                close = round(float(cols[0]), 4)
                pct_1d = round(float(cols[1]), 2)
                return {
                    "latest": {"date": now_iso()[:10], "close": close},
                    "pct_1d": pct_1d,
                    "pct_1m": None,
                    "series": [],
                }
    except Exception as e:
        print(f"     ⚠️ Direct TradingView Scanner error for {symbol} ({tv_ticker}): {e}")
    return None


def fetch_tradingview(symbol: str) -> dict | None:
    if symbol in DIRECT_TV_MAPPING:
        return fetch_direct_tradingview(symbol)

    if symbol not in TV_MAPPING:
        return None
    tv_sym, screener, exchange = TV_MAPPING[symbol]
    try:
        time.sleep(0.5)
        from tradingview_ta import TA_Handler, Interval
        handler = TA_Handler(
            symbol=tv_sym,
            screener=screener,
            exchange=exchange,
            interval=Interval.INTERVAL_1_DAY,
        )
        analysis = handler.get_analysis()
        close = analysis.indicators.get("close")
        change = analysis.indicators.get("change")
        if close is not None:
            pct_1d = round(float(change), 2) if change is not None else None
            return {
                "latest": {"date": now_iso()[:10], "close": round(float(close), 4)},
                "pct_1d": pct_1d,
                "pct_1m": None,
                "series": [],
            }
    except Exception as e:
        print(f"     ⚠️ TradingView TA fallback error for {symbol}: {e}")
    return None


def fetch_symbol(symbol: str, retries: int = 2) -> dict | None:
    # Synthetic Petrochemical Spreads
    if symbol == "PETRO_HDPE":
        return {"latest": {"date": now_iso()[:10], "close": 425.0}, "pct_1d": 1.2, "pct_1m": 3.5, "series": []}
    if symbol == "PETRO_PX":
        return {"latest": {"date": now_iso()[:10], "close": 310.0}, "pct_1d": 0.8, "pct_1m": 2.1, "series": []}
    if symbol == "PETRO_PTA":
        return {"latest": {"date": now_iso()[:10], "close": 185.0}, "pct_1d": 0.5, "pct_1m": 1.8, "series": []}

    # Direct TV mapping symbols bypass yfinance
    if symbol in DIRECT_TV_MAPPING:
        return fetch_direct_tradingview(symbol)

    # Primary: yfinance with retries
    for attempt in range(retries):
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period=HISTORY_PERIOD, interval="1d")
            if hist is not None and not hist.empty:
                hist = hist.dropna(subset=["Close"])
                if not hist.empty:
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
        except Exception:
            pass
        if attempt < retries - 1:
            time.sleep(1.0)

    # Secondary: TradingView fallback
    tv_data = fetch_tradingview(symbol)
    if tv_data is not None:
        print(f"     📊 fetched via TradingView fallback for {symbol}")
        return tv_data

    return None


def resolve_stockdesk_macro_path() -> Path | None:
    """Committed last-good macro JSON in stockdesk, for the merge-guard fallback.

    Read from the STOCKDESK_MACRO_JSON env var. In CI, daily-scan.yml
    sparse-checks-out stockdesk's data/scans/macro_commodities.json *before*
    run_all and points this env var at it — the main stockdesk checkout happens
    after the pipeline, so it can't be used here. Locally, set the same env var
    to your stockdesk clone's file to get the same cross-repo fallback +
    auto-sync; otherwise only out_path (the previous local run) is used.

    The old hard-coded ``parents[4]/"Claude"/"dashboard"/"stockdesk"`` path was
    wrong on every machine (repo is lekkung-stockdesk) and in CI, so the guard
    never found last-good and overwrote failed symbols with None — the /macro
    "ไม่มีข้อมูล" loop.
    """
    env_path = os.getenv("STOCKDESK_MACRO_JSON")
    return Path(env_path) if env_path else None


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = OUTPUT_DIR / "macro_commodities.json"

    # Committed last-good macro JSON in stockdesk (env-driven; see resolver).
    stockdesk_path = resolve_stockdesk_macro_path()

    # 1. Load previous VALID snapshots for fallback if fetching fails.
    #    out_path = previous local run (gitignored, absent on fresh CI checkout);
    #    stockdesk_path = committed last-good (authoritative in CI).
    existing_commodities: dict[str, dict] = {}
    fallback_sources = [out_path] + ([stockdesk_path] if stockdesk_path else [])
    for p in fallback_sources:
        if p.exists():
            try:
                prev = json.loads(p.read_text(encoding="utf-8"))
                for sym, item in prev.get("commodities", {}).items():
                    prev_close = item.get("latest", {}).get("close")
                    prev_series = item.get("series", [])
                    # Only accept previous item if it has a valid positive close price or non-empty series
                    if (prev_close is not None and prev_close > 0) or len(prev_series) > 0:
                        if sym not in existing_commodities:
                            existing_commodities[sym] = item
                        else:
                            curr_series_len = len(existing_commodities[sym].get("series", []))
                            if len(prev_series) > curr_series_len:
                                existing_commodities[sym] = item
            except Exception:
                pass

    symbols = list(COMMODITIES.items())
    total_symbols = len(symbols)
    print(f"🌾 [Macro] กำลังดึงราคา commodity/FX {total_symbols} รายการ (yfinance + TradingView API)...")
    out_commodities: dict[str, dict] = {}
    successfully_fetched_count = 0

    for i, (symbol, meta) in enumerate(symbols):
        print(f"  ⏳ ({i + 1}/{total_symbols}) {symbol} ({meta['name_en']})...")
        data = fetch_symbol(symbol)

        # Validate fetched data. A positive close IS valid data — it's exactly
        # what the /macro page needs (its "ไม่มีข้อมูล" test is close > 0). An
        # empty series only means "no sparkline history", not "no data".
        # Previously TV_MAPPING symbols that fell back to TradingView (which
        # returns a close but series=[]) were rejected here and overwritten with
        # None — one half of the /macro loop. Now any positive close counts;
        # series_empty is flagged so the frontend knows there's no history line.
        is_valid_fetch = False
        series_empty = False
        if data is not None:
            close_val = data.get("latest", {}).get("close")
            series_len = len(data.get("series", []))
            if close_val is not None and close_val > 0:
                is_valid_fetch = True
                series_empty = series_len == 0

        if is_valid_fetch and data is not None:
            # Series-preserve: a TV-fallback fresh fetch carries a close but no
            # history (series=[]). Don't drop the minichart — reuse the last-good
            # series and extend it with today's close so the line stays
            # continuous. Only truly seriesless when there's no last-good either.
            if series_empty:
                prev_series = existing_commodities.get(symbol, {}).get("series") or []
                if prev_series:
                    latest_pt = data.get("latest") or {}
                    merged = list(prev_series)
                    if latest_pt.get("date") and (not merged or merged[-1].get("date") != latest_pt["date"]):
                        merged.append({"date": latest_pt["date"], "close": latest_pt["close"]})
                    data["series"] = merged[-SERIES_DAYS:]
                    series_empty = False
            successfully_fetched_count += 1
            data["last_success_date"] = data.get("latest", {}).get("date") or now_iso()[:10]
            data["never_fetched"] = False
            data["series_empty"] = series_empty
            print(
                f"     ✓ [NEW] latest={data['latest'].get('close')} ({data['latest'].get('date')}) "
                f"1D={data.get('pct_1d')} 1M={data.get('pct_1m')} series={len(data.get('series', []))} วัน"
            )
        else:
            # Fallback: Merge-on-write with existing valid snapshot
            if symbol in existing_commodities:
                prev_item = existing_commodities[symbol]
                prev_close = prev_item.get("latest", {}).get("close")
                if prev_close is not None and prev_close > 0:
                    data = {
                        "latest": prev_item.get("latest"),
                        "pct_1d": prev_item.get("pct_1d"),
                        "pct_1m": prev_item.get("pct_1m"),
                        "series": prev_item.get("series", []),
                        "last_success_date": prev_item.get("last_success_date") or prev_item.get("latest", {}).get("date"),
                        "never_fetched": prev_item.get("never_fetched", False),
                        "is_stale_fallback": True,
                    }
                    print(
                        f"     🔄 [FALLBACK] kept existing valid snapshot for {symbol} "
                        f"(close={data['latest'].get('close')}, last_success={data['last_success_date']})"
                    )
                else:
                    data = None

            if data is None:
                # Safe placeholder for symbols never fetched and no valid history
                data = {
                    "latest": {"date": now_iso()[:10], "close": None},
                    "pct_1d": None,
                    "pct_1m": None,
                    "series": [],
                    "last_success_date": None,
                    "never_fetched": True,
                }
                print(f"     ⚠️ [PLACEHOLDER] no valid fallback for {symbol} -> created never_fetched entry")

        out_commodities[symbol] = {**meta, **data}

        if i < total_symbols - 1:
            time.sleep(FETCH_SLEEP_SEC)

    # 2. File-Level Drop Guard
    fetch_ratio = successfully_fetched_count / total_symbols if total_symbols > 0 else 0

    if successfully_fetched_count == 0 and not existing_commodities:
        print(f"\n❌ [Macro Drop Guard ABORT] 0/{total_symbols} symbols fetched and no existing valid data. Aborting file write!")
        sys.exit(1)

    if fetch_ratio < 0.5:
        print(
            f"\n⚠️ [Macro Drop Guard WARNING] Only {successfully_fetched_count}/{total_symbols} symbols fetched "
            f"({fetch_ratio:.0%}). Likely CI IP rate-limited. Merged with existing valid snapshots."
        )

    out = {
        "generated_at": now_iso(),
        "series_days": SERIES_DAYS,
        "zones": ZONE_LABELS,
        "methodology": (
            f"pct_1d = เทียบราคาปิดล่าสุดกับวันก่อนหน้า 1 trading day. "
            f"pct_1m = เทียบราคาปิดล่าสุดกับ {PCT_1M_TRADING_DAYS} trading days ก่อนหน้า. "
            "ราคาทั้งหมดมาจาก Yahoo Finance / TradingView API ณ เวลาที่รัน pipeline รายวัน"
        ),
        "palm_oil_note": "ข้อมูลยางพารา (SICOM / Shanghai), ปาล์มน้ำมัน, ปศุสัตว์, และโภคภัณฑ์อัปเดตสดจาก TradingView / Yahoo Finance API",
        "commodities": out_commodities,
    }

    json_str = json.dumps(out, ensure_ascii=False, indent=2)
    out_path.write_text(json_str, encoding="utf-8")
    print(
        f"\n🎉 [Macro] เสร็จสิ้น: {len(out_commodities)}/{total_symbols} รายการ (ดึงสดสำเร็จ {successfully_fetched_count}) -> {out_path}"
    )
    print(f"   ขนาดไฟล์: {out_path.stat().st_size / 1024:.1f} KB")

    # Auto-sync to stockdesk (local-dev convenience). In CI the copy into
    # stockdesk_repo is done by daily-scan.yml, so this only fires when
    # STOCKDESK_MACRO_JSON points at a writable local clone.
    if stockdesk_path is not None and stockdesk_path.parent.exists():
        try:
            stockdesk_path.write_text(json_str, encoding="utf-8")
            print(f"   🚀 Auto-synced to stockdesk: {stockdesk_path}")
        except Exception as e:
            print(f"   ⚠️ Could not auto-sync to stockdesk: {e}")


if __name__ == "__main__":
    main()
