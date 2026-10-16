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

import asyncio
import json
from datetime import datetime, timezone, timedelta
import urllib.request

import yfinance as yf

sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import RESULTS_DIR  # type: ignore

from macro_mapping import COMMODITIES, ZONE_LABELS  # type: ignore
from tools.core.set_index_source import _tv_fetch_async  # type: ignore  # TradingView WebSocket (same as SET_INDEX)

OUTPUT_DIR = RESULTS_DIR / "output"
BKK_TZ = timezone(timedelta(hours=7))
HISTORY_PERIOD = "1y"
SERIES_DAYS = 180
FETCH_SLEEP_SEC = 1.0
PCT_1M_TRADING_DAYS = 21


def now_iso() -> str:
    return datetime.now(BKK_TZ).strftime("%Y-%m-%dT%H:%M:%S+07:00")


# ─── TradingView WebSocket as the PRIMARY source ─────────────────────────────
# Since 2026-10-06 Yahoo fails from the CI runner for these five energy futures (the other =F symbols
# still work), and the tradingview_ta fallback never ran in CI (package not installed) → /macro showed the
# 2026-10-05 prices as "ราคาค้าง". These now come from the same TradingView WebSocket daily feed that
# set_index_source.py uses for SET_INDEX (no new dependency); Yahoo stays as the fallback, then last-good.
# TradingView continuous front month (1!): Brent = ICE Brent, WTI / Henry Hub / ULSD (heating oil) /
# RBOB = NYMEX. Same instruments as Yahoo's =F, but 1! rolls to the next contract on a different day, so
# around each roll the two differ (seen 2026-10-15: up to ~13%, HO/RB about half of every month); outside
# the roll windows the closes are identical. Approved 2026-10-16: use 1! as is, no cross-source % rule.
# One source per series: the whole history, latest, % 1D and % 1M all come from whichever source answered.
TV_PRIMARY = {
    "BZ=F": "ICEEUR:BRN1!",
    "CL=F": "NYMEX:CL1!",
    "NG=F": "NYMEX:NG1!",
    "HO=F": "NYMEX:HO1!",
    "RB=F": "NYMEX:RB1!",
}
TV_WS_BARS = 300                 # > SERIES_DAYS + PCT_1M_TRADING_DAYS
TV_WS_TIMEOUT_SEC = 30
TV_WS_ATTEMPTS = 2
# A TradingView daily bar is stamped at its session open: NYMEX 22:00Z the evening before, ICE 00:00Z
# (22:00Z after a weekend). +6h moves every stamp onto the trading date Yahoo uses for the same close.
TV_SESSION_SHIFT = timedelta(hours=6)
# Guard: a day-on-day move beyond this is logged as a WARNING (the price is still used)
TV_JUMP_WARN_PCT = 15.0

# "source" / "source_symbol" written into macro_commodities.json = what this run actually used
SRC_TV_WS = "TradingView continuous"
SRC_TV_SCANNER = "TradingView scanner"
SRC_TV_TA = "TradingView TA"
SRC_YAHOO = "Yahoo Finance"

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


def build_result(points: list[tuple[str, float]]) -> dict | None:
    """[(YYYY-MM-DD, close), …] oldest first → latest / pct_1d / pct_1m / series (last SERIES_DAYS)."""
    if not points:
        return None
    full = [c for _, c in points]
    tail = points[-SERIES_DAYS:]
    series = [{"date": d, "close": round(float(c), 4)} for d, c in tail]
    latest_date, latest_close = tail[-1][0], float(tail[-1][1])

    pct_1d = None
    if len(full) >= 2:
        prev = float(full[-2])
        if prev:
            pct_1d = round((latest_close - prev) / prev * 100, 2)

    pct_1m = None
    if len(tail) > PCT_1M_TRADING_DAYS:
        ref = float(tail[-1 - PCT_1M_TRADING_DAYS][1])
        if ref:
            pct_1m = round((latest_close - ref) / ref * 100, 2)

    return {
        "latest": {"date": latest_date, "close": round(latest_close, 4)},
        "pct_1d": pct_1d,
        "pct_1m": pct_1m,
        "series": series,
    }


def fetch_yahoo(symbol: str, retries: int = 2) -> dict | None:
    for attempt in range(retries):
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period=HISTORY_PERIOD, interval="1d")
            if hist is not None and not hist.empty:
                hist = hist.dropna(subset=["Close"])
                if not hist.empty:
                    return build_result([(d.strftime("%Y-%m-%d"), float(c)) for d, c in hist["Close"].items()])
            print(f"     ⚠️ Yahoo {symbol}: empty history (attempt {attempt + 1}/{retries})")
        except Exception as e:
            print(f"     ⚠️ Yahoo {symbol}: {type(e).__name__}: {e} (attempt {attempt + 1}/{retries})")
        if attempt < retries - 1:
            time.sleep(1.0)
    return None


