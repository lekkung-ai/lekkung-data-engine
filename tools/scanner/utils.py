from datetime import date
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

# ไฟล์ใน HISTORY_DIR ที่ไม่ใช่หุ้น (ดัชนี) — 2_download_history.py ยังเขียนไว้ให้
# calculate_breadth.py อ่านตรงๆ แต่ต้องไม่หลุดเข้า universe ของ RS / scanner
NON_STOCK_FILES = frozenset({"SET_INDEX.csv"})

# ไฟล์ราคาหยุดนิ่ง: หุ้นที่ถูกพัก/เพิกถอนยังค้างไฟล์ใน HISTORY_DIR (stale fallback ของ
# 2_download_history.py) หรือ Yahoo ยังเติมแท่งวันที่ใหม่ให้แต่ Volume = 0 ทุกแท่ง
# → วัดจาก "แถวสุดท้ายที่ Volume > 0" ไม่ใช่แถวสุดท้าย
# 2 ระดับ: หุ้นที่ TradingView ยังนับเป็นหุ้น (active_symbols Source = tv / grace) ได้เกณฑ์
# หลวม 60 วัน กันหุ้นเทรดน้อย / Yahoo ข้อมูลหายเป็นช่วง (เช่น BANPU ~34 วัน) หลุดเข้าออก
# universe สลับไปมา — ตัวกรองนี้มีไว้ตัดหุ้นที่ไม่มีข้อมูลแล้ว ไม่ใช่ตัดหุ้นสภาพคล่องต่ำ
# หุ้นที่มาจาก union ของ sector_map อย่างเดียว (TradingView เอาออกแล้ว) ใช้ 20 วันเหมือนไฟล์นอก universe
STALE_TRADING_DAYS = 20          # Source = sector_map และไฟล์ที่ไม่อยู่ใน active_symbols
STALE_TRADING_DAYS_LISTED = 60   # Source = tv / grace
_LISTED_SOURCES = frozenset({"tv", "grace"})
# ถ้าต้องตัดเกินสัดส่วนนี้ แปลว่าข้อมูลรอบล่าสุดเสียทั้งชุด ไม่ใช่หุ้นหยุดนิ่งจริง → ไม่ตัดเลย
STALE_MAX_CUT_PCT = 0.10
# อ่านย้อนจากท้ายไฟล์ไม่เกินกี่แถว (~1 ปี) — ไม่เจอแถว Volume > 0 ในช่วงนี้ = หยุดนิ่งแน่นอน
STALE_SCAN_ROWS = 250
_TAIL_CHUNK_BYTES = 16 * 1024


def last_traded_date(file_path: Path) -> Optional[date]:
    """วันที่ของแถวสุดท้ายที่ Volume > 0 ภายใน STALE_SCAN_ROWS แถวท้ายไฟล์ — อ่านย้อนจาก
    ท้ายไฟล์ทีละก้อน ไม่โหลดทั้งไฟล์

    รองรับ header ทั้งแบบธรรมดา (",Open,High,Low,Close,Volume,...") และแบบ Price 2 ชั้น
    ของ yfinance ("Price,Close,...,Volume" / "Ticker,..." / "Date,,...") — ชื่อคอลัมน์อยู่
    บรรทัดแรกทั้งคู่ ส่วนบรรทัดที่คอลัมน์แรกไม่ใช่วันที่ (Ticker/Date) จะถูกข้าม
    คืน None ถ้าไม่เจอแถว Volume > 0 ในช่วงที่อ่าน หรืออ่านไฟล์/หาคอลัมน์ Volume ไม่ได้
    """
    try:
        with open(file_path, "rb") as f:
            header = f.readline().decode("utf-8", "replace").strip().split(",")
            vol_idx = next((i for i, c in enumerate(header) if "Volume" in c), None)
            if vol_idx is None:
                return None
            body_start = f.tell()
            f.seek(0, 2)
            pos = f.tell()
            carry = b""  # บรรทัดที่ถูกตัดครึ่งจากก้อนก่อนหน้า
            rows_seen = 0
            while pos > body_start and rows_seen < STALE_SCAN_ROWS:
                step = min(_TAIL_CHUNK_BYTES, pos - body_start)
                pos -= step
                f.seek(pos)
                lines = (f.read(step) + carry).split(b"\n")
                # บรรทัดแรกของก้อนอาจไม่ครบ (ยกเว้นก้อนที่เริ่มต้นเนื้อไฟล์พอดี) → เก็บไว้ต่อก้อนถัดไป
                carry = lines.pop(0) if pos > body_start else b""
                for raw in reversed(lines):
                    parts = raw.decode("utf-8", "replace").strip().split(",")
                    if len(parts) <= vol_idx:
                        continue
                    try:
                        d = date.fromisoformat(parts[0].strip()[:10])
                        vol = float(parts[vol_idx])
                    except ValueError:
                        continue
                    if vol > 0:
                        return d
                    rows_seen += 1
                    if rows_seen >= STALE_SCAN_ROWS:
                        break
    except OSError:
        return None
    return None


