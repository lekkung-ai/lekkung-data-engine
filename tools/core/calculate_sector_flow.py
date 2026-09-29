"""
calculate_sector_flow.py - Sector Flow data for the stockdesk /sector-flow page.

Two daily-close based views per subsector (and per sector), SET market only:

1) FLOW - where is trading value going, relative to normal
     value_d   = sum(Close x Volume) of the group's stocks (baht)
     share_d   = value_d / sum(value_d of the whole universe)
     flow_1d   = share today / mean(share of the previous 20 trading days, today excluded)
     flow_5d   = mean(share, last 5 days) / mean(share, the 20 days before those 5)
     chg_1d/5d = market-cap-weighted group return over the same window
     avg_value_20d = mean daily traded value of the last 20 days (million baht)

2) STRENGTH - group return in excess of the SET index
     Group return is the return of a constant-share basket: shares_i = market_cap_now / Close_now
     (an ESTIMATE - true historical share counts are not available), weight of day t = shares x Close_t.
     ret_1w / ret_1m / ret_3m = 5 / 21 / 63 trading days, excess = group ret - SET ret
     status: level = excess_3m, direction = excess_1m
       3m > 0 & 1m > 0 -> แข็งต่อเนื่อง   | 3m > 0 & 1m <= 0 -> เริ่มหมดแรง
       3m <= 0 & 1m > 0 -> เพิ่งเริ่มแข็ง | 3m <= 0 & 1m <= 0 -> อ่อนต่อเนื่อง

Universe = iter_stock_files(HISTORY_DIR) (stale files and SET_INDEX.csv already excluded)
           restricted to SET-market tickers in sector_map.json.
Market cap = Market_Cap column of daily_prices.csv (TradingView market_cap_basic, written by 2_download_history.py).
Output:   data/results/output/sector_flow.json (copied to stockdesk/data/scans/ by the pipeline).
"""
import argparse
import json
import logging
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

# Force UTF-8 encoding for standard output/error on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import DAILY_FILE, HISTORY_DIR, RESULTS_DIR  # noqa: E402
from tools.scanner.utils import iter_stock_files, load_full_df  # noqa: E402

BANGKOK_TZ = timezone(timedelta(hours=7))

# sector_map.json lives in stockdesk - same two lookup locations as 4_calculate_sector_rs.py
#   - CI (daily-scan.yml): early sparse checkout at data_engine/stockdesk_sector_map_check/
#   - local dev: stockdesk sibling checkout
_DATA_ENGINE_ROOT = Path(__file__).resolve().parents[2]
SECTOR_MAP_CANDIDATES = [
    _DATA_ENGINE_ROOT / "stockdesk_sector_map_check" / "data" / "scans" / "sector_map.json",
    _DATA_ENGINE_ROOT.parent.parent / "Claude" / "dashboard" / "stockdesk" / "data" / "scans" / "sector_map.json",
]

OUTPUT_PATH = RESULTS_DIR / "output" / "sector_flow.json"

# Drop Guard: losing more than this fraction of subsectors vs the baseline = broken input, keep the old file
MAX_SUBSECTOR_DROP_PCT = 0.30

BASE_WINDOW = 20      # trading days that define "normal" share
SHORT_WINDOW = 5      # flow_5d
LOOKBACK = {"1w": 5, "1m": 21, "3m": 63}
MIN_ACTIVE_TICKERS = 100   # a date counts as a trading day only if this many stocks traded on it
MIN_HISTORY_DAYS = BASE_WINDOW + SHORT_WINDOW + 1
MAX_INDEX_LAG_DAYS = 5     # SET_INDEX.csv older than this vs the last trading day = stale benchmark

STATUS_STRONG = "แข็งต่อเนื่อง"
STATUS_FADING = "เริ่มหมดแรง"
STATUS_TURNING = "เพิ่งเริ่มแข็ง"
STATUS_WEAK = "อ่อนต่อเนื่อง"


# ── pure helpers ──────────────────────────────────────────────────────────────

