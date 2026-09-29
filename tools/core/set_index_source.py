"""
set_index_source.py
แหล่งข้อมูล SET Index (SET_INDEX.csv) — 2_download_history.py และ calculate_breadth.py ใช้โมดูลนี้ร่วมกัน

ที่มา: 2026-07 Yahoo เริ่มส่ง ^SET.BK รายวัน (interval=1d) มาแค่แท่งล่าสุด 1 แท่งไม่ว่า period ไหน
(หุ้นรายตัวและ ^GSPC ยังปกติ) → guard "< 200 แถว" เก็บไฟล์เดิมไว้เฉยๆ ไฟล์เลยค้างและมีแท่งเดี่ยวหลุดเข้ามา

ลำดับแหล่งข้อมูล
  1) TradingView WebSocket (SET:SET, รายวัน, ประวัติเต็ม) — ใช้ไลบรารี websockets ที่ yfinance ติดตั้งมาให้อยู่แล้ว
  2) Yahoo ^SET.BK แท่งล่าสุด ต่อท้ายไฟล์เดิม (append + ลบวันซ้ำ) — ใช้เมื่อ TradingView ล้ม
  ทั้งสองล้ม → log ERROR และไม่แตะไฟล์เดิม

กติกาการเขียน
  - ไม่เขียนแท่งที่ยังไม่ปิด: ก่อน 17:00 เวลาไทย ตัดแท่งของ "วันนี้" ทิ้ง
  - merge-on-write: ไฟล์ใหม่ต้องมีแถวไม่น้อยกว่าเดิม วันที่ซ้ำเขียนทับด้วยข้อมูลใหม่
  - เขียนแบบ atomic (tmp + os.replace)

⚠️ คอลัมน์ Volume ของ SET_INDEX.csv = "มูลค่าซื้อขายรวมของ universe ต่อวัน (บาท)"
   = Σ (Close × Volume) ของหุ้นทุกตัวจาก iter_stock_files — ไม่ใช่ volume ของดัชนี
   (TradingView ไม่มี volume ของดัชนี และ volume ดัชนีของ Yahoo ไม่ใช่ volume ตลาดจริง)
   ใช้เทียบ "วันนี้มากกว่าเมื่อวาน" ใน FTD/DD เท่านั้น
"""
import asyncio
import json
import math
import os
import random
import re
import string
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd

# log มี emoji — Windows console (cp1252) จะ raise UnicodeEncodeError ถ้าไม่ reconfigure (สคริปต์อื่นทำเหมือนกัน)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.append(str(Path(__file__).resolve().parents[2]))

BKK_TZ = timezone(timedelta(hours=7))

TV_SYMBOL = "SET:SET"
TV_WS_URL = "wss://data.tradingview.com/socket.io/websocket"
TV_TIMEOUT_SEC = 30          # เพดานรวมของทั้งการเชื่อมต่อ + รับข้อมูล — เกินแล้วถือว่า TV ล้ม ห้ามค้าง pipeline
TV_OPEN_TIMEOUT_SEC = 15
TV_ATTEMPTS = 2
TV_MIN_ROWS = 200            # ต่ำกว่านี้ถือว่าได้ไม่ครบ (เหมือน guard เดิมของ Yahoo)

MARKET_CLOSED_HOUR_BKK = 17  # ก่อนเวลานี้ แท่งของวันนี้ยังไม่ปิด (ปิดตลาด ~16:30-16:40)
CLOSE_MISMATCH_WARN_PCT = 0.5

# วันที่นับเป็นวันซื้อขายใน universe ต้องมีหุ้นที่ Volume > 0 อย่างน้อยเท่านี้ (ตัดวันที่มีข้อมูลหุ้นแหว่ง)
MIN_ACTIVE_TICKERS = 100

OHLC = ["Open", "High", "Low", "Close"]


# ─── helpers ─────────────────────────────────────────────────────────────────

