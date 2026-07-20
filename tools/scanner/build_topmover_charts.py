"""
build_topmover_charts.py
สร้าง mini-candlestick data (OHLC ย้อนหลัง N วัน + EMA200) สำหรับทุก ticker ใน
universe เดียวกับ sparklines.json - ให้หน้า Top Movers (และหน้าอื่นในอนาคต)
วาด candlestick + เส้น EMA200 ได้จากไฟล์ bundle เดียว แทนที่จะยิง fetch ราคา
ทีละตัวต่อแถว (ของเดิมที่ MiniCandleChart เคยทำผ่าน /api/chart/[ticker] -
ช้าเพราะยิง Yahoo ทีละ ticker ตอน component mount)

เขียนผลเป็น topmover_charts.json ไปที่ data/results/output/
(pipeline เดิม - update_stockdesk.bat และ daily-scan.yml - copy *.json จากโฟลเดอร์นี้
ไป stockdesk/data/scans/ อยู่แล้ว ไม่ต้องแก้ path เพิ่ม)

Usage:
    python build_topmover_charts.py                # write real output
    python build_topmover_charts.py --dry-run       # compute + print summary, write nothing
    python build_topmover_charts.py --out <dir>     # write into <dir> instead of the real results/output tree
"""
import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import json
from datetime import datetime, timezone, timedelta

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import HISTORY_DIR, RESULTS_DIR, BREADTH_MIN_HISTORY_DAYS

BKK_TZ = timezone(timedelta(hours=7))
CHART_DAYS = 30       # bars kept per ticker - matches SPARKLINE_DAYS convention
EMA_PERIOD = 200       # matches the purple EMA line the old MiniCandleChart drew


def now_iso() -> str:
    return datetime.now(BKK_TZ).strftime("%Y-%m-%dT%H:%M:%S+07:00")


def ema(closes: list[float], period: int) -> list[float | None]:
    """Same seed-at-first-value EMA the old client-side MiniCandleChart used,
    so a ticker with a long enough history draws an identical line to before."""
    k = 2 / (period + 1)
    out: list[float | None] = [None] * len(closes)
    e = closes[0]
    for i, c in enumerate(closes):
        e = c if i == 0 else c * k + e * (1 - k)
        if i >= period - 1:
            out[i] = e
    return out


def load_ticker_ohlc(file_path: Path) -> pd.DataFrame | None:
    df = pd.read_csv(file_path)
    df = df.rename(columns={df.columns[0]: "Date"})
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    for col in ("Open", "High", "Low", "Close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["Date", "Open", "High", "Low", "Close"]).sort_values("Date")
    if len(df) < BREADTH_MIN_HISTORY_DAYS:
        return None
    return df


def build_chart_entry(df: pd.DataFrame) -> dict:
    closes = df["Close"].tolist()
    ema_all = ema(closes, EMA_PERIOD)

    tail = df.tail(CHART_DAYS)
    ema_tail = ema_all[-len(tail):]

    bars = [
        {
            "date": d.strftime("%Y-%m-%d"),
            "open": round(float(o), 2),
            "high": round(float(h), 2),
            "low": round(float(l), 2),
            "close": round(float(c), 2),
        }
        for d, o, h, l, c in zip(tail["Date"], tail["Open"], tail["High"], tail["Low"], tail["Close"])
    ]
    ema200 = [None if v is None else round(float(v), 3) for v in ema_tail]

    return {"bars": bars, "ema200": ema200}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    out_dir = Path(args.out) if args.out else (RESULTS_DIR / "output")

    print("📊 [TopMoverCharts] กำลังโหลด price history ทั้ง universe...")
    data: dict[str, dict] = {}
    skipped = 0
    for file_path in sorted(HISTORY_DIR.glob("*.csv")):
        ticker = file_path.stem.strip()
        try:
            df = load_ticker_ohlc(file_path)
        except Exception:
            df = None
        if df is None:
            skipped += 1
            continue
        data[ticker] = build_chart_entry(df)

    print(f"  ✓ universe: {len(data)} ticker (>= {BREADTH_MIN_HISTORY_DAYS} วัน), ข้าม {skipped} ticker ประวัติสั้นไป")

    out = {
        "generated_at": now_iso(),
        "days": CHART_DAYS,
        "ema_period": EMA_PERIOD,
        "data": data,
    }

    if args.dry_run:
        sample_ticker = next(iter(data), None)
        print(json.dumps({
            "generated_at": out["generated_at"],
            "days": out["days"],
            "ema_period": out["ema_period"],
            "ticker_count": len(data),
            "sample": {sample_ticker: data[sample_ticker]} if sample_ticker else {},
        }, ensure_ascii=False, indent=2))
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "topmover_charts.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"  ✓ topmover_charts.json: {len(data)} ticker, {out_path.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