def load_symbol_sources() -> Tuple[Optional[Dict[str, str]], bool]:
    """(ticker → Source, มีคอลัมน์ Source ไหม) จาก active_symbols (SYMBOLS_FILE ของ config.py)

    Source = "tv" / "grace" / "sector_map" (เขียนโดย 1_get_symbols.py) · ไฟล์เก่าที่ยังไม่มี
    คอลัมน์ Source → ทุกตัวได้ "listed" (เกณฑ์ 60 เหมือนกัน) · อ่านไฟล์ไม่ได้ → (None, False)
    """
    try:
        from config import SYMBOLS_FILE  # ทุกจุดที่เรียกเพิ่ม data_engine root ลง sys.path แล้ว

        df = pd.read_csv(SYMBOLS_FILE)
        tickers = df["Ticker"].astype(str).str.strip().str.upper()
        has_source = "Source" in df.columns
        sources = df["Source"].astype(str).str.strip().str.lower() if has_source else ["listed"] * len(df)
        mapping = {t: s for t, s in zip(tickers, sources) if t and t != "NAN"}
        return (mapping or None), has_source
    except Exception:
        return None, False


def stale_limit(ticker: str, sources: Optional[Dict[str, str]]) -> Tuple[int, str]:
    """(เกณฑ์วันทำการ, label ของเกณฑ์) ของ ticker นี้"""
    src = (sources or {}).get(ticker.upper())
    if src in _LISTED_SOURCES or src == "listed":
        return STALE_TRADING_DAYS_LISTED, f"{src} {STALE_TRADING_DAYS_LISTED}"
    return STALE_TRADING_DAYS, f"{src or 'not in active_symbols'} {STALE_TRADING_DAYS}"


def find_stale_files(
    files: List[Path], sources: Optional[Dict[str, str]]
) -> Tuple[List[Tuple[Path, Optional[date], str]], Optional[date]]:
    """คืน (ไฟล์ที่หยุดนิ่ง + วันสุดท้ายที่มี volume + label เกณฑ์ที่ใช้, วันอ้างอิง)

    วันอ้างอิง = วันสุดท้ายที่มี volume ที่มากที่สุดในทุกไฟล์ (ไม่ใช้วันนี้ เพราะ CI รันวันหยุดด้วย)
    หยุดนิ่ง = ช้ากว่าวันอ้างอิงเกินเกณฑ์ (วันทำการ จันทร์–ศุกร์) หรือไม่เจอแถว Volume > 0
    ใน STALE_SCAN_ROWS แถวท้ายไฟล์ · เกณฑ์ตาม stale_limit() (sources = None → 20 ทุกไฟล์)
    """
    last: Dict[Path, Optional[date]] = {p: last_traded_date(p) for p in files}
    known = [d for d in last.values() if d is not None]
    ref = max(known) if known else None
    stale = []
    for p, d in last.items():
        limit, tier = stale_limit(p.stem, sources)
        if d is None or int(np.busday_count(d, ref)) > limit:
            stale.append((p, d, tier))
    return stale, ref


def iter_stock_files(history_dir: Path) -> Iterator[Path]:
    """วนไฟล์ *.csv ใน history_dir เฉพาะหุ้น ลำดับเดียวกับ glob

    ข้ามไฟล์ใน NON_STOCK_FILES และไฟล์ราคาหยุดนิ่ง (ดู find_stale_files) — ถ้าหยุดนิ่ง
    เกิน STALE_MAX_CUT_PCT ของทั้งหมด ถือว่าข้อมูลเสีย ไม่ตัด แล้ว log ERROR
    """
    files = [p for p in history_dir.glob("*.csv") if p.name not in NON_STOCK_FILES]
    sources, has_source = load_symbol_sources()
    if sources is None:
        print(
            f"[iter_stock_files] WARNING: cannot read active_symbols - using the "
            f"{STALE_TRADING_DAYS}-trading-day stale limit for every file"
        )
    elif not has_source:
        print(
            f"[iter_stock_files] WARNING: active_symbols has no Source column - using the "
            f"{STALE_TRADING_DAYS_LISTED}-trading-day limit for every ticker in it"
        )
    stale, ref = find_stale_files(files, sources)
    if stale:
        no_bar = f"no traded bar within last {STALE_SCAN_ROWS} rows"
        detail = ", ".join(
            f"{p.stem}({d if d is not None else no_bar}, {tier})"
            for p, d, tier in sorted(stale, key=lambda x: x[0].stem)
        )
        if len(stale) > STALE_MAX_CUT_PCT * len(files):
            print(
                f"[iter_stock_files] ERROR: {len(stale)}/{len(files)} files look stale "
                f"(> {STALE_MAX_CUT_PCT:.0%}, ref {ref}) - NOT excluding any, data may be broken: {detail}"
            )
            stale = []
        else:
            print(
                f"[iter_stock_files] excluded {len(stale)} stale file(s) (last traded bar more than "
                f"tv/grace {STALE_TRADING_DAYS_LISTED} / other {STALE_TRADING_DAYS} trading days before {ref}): {detail}"
            )
    stale_set = {p for p, _, _ in stale}
    for file_path in files:
        if file_path not in stale_set:
            yield file_path