def period_to_bars(period: str) -> int:
    """config.SET_INDEX_FETCH_PERIOD ("2y", "6mo", "90d") -> จำนวนแท่งรายวันที่ขอจาก TradingView
    (+10% เผื่อวันหยุดต่างกัน)"""
    m = re.fullmatch(r"\s*(\d+)\s*(y|mo|d)\s*", str(period).lower())
    if not m:
        raise ValueError(f"unsupported period {period!r}")
    n, unit = int(m.group(1)), m.group(2)
    days = {"y": 252, "mo": 21, "d": 1}[unit] * n
    return int(math.ceil(days * 1.1))


def drop_unclosed_today(df: pd.DataFrame, now: datetime) -> Tuple[pd.DataFrame, bool]:
    """ก่อน 17:00 เวลาไทย ตัดแท่งของวันนี้ทิ้ง (แท่งยังไม่ปิด). คืน (df, ตัดหรือไม่)"""
    now_bkk = now.astimezone(BKK_TZ) if now.tzinfo else now.replace(tzinfo=BKK_TZ)
    if now_bkk.hour >= MARKET_CLOSED_HOUR_BKK or df.empty:
        return df, False
    today = pd.Timestamp(now_bkk.date())
    keep = df.index != today
    return df[keep], bool((~keep).any())


def _clean_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    df = df[OHLC].apply(pd.to_numeric, errors="coerce").dropna()
    df = df[(df[OHLC] > 0).all(axis=1)]
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df.index = pd.DatetimeIndex(df.index).normalize()
    df.index.name = "Date"
    return df


# ─── source 1: TradingView WebSocket ─────────────────────────────────────────

def _tv_msg(m: str, p: list) -> str:
    s = json.dumps({"m": m, "p": p}, separators=(",", ":"))
    return f"~m~{len(s)}~m~{s}"


async def _tv_fetch_async(symbol: str, n_bars: int) -> list:
    import websockets  # transitive dependency ของ yfinance (ไม่เพิ่มใน requirements)

    session = "cs_" + "".join(random.choice(string.ascii_lowercase) for _ in range(12))
    async with websockets.connect(
        TV_WS_URL, origin="https://www.tradingview.com", open_timeout=TV_OPEN_TIMEOUT_SEC
    ) as ws:
        await ws.send(_tv_msg("set_auth_token", ["unauthorized_user_token"]))
        await ws.send(_tv_msg("chart_create_session", [session, ""]))
        await ws.send(_tv_msg("resolve_symbol", [session, "sds_sym_1", '={"symbol":"%s","adjustment":"splits"}' % symbol]))
        await ws.send(_tv_msg("create_series", [session, "sds_1", "s1", "sds_sym_1", "1D", n_bars, ""]))
        while True:
            raw = await ws.recv()
            for part in re.split(r"~m~\d+~m~", raw):
                if part.startswith("~h~"):  # heartbeat ต้องตอบกลับ ไม่งั้น server ตัด
                    await ws.send(f"~m~{len(part)}~m~{part}")
                    continue
                if not part.startswith("{"):
                    continue
                msg = json.loads(part)
                m = msg.get("m")
                if m in ("symbol_error", "series_error", "critical_error"):
                    raise RuntimeError(f"TradingView {m}: {part[:200]}")
                if m == "timescale_update":
                    return msg["p"][1]["sds_1"]["s"]


def fetch_tradingview_daily(symbol: str = TV_SYMBOL, n_bars: int = 600) -> pd.DataFrame:
    """OHLC รายวัน (index = Date) จาก TradingView. ล้มเหลว/ไม่ครบ/timeout → raise
    ลอง TV_ATTEMPTS ครั้ง (handshake timeout เกิดเป็นบางครั้ง) — แต่ละครั้งมีเพดานเวลา TV_TIMEOUT_SEC"""
    rows, err = None, None
    for attempt in range(1, TV_ATTEMPTS + 1):
        try:
            rows = asyncio.run(asyncio.wait_for(_tv_fetch_async(symbol, n_bars), timeout=TV_TIMEOUT_SEC))
            break
        except Exception as e:
            err = e
            print(f"  ⏳ [SET_INDEX] TradingView ครั้งที่ {attempt}/{TV_ATTEMPTS} ล้ม: {type(e).__name__}: {e}")
            if attempt < TV_ATTEMPTS:
                time.sleep(3)
    if rows is None:
        raise err  # type: ignore[misc]
    recs = []
    for r in rows:
        v = r["v"]
        recs.append({
            "Date": pd.Timestamp(datetime.fromtimestamp(v[0], timezone.utc).date()),
            "Open": v[1], "High": v[2], "Low": v[3], "Close": v[4],
        })
    df = _clean_ohlc(pd.DataFrame(recs).set_index("Date"))
    if len(df) < TV_MIN_ROWS:
        raise RuntimeError(f"TradingView returned only {len(df)} rows (< {TV_MIN_ROWS})")
    return df