def tv_session_date(ts: float) -> str:
    return (datetime.fromtimestamp(ts, timezone.utc) + TV_SESSION_SHIFT).strftime("%Y-%m-%d")


def fetch_tv_websocket(symbol: str) -> dict | None:
    """TV_PRIMARY symbol → daily history from the TradingView WebSocket (set_index_source._tv_fetch_async)."""
    tv_sym = TV_PRIMARY[symbol]
    for attempt in range(1, TV_WS_ATTEMPTS + 1):
        try:
            rows = asyncio.run(asyncio.wait_for(_tv_fetch_async(tv_sym, TV_WS_BARS), timeout=TV_WS_TIMEOUT_SEC))
            bars = [r["v"] for r in rows if len(r.get("v", [])) >= 5]
            if not bars:
                print(f"     ⚠️ TradingView {tv_sym}: no bars (attempt {attempt}/{TV_WS_ATTEMPTS})")
            elif bars[-1][4] is None or float(bars[-1][4]) <= 0:
                # newest bar without a price → use the fallback source, never yesterday's close as "latest"
                print(f"     ⚠️ TradingView {tv_sym}: newest bar has no price ({bars[-1][4]!r}) → fallback")
                return None
            else:
                by_date: dict[str, float] = {}
                for v in bars:
                    if v[4] is not None and float(v[4]) > 0:
                        by_date[tv_session_date(v[0])] = float(v[4])  # same session twice → keep the later bar
                data = build_result(sorted(by_date.items()))
                if data and data["pct_1d"] is not None and abs(data["pct_1d"]) > TV_JUMP_WARN_PCT:
                    print(f"     ⚠️ WARNING TradingView {tv_sym}: day-on-day change {data['pct_1d']:+.2f}% "
                          f"(> ±{TV_JUMP_WARN_PCT:.0f}%) — still used, check the contract roll")
                return data
        except Exception as e:
            print(f"     ⚠️ TradingView {tv_sym}: {type(e).__name__}: {e} (attempt {attempt}/{TV_WS_ATTEMPTS})")
        if attempt < TV_WS_ATTEMPTS:
            time.sleep(3)
    return None


def fetch_symbol(symbol: str) -> tuple[dict | None, str | None, str | None]:
    """(data, source, source_symbol) — logged every run and written into the JSON."""
    # Direct TV mapping symbols bypass yfinance
    if symbol in DIRECT_TV_MAPPING:
        return fetch_direct_tradingview(symbol), SRC_TV_SCANNER, DIRECT_TV_MAPPING[symbol]

    if symbol in TV_PRIMARY:
        data = fetch_tv_websocket(symbol)
        if data is not None:
            return data, SRC_TV_WS, TV_PRIMARY[symbol]
        print(f"     ↪️ TradingView {TV_PRIMARY[symbol]} failed → Yahoo {symbol} (whole history from Yahoo)")

    # Yahoo (primary for everything else, fallback for TV_PRIMARY — always the full Yahoo history)
    data = fetch_yahoo(symbol)
    if data is not None:
        return data, SRC_YAHOO, symbol

    # TradingView TA fallback — tradingview_ta is not in requirements (absent in CI), but the local
    # update_stockdesk.bat run (run_all.py scan) has it. Not used for TV_PRIMARY symbols.
    if symbol not in TV_PRIMARY:
        tv_data = fetch_tradingview(symbol)
        if tv_data is not None:
            tv_sym, _, exchange = TV_MAPPING[symbol]
            return tv_data, SRC_TV_TA, f"{exchange}:{tv_sym}"

    return None, None, None


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
        data, source, source_symbol = fetch_symbol(symbol)

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
            data["source"] = source
            data["source_symbol"] = source_symbol
            print(
                f"     ✓ [NEW] source={source} ({source_symbol}) latest={data['latest'].get('close')} ({data['latest'].get('date')}) "
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
                        # the run that produced this price (absent in files written before 2026-10-16)
                        "source": prev_item.get("source"),
                        "source_symbol": prev_item.get("source_symbol"),
                    }
                    print(
                        f"     🔄 [FALLBACK] source=last-good ({data['source']} {data['source_symbol']}) kept existing valid snapshot for {symbol} "
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
                    "source": None,
                    "source_symbol": None,
                }
                print(f"     ⚠️ [PLACEHOLDER] source=none no valid fallback for {symbol} -> created never_fetched entry")

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