def finite_or_none(v: Any, digits: Optional[int] = None) -> Optional[float]:
    """float(v) if finite else None (NaN / Infinity must never reach the JSON)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return round(f, digits) if digits is not None else f


def classify_status(excess_3m: Optional[float], excess_1m: Optional[float]) -> Optional[str]:
    if excess_3m is None or excess_1m is None:
        return None
    if excess_3m > 0:
        return STATUS_STRONG if excess_1m > 0 else STATUS_FADING
    return STATUS_TURNING if excess_1m > 0 else STATUS_WEAK


def compute_flow(value_g: pd.Series, total: pd.Series) -> Dict[str, Optional[float]]:
    """flow_1d / flow_5d / avg_value_20d for one group.

    value_g / total: daily traded value (baht) of the group / of the whole universe,
    same trading-day index, oldest first."""
    out: Dict[str, Optional[float]] = {
        "flow_1d": None, "flow_5d": None, "avg_value_20d": None,
        "value_1d": None, "base_value_1d": None, "value_5d": None, "base_value_5d": None,
    }
    if len(total) < MIN_HISTORY_DAYS:
        return out
    share = (value_g / total.where(total > 0)).astype(float)
    out["avg_value_20d"] = finite_or_none(value_g.tail(BASE_WINDOW).mean() / 1e6, 2)
    # raw traded value (million baht) behind each ratio, for display: today vs the 20-day base,
    # and the last-5-day daily average vs the 20-day base before it
    out["value_1d"] = finite_or_none(value_g.iloc[-1] / 1e6, 2)
    out["base_value_1d"] = finite_or_none(value_g.iloc[-1 - BASE_WINDOW:-1].mean() / 1e6, 2)
    out["value_5d"] = finite_or_none(value_g.iloc[-SHORT_WINDOW:].mean() / 1e6, 2)
    out["base_value_5d"] = finite_or_none(value_g.iloc[-SHORT_WINDOW - BASE_WINDOW:-SHORT_WINDOW].mean() / 1e6, 2)

    base_1d = share.iloc[-1 - BASE_WINDOW:-1].mean()
    if base_1d and base_1d > 0:
        out["flow_1d"] = finite_or_none(share.iloc[-1] / base_1d, 3)

    recent = share.iloc[-SHORT_WINDOW:].mean()
    base_5d = share.iloc[-SHORT_WINDOW - BASE_WINDOW:-SHORT_WINDOW].mean()
    if base_5d and base_5d > 0:
        out["flow_5d"] = finite_or_none(recent / base_5d, 3)
    return out


def basket_return(close: pd.DataFrame, shares: pd.Series, tickers: List[str], days: int) -> Optional[float]:
    """Return (in %) of a constant-share basket over `days` trading days ending on the last row.

    Only tickers with a mcap-derived share count and a price on both endpoints count."""
    if len(close) <= days:
        return None
    cols = [t for t in tickers if t in close.columns and t in shares.index and shares[t] > 0]
    if not cols:
        return None
    end = close[cols].iloc[-1]
    start = close[cols].iloc[-1 - days]
    ok = end.notna() & start.notna() & (start > 0)
    if not ok.any():
        return None
    sh = shares[cols][ok]
    v_end = float((sh * end[ok]).sum())
    v_start = float((sh * start[ok]).sum())
    if v_start <= 0:
        return None
    return finite_or_none((v_end / v_start - 1) * 100, 2)


def index_return(idx_close: pd.Series, days: int) -> Optional[float]:
    s = idx_close.dropna()
    if len(s) <= days:
        return None
    return finite_or_none((s.iloc[-1] / s.iloc[-1 - days] - 1) * 100, 2)


def group_record(close: pd.DataFrame, value: pd.DataFrame, total: pd.Series, shares: pd.Series,
                 idx_close: pd.Series, tickers: List[str]) -> Dict[str, Any]:
    rec: Dict[str, Any] = {"n": len(tickers)}
    vg = value[[t for t in tickers if t in value.columns]].sum(axis=1)
    rec.update(compute_flow(vg, total))
    rec["chg_1d"] = basket_return(close, shares, tickers, 1)
    rec["chg_5d"] = basket_return(close, shares, tickers, SHORT_WINDOW)
    for name, days in LOOKBACK.items():
        ret = basket_return(close, shares, tickers, days)
        bench = index_return(idx_close, days)
        rec[f"ret_{name}"] = ret
        rec[f"excess_{name}"] = finite_or_none(ret - bench, 2) if ret is not None and bench is not None else None
    rec["status"] = classify_status(rec["excess_3m"], rec["excess_1m"])
    return rec


# ── I/O ───────────────────────────────────────────────────────────────────────

def resolve_sector_map(explicit: Optional[Path]) -> Optional[Path]:
    for path in ([explicit] if explicit else SECTOR_MAP_CANDIDATES):
        if path and path.exists():
            return path
    return None


def load_history(history_dir: Path, wanted: set) -> Dict[str, pd.DataFrame]:
    """ticker -> DataFrame(Close, Volume) indexed by date, for stock files (stale ones excluded)."""
    out: Dict[str, pd.DataFrame] = {}
    for fp in iter_stock_files(history_dir):
        ticker = fp.stem.upper()
        if ticker not in wanted:
            continue
        try:
            df = load_full_df(fp)
            dates = pd.to_datetime(df.iloc[:, 0], errors="coerce")
            close = pd.to_numeric(df["Close"], errors="coerce")
            vol = pd.to_numeric(df["Volume"], errors="coerce").fillna(0)
            frame = pd.DataFrame({"Close": close.values, "Volume": vol.values}, index=dates.values)
            frame = frame[frame.index.notna()]
            frame = frame[~frame.index.duplicated(keep="last")].sort_index()
            out[ticker] = frame.dropna(subset=["Close"])
        except Exception as e:  # noqa: BLE001
            logger.warning(f"⚠️ skip {fp.name}: {e}")
    return out


def load_market_caps(daily_file: Path) -> pd.Series:
    df = pd.read_csv(daily_file, encoding="utf-8-sig")
    if "Market_Cap" not in df.columns:
        return pd.Series(dtype=float)
    s = pd.Series(pd.to_numeric(df["Market_Cap"], errors="coerce").values,
                  index=df["Ticker"].astype(str).str.strip().str.upper())
    s = s[s.notna() & (s > 0)]
    return s[~s.index.duplicated(keep="last")]


def load_set_index(history_dir: Path) -> pd.Series:
    fp = history_dir / "SET_INDEX.csv"
    if not fp.exists():
        return pd.Series(dtype=float)
    df = load_full_df(fp)
    dates = pd.to_datetime(df.iloc[:, 0], errors="coerce")
    s = pd.Series(pd.to_numeric(df["Close"], errors="coerce").values, index=dates.values)
    s = s[s.index.notna()].sort_index()
    return s[~s.index.duplicated(keep="last")].dropna()


# ── build ─────────────────────────────────────────────────────────────────────

def build(history_dir: Path, daily_file: Path, sector_map_path: Path) -> Dict[str, Any]:
    t2s = json.loads(sector_map_path.read_text(encoding="utf-8")).get("ticker_to_sector", {})
    set_tickers = {t.upper(): (v.get("sector") or "", v.get("subsector") or "")
                   for t, v in t2s.items() if v.get("market") == "SET" and v.get("sector")}
    if not set_tickers:
        raise SystemExit("sector_map has no SET tickers")

    hist = load_history(history_dir, set(set_tickers))
    if len(hist) < 100:
        raise SystemExit(f"only {len(hist)} SET stock files usable in {history_dir} (< 100) - refusing to build")

    close = pd.DataFrame({t: h["Close"] for t, h in hist.items()}).sort_index()
    vol = pd.DataFrame({t: h["Volume"] for t, h in hist.items()}).reindex(close.index)
    traded = (vol > 0).sum(axis=1)
    days = traded[traded >= MIN_ACTIVE_TICKERS].index
    close, vol = close.loc[days], vol.loc[days]
    if len(close) < MIN_HISTORY_DAYS:
        raise SystemExit(f"only {len(close)} usable trading days (< {MIN_HISTORY_DAYS})")
    close_f = close.ffill()          # illiquid stock with no bar on a day keeps its last price
    value = (close * vol).fillna(0)  # traded value only where a bar exists
    total = value.sum(axis=1)

    caps = load_market_caps(daily_file)
    if caps.empty:
        raise SystemExit(f"no Market_Cap in {daily_file} - cannot weight returns")
    last_close = close_f.iloc[-1]
    shares = (caps.reindex(last_close.index) / last_close).replace([np.inf, -np.inf], np.nan).dropna()
    shares = shares[shares > 0]
    set_idx = load_set_index(history_dir)
    if set_idx.empty or set_idx.index.max() < close.index[-1] - pd.Timedelta(days=MAX_INDEX_LAG_DAYS):
        # 2_download_history.py keeps the OLD SET_INDEX.csv when Yahoo returns < 200 bars, so it can be stale.
        # Flow is still valid; excess_* / status become null rather than compare against a stale benchmark.
        logger.warning(f"⚠️ SET_INDEX.csv missing or older than {MAX_INDEX_LAG_DAYS} days vs {close.index[-1].date()} - "
                       f"excess_* and status will be null")
        idx_close = pd.Series(np.nan, index=close.index)
    else:
        idx_close = set_idx.reindex(close.index).ffill()

    tickers_by_sub: Dict[tuple, List[str]] = {}
    tickers_by_sec: Dict[str, List[str]] = {}
    for t in hist:
        sec, sub = set_tickers[t]
        tickers_by_sec.setdefault(sec, []).append(t)
        if sub:
            tickers_by_sub.setdefault((sec, sub), []).append(t)

    subsectors = []
    for (sec, sub), tk in sorted(tickers_by_sub.items()):
        rec = group_record(close_f, value, total, shares, idx_close, tk)
        subsectors.append({"sector": sec, "subsector": sub, **rec})
    sectors = []
    for sec, tk in sorted(tickers_by_sec.items()):
        sectors.append({"sector": sec, **group_record(close_f, value, total, shares, idx_close, tk)})

    as_of = close.index[-1].date().isoformat()
    no_mcap = sorted(t for t in hist if t not in shares.index)
    return {
        "generated_at": datetime.now(BANGKOK_TZ).isoformat(),
        "as_of": as_of,
        "method": (
            "Daily closes, SET market. flow_1d = group share of total traded value (Close x Volume) today / avg share of "
            "the previous 20 trading days; flow_5d = avg share of last 5 days / avg share of the 20 days before. "
            "value_* / base_value_* = traded value in million baht behind flow_* (today / 5-day daily avg vs the 20-day base). chg_* and ret_* = market-cap-weighted group return (constant-share basket, shares ESTIMATED as "
            "market_cap_now / Close_now - historical share counts unavailable); excess_* = group ret - SET index ret over "
            "5 / 21 / 63 trading days. status: level = excess_3m, direction = excess_1m. avg_value_20d in million baht."
        ),
        "n_stocks": len(hist),
        "n_without_market_cap": len(no_mcap),
        "subsectors": subsectors,
        "sectors": sectors,
    }


def load_baseline_count(sector_map_path: Path, out_path: Path) -> tuple:
    """Subsector count to compare against: the previous output if readable, else what sector_map defines."""
    try:
        if out_path.exists():
            prev = json.loads(out_path.read_text(encoding="utf-8"))
            if prev.get("subsectors"):
                return len(prev["subsectors"]), str(out_path)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"⚠️ could not read previous {out_path}: {e}")
    t2s = json.loads(sector_map_path.read_text(encoding="utf-8")).get("ticker_to_sector", {})
    subs = {(v.get("sector"), v.get("subsector")) for v in t2s.values() if v.get("market") == "SET" and v.get("subsector")}
    return len(subs), "sector_map.json"


def main() -> None:
    ap = argparse.ArgumentParser(description="Sector Flow (volume share + relative strength) per subsector")
    ap.add_argument("--history-dir", type=Path, default=HISTORY_DIR)
    ap.add_argument("--daily-prices", type=Path, default=DAILY_FILE)
    ap.add_argument("--sector-map", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=OUTPUT_PATH)
    args = ap.parse_args()

    sm_path = resolve_sector_map(args.sector_map)
    if sm_path is None:
        tried = "\n".join(f"  - {c}" for c in SECTOR_MAP_CANDIDATES)
        logger.error(f"❌ ERROR: sector_map.json not found. Tried:\n{tried}")
        sys.exit(1)
    logger.info(f"Using sector_map.json at {sm_path}")
    if not args.daily_prices.exists():
        logger.error(f"❌ ERROR: {args.daily_prices} not found (needed for Market_Cap)")
        sys.exit(1)

    payload = build(args.history_dir, args.daily_prices, sm_path)

    base_count, base_src = load_baseline_count(sm_path, args.out)
    new_count = len(payload["subsectors"])
    if base_count > 0 and new_count < base_count * (1 - MAX_SUBSECTOR_DROP_PCT):
        logger.error(f"❌ Drop Guard triggered: subsectors {base_count} -> {new_count} "
                     f"(> {MAX_SUBSECTOR_DROP_PCT:.0%} drop vs {base_src}). Aborting write, keeping previous file.")
        sys.exit(1)

    # Serialize first: allow_nan=False raises before any existing file is truncated
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    logger.info(f"✅ Saved {args.out} ({new_count} subsectors, {len(payload['sectors'])} sectors, as_of {payload['as_of']})")


if __name__ == "__main__":
    main()