# ─── source 2: Yahoo (latest bar only) ───────────────────────────────────────

def fetch_yahoo_latest(symbol: str) -> pd.DataFrame:
    """แท่งล่าสุดของ Yahoo (ตอนนี้ได้ 1 แท่ง) — คืนเท่าที่ได้ ไม่บังคับจำนวนแถว. ล้ม/ว่าง → raise"""
    import yfinance as yf

    df = yf.download(symbol, period="5d", interval="1d", auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df.dropna(subset=["Close"])
    if df.empty:
        raise RuntimeError("Yahoo returned no rows")
    return _clean_ohlc(df)


# ─── existing file / indicators / merge ──────────────────────────────────────

def read_existing(path: Path) -> Optional[pd.DataFrame]:
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path)
        df = df.rename(columns={df.columns[0]: "Date"})
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["Date"]).set_index("Date").sort_index()
        df.index = pd.DatetimeIndex(df.index).normalize()
        df.index.name = "Date"
        for c in OHLC + ["Volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df
    except Exception as e:
        print(f"  ⚠️ [SET_INDEX] อ่านไฟล์เดิมไม่ได้ ({e}) — ถือว่าไม่มีไฟล์เดิม")
        return None


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """SMA/52W ให้ format ตรงกับไฟล์หุ้น — คำนวณใหม่บนชุดที่ merge แล้วทั้งชุด"""
    df = df.copy()
    df["SMA_50"] = df["Close"].rolling(50).mean().round(2)
    df["SMA_150"] = df["Close"].rolling(150).mean().round(2)
    df["SMA_200"] = df["Close"].rolling(200).mean().round(2)
    df["52W_High"] = df["High"].rolling(252).max().round(2)
    df["52W_Low"] = df["Low"].rolling(252).min().round(2)
    return df


def merge_on_write(existing: Optional[pd.DataFrame], new: pd.DataFrame) -> pd.DataFrame:
    """union ของวันที่เดิม+ใหม่ — วันที่ซ้ำใช้ OHLC จากข้อมูลใหม่ · Volume ของแถวเดิมคงไว้ (แถวใหม่ = 0
    จนกว่าจะคำนวณมูลค่าซื้อขายรวม) · ไฟล์ผลลัพธ์แถวไม่น้อยกว่าเดิมเสมอ"""
    new = new[OHLC].copy()
    if existing is None or existing.empty:
        new["Volume"] = 0
        return new
    old = existing[[c for c in OHLC + ["Volume"] if c in existing.columns]].copy()
    if "Volume" not in old.columns:
        old["Volume"] = 0
    vol_old = old["Volume"]
    merged = pd.concat([old[OHLC], new]).pipe(lambda d: d[~d.index.duplicated(keep="last")]).sort_index()
    merged["Volume"] = vol_old.reindex(merged.index).fillna(0)
    assert len(merged) >= len(old), "merge-on-write produced fewer rows than the existing file"
    return merged


def atomic_write_csv(df: pd.DataFrame, path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_csv(tmp)
    os.replace(tmp, path)


# ─── orchestration ───────────────────────────────────────────────────────────

def fetch_set_index_ohlc(
    existing: Optional[pd.DataFrame],
    yf_symbol: str,
    period: str,
    now: Optional[datetime] = None,
) -> Tuple[Optional[pd.DataFrame], str]:
    """คืน (ตาราง OHLC ใหม่ที่พร้อม merge, ชื่อแหล่ง 'TV' | 'Yahoo-append') หรือ (None, 'FAILED')
    ไม่เขียนไฟล์ — ให้ผู้เรียก merge/เขียนเอง"""
    now = now or datetime.now(BKK_TZ)
    tv_df: Optional[pd.DataFrame] = None
    try:
        tv_df = fetch_tradingview_daily(TV_SYMBOL, period_to_bars(period))
        tv_df, cut = drop_unclosed_today(tv_df, now)
        if cut:
            print(f"  ⏱️ [SET_INDEX] ก่อน {MARKET_CLOSED_HOUR_BKK}:00 เวลาไทย — ตัดแท่งของวันนี้ (ยังไม่ปิด) ทิ้ง")
    except Exception as e:
        print(f"  ⚠️ [SET_INDEX] TradingView ล้ม: {type(e).__name__}: {e}")
        tv_df = None

    yh_df: Optional[pd.DataFrame] = None
    yh_cut = False
    try:
        yh_df = fetch_yahoo_latest(yf_symbol)
        yh_df, yh_cut = drop_unclosed_today(yh_df, now)
    except Exception as e:
        print(f"  ⚠️ [SET_INDEX] Yahoo {yf_symbol} ล้ม: {type(e).__name__}: {e}")
        yh_df = None

    if tv_df is not None and not tv_df.empty:
        # f) เทียบราคาปิดล่าสุดที่ตรงวันกันระหว่าง TV กับ Yahoo
        if yh_df is not None and not yh_df.empty:
            common = tv_df.index.intersection(yh_df.index)
            if len(common):
                d = common[-1]
                tv_c, yh_c = float(tv_df.loc[d, "Close"]), float(yh_df.loc[d, "Close"])
                diff_pct = abs(tv_c - yh_c) / yh_c * 100
                if diff_pct > CLOSE_MISMATCH_WARN_PCT:
                    print(f"  ⚠️ WARNING [SET_INDEX] ราคาปิด {d.date()} TV={tv_c:.2f} vs Yahoo={yh_c:.2f} "
                          f"ต่าง {diff_pct:.2f}% (> {CLOSE_MISMATCH_WARN_PCT}%)")
                else:
                    print(f"  ✓ [SET_INDEX] ราคาปิด {d.date()} TV={tv_c:.2f} ตรง Yahoo={yh_c:.2f} (ต่าง {diff_pct:.3f}%)")
            else:
                print("  ℹ️ [SET_INDEX] TV กับ Yahoo ไม่มีวันที่ตรงกันให้เทียบ")
        print(f"  ✅ [SET_INDEX] แหล่ง: TV — {len(tv_df)} แถว ({tv_df.index[0].date()} - {tv_df.index[-1].date()})")
        return tv_df, "TV"

    if yh_df is not None and not yh_df.empty and existing is not None and not existing.empty:
        print(f"  ✅ [SET_INDEX] แหล่ง: Yahoo-append — {len(yh_df)} แถว ({yh_df.index[0].date()} - {yh_df.index[-1].date()})")
        return yh_df, "Yahoo-append"

    if yh_df is not None and yh_df.empty and yh_cut and existing is not None and not existing.empty:
        print(f"  ℹ️ [SET_INDEX] TV ล้ม / Yahoo มีแต่แท่งของวันนี้ที่ยังไม่ปิด (ก่อน {MARKET_CLOSED_HOUR_BKK}:00) — ไม่มีแท่งใหม่ให้ต่อ เก็บไฟล์เดิมไว้")
        return None, "Yahoo-append(no closed bar)"

    if yh_df is not None and (existing is None or existing.empty):
        print("  ❌ ERROR [SET_INDEX] TradingView ล้มและไม่มีไฟล์เดิมให้ต่อท้าย — Yahoo ให้ได้แค่แท่งล่าสุด ใช้ไม่ได้")
    else:
        print("  ❌ ERROR [SET_INDEX] ทั้ง TradingView และ Yahoo ล้ม — เก็บ SET_INDEX.csv เดิมไว้ ไม่แก้")
    return None, "FAILED"


def universe_traded_value(history_dir: Path) -> pd.Series:
    """Σ (Close × Volume) ของหุ้นใน universe (iter_stock_files) ต่อวัน หน่วยบาท
    เฉพาะวันที่มีหุ้น Volume > 0 ≥ MIN_ACTIVE_TICKERS ตัว (วันที่หุ้นแหว่งไม่นับ)"""
    from tools.scanner.utils import iter_stock_files, load_full_df

    value_parts: List[pd.Series] = []
    active_parts: List[pd.Series] = []
    for fp in iter_stock_files(history_dir):
        try:
            df = load_full_df(fp)
            dates = pd.to_datetime(df.iloc[:, 0], errors="coerce")
            close = pd.to_numeric(df["Close"], errors="coerce")
            vol = pd.to_numeric(df["Volume"], errors="coerce")
        except Exception:
            continue
        s = pd.DataFrame({"c": close.values, "v": vol.values}, index=dates.values)
        s = s[s.index.notna()]
        s = s[~s.index.duplicated(keep="last")]
        s = s.dropna()
        s = s[(s.v > 0) & (s.c > 0)]
        if s.empty:
            continue
        value_parts.append((s.c * s.v).rename(fp.stem))
        active_parts.append(pd.Series(1, index=s.index, name=fp.stem))
    if not value_parts:
        return pd.Series(dtype=float)
    value = pd.concat(value_parts, axis=1).sum(axis=1)
    active = pd.concat(active_parts, axis=1).sum(axis=1)
    value = value[active >= MIN_ACTIVE_TICKERS]
    value.index = pd.DatetimeIndex(value.index).normalize()
    return value.sort_index()


def apply_universe_volume(df: pd.DataFrame, history_dir: Path) -> Tuple[pd.DataFrame, int]:
    """เขียนทับคอลัมน์ Volume ทั้งชุดด้วยมูลค่าซื้อขายรวมของ universe (ไม่ต่อกับ volume เก่า)
    วันที่ไม่มีข้อมูล universe = 0. คืน (df, จำนวนแถวที่ได้ค่า)"""
    tv = universe_traded_value(history_dir)
    df = df.copy()
    df["Volume"] = tv.reindex(df.index).fillna(0).round(0).astype("int64")
    return df, int((df["Volume"] > 0).sum())


def update_set_index_csv(history_dir: Path, yf_symbol: str, period: str,
                         now: Optional[datetime] = None) -> str:
    """ขั้นราคา: ดึง → merge → เขียน SET_INDEX.csv. คืนชื่อแหล่ง หรือ 'FAILED' (ไฟล์เดิมไม่ถูกแตะ)"""
    path = history_dir / "SET_INDEX.csv"
    existing = read_existing(path)
    new, source = fetch_set_index_ohlc(existing, yf_symbol, period, now)
    if new is None:
        return source
    merged = merge_on_write(existing, new)
    out = add_indicators(merged)
    atomic_write_csv(out, path)
    n_old = 0 if existing is None else len(existing)
    print(f"  💾 [SET_INDEX] source={source} เขียน {len(out)} แถว (เดิม {n_old}) "
          f"({out.index[0].date()} - {out.index[-1].date()})")
    return source


def update_set_index_volume(history_dir: Path) -> bool:
    """ขั้น volume: หลังดาวน์โหลดหุ้นเสร็จ — แทน Volume ด้วยมูลค่าซื้อขายรวมของ universe"""
    path = history_dir / "SET_INDEX.csv"
    df = read_existing(path)
    if df is None or df.empty:
        print("  ⚠️ [SET_INDEX] ไม่มีไฟล์ให้ใส่มูลค่าซื้อขายรวม — ข้าม")
        return False
    out, n = apply_universe_volume(df, history_dir)
    if n == 0:
        print("  ❌ ERROR [SET_INDEX] คำนวณมูลค่าซื้อขายรวมของ universe ไม่ได้ (ไม่มีวันที่มีหุ้นพอ) — คง Volume เดิม")
        return False
    core = ["Open", "High", "Low", "Close", "Volume"]
    out = out[core + [c for c in df.columns if c not in core]]
    atomic_write_csv(out, path)
    print(f"  💾 [SET_INDEX] Volume = มูลค่าซื้อขายรวม universe (บาท) — {n}/{len(out)} วันมีค่า "
          f"(ล่าสุด {out.index[-1].date()} = {int(out['Volume'].iloc[-1]):,})")
    return True