def load_price_volume(
    file_path: Union[str, Path], min_length: int = 0
) -> Tuple[Optional[pd.Series], Optional[pd.Series]]:
    """
    อ่านไฟล์ CSV และเตรียมข้อมูล Price/Volume อย่างรวดเร็ว
    แก้ปัญหาการโหลดไฟล์ช้าจากการอ่านไฟล์ซ้ำซ้อน

    Args:
        file_path: Path to the CSV file.
        min_length: Minimum number of rows required.

    Returns:
        Tuple of (price_series, vol_series) or (None, None) if criteria not met.
    """
    # เรียกใช้ load_full_df เพื่อลดความซ้ำซ้อนของโค้ดอ่านไฟล์
    df = load_full_df(file_path)

    # load_full_df จัดการ Header ให้แล้ว จึงเรียกชื่อคอลัมน์ได้ตรงๆ
    close_col = next((c for c in df.columns if "Close" in c), "Close")
    vol_col = next((c for c in df.columns if "Volume" in c), "Volume")

    price_series = pd.to_numeric(df[close_col], errors="coerce").dropna()
    vol_series = pd.to_numeric(df[vol_col], errors="coerce").fillna(0)

    if len(price_series) < min_length:
        return None, None

    return price_series, vol_series


def load_full_df(file_path: Union[str, Path]) -> pd.DataFrame:
    """
    อ่านไฟล์ CSV และคืนค่าเป็น DataFrame เต็มรูปแบบ (มีครบทุกคอลัมน์ Date, O, H, L, C, V)
    พร้อมจัดการปัญหา Multi-Index Header แบบอัตโนมัติ

    Args:
        file_path: Path to the CSV file.

    Returns:
        pd.DataFrame containing the parsed stock data.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        first_line = f.readline()

    header_idx = [0, 1] if "Price" in first_line else 0
    df = pd.read_csv(file_path, header=header_idx)

    # ถ้ายอดคอลัมน์เป็น Multi-Index (เช่น มีชั้น Price, Ticker) ให้ยุบเหลือแค่ชั้นเดียว
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [str(col[0]).strip() for col in df.columns]

    return df


def calculate_adtv(
    price_series: pd.Series, vol_series: pd.Series, window: int = 50
) -> float:
    """
    คำนวณมูลค่าการซื้อขายเฉลี่ยต่อวัน (ADTV) เป็นหน่วยล้านบาท

    Args:
        price_series: Series containing closing prices.
        vol_series: Series containing volume.
        window: The rolling window for calculation.

    Returns:
        Average Daily Trading Volume in millions of baht.
    """
    return (price_series.tail(window) * vol_series.tail(window)).mean() / 1_000_000


def prepare_stock_data(
    file_path: Union[str, Path], min_days: int = 250
) -> Tuple[Optional[pd.DataFrame], Optional[float]]:
    """
    อ่านไฟล์ CSV และเตรียม DataFrame พื้นฐานสำหรับการสแกนหุ้น
    รวมถึงจัดการเรื่องคอลัมน์ที่จำเป็น แปลงชนิดข้อมูล และเช็คความยาวข้อมูล

    Args:
        file_path: Path to the CSV file.
        min_days: Minimum required trading days in the history.

    Returns:
        Tuple of (clean_dataframe, adtv_mb) or (None, None) if criteria not met.
    """
    df = load_full_df(file_path)
    if df is None or len(df) < min_days:
        return None, None

    required_cols = ["Close", "High", "Low", "Volume"]
    if not all(col in df.columns for col in required_cols):
        # Fallback to load_price_volume if only Close/Volume needed?
        # For simplicity, if High/Low is missing, scanners needing them will fail gracefully.
        return None, None

    for col in required_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df.dropna(subset=required_cols, inplace=True)

    if len(df) < min_days:
        return None, None

    adtv_mb = calculate_adtv(df["Close"], df["Volume"], window=50)
    return df, adtv_mb
