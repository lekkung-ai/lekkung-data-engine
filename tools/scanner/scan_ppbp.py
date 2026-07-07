"""
scan_ppbp.py  —  Pocket Pivot Buy Point scanner

เงื่อนไข (ครบทุกข้อ):
  1. วันนี้เป็นวันขึ้น: Close > Open
  2. Volume วันนี้ > Volume ของ ทุก วันที่ลง (Close <= Open) ใน 10 วันก่อนหน้า
  3. Volume วันนี้ > Volume SMA50 (50 วันก่อนหน้า)

Output: ppbp_result.csv
  Columns: Ticker, Price, Volume, Avg50Vol, MaxDownVol10D, Signal
"""
import pandas as pd
import warnings
import sys
from pathlib import Path

warnings.filterwarnings('ignore')

sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import HISTORY_DIR

OUTPUT_FILE = Path(__file__).resolve().parents[2] / 'data' / 'results' / 'ppbp_result.csv'

MIN_BARS = 55  # 50 สำหรับ Volume SMA + 5 buffer


def scan_ppbp():
    if not HISTORY_DIR.exists():
        print('ไม่พบโฟลเดอร์ history')
        _write_empty()
        return

    print('สแกน Pocket Pivot Buy Point...')
    results = []

    for file_path in sorted(HISTORY_DIR.glob('*.csv')):
        ticker = file_path.stem
        try:
            df = pd.read_csv(file_path, encoding='utf-8-sig', index_col=0, parse_dates=True)
            if len(df) < MIN_BARS:
                continue
            if 'Close' not in df.columns or 'Open' not in df.columns or 'Volume' not in df.columns:
                continue

            today = df.iloc[-1]
            close = float(today['Close'])
            open_ = float(today['Open'])
            vol = float(today['Volume'])

            # 1. วันขึ้น
            if close <= open_:
                continue

            # 2. Volume > Volume ทุกวันลงใน 10 วันก่อนหน้า
            window = df.iloc[-11:-1]
            down_days = window[window['Close'] <= window['Open']]
            max_down_vol = float(down_days['Volume'].max()) if len(down_days) > 0 else 0.0
            if len(down_days) > 0 and vol <= max_down_vol:
                continue

            # 3. Volume > Volume SMA50 (คำนวณจาก 50 วันก่อนหน้า ไม่รวมวันนี้)
            vol_sma50 = float(df['Volume'].iloc[-51:-1].mean())
            if vol <= vol_sma50:
                continue

            results.append({
                'Ticker': ticker,
                'Price': round(close, 2),
                'Volume': int(vol),
                'Avg50Vol': int(round(vol_sma50)),
                'MaxDownVol10D': int(round(max_down_vol)),
                'Signal': 'PPBP',
            })

        except Exception:
            pass

    cols = ['Ticker', 'Price', 'Volume', 'Avg50Vol', 'MaxDownVol10D', 'Signal']
    if results:
        df_out = pd.DataFrame(results, columns=cols).sort_values('Ticker').reset_index(drop=True)
        df_out.to_csv(OUTPUT_FILE, index=False, encoding='utf-8-sig')
        print(f'Pocket Pivot: {len(df_out)} ตัว -> {OUTPUT_FILE}')
        print(df_out.to_string(index=False))
    else:
        _write_empty()
        print('ไม่พบหุ้นที่เป็น Pocket Pivot วันนี้')


def _write_empty():
    pd.DataFrame(columns=['Ticker', 'Price', 'Volume', 'Avg50Vol', 'MaxDownVol10D', 'Signal']).to_csv(
        OUTPUT_FILE, index=False, encoding='utf-8-sig'
    )


if __name__ == '__main__':
    scan_ppbp()
