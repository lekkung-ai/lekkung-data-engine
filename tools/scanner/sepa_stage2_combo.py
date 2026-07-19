import sys
import warnings
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")

# Force UTF-8 encoding for standard output/error on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore


# 🔌 เชื่อมต่อ config.py
sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import ADTV_MIN_MB, COMBO_FILE, HISTORY_DIR, RS_FILE  # type: ignore
from utils import calculate_adtv, load_full_df, load_price_volume  # type: ignore


def run_combo_scanner():
    if not RS_FILE.exists() or not HISTORY_DIR.exists():
        print("❌ ขาดไฟล์ตั้งต้น (RS Ranking หรือโฟลเดอร์ History)")
        return

    try:
        df_rs = pd.read_csv(RS_FILE)
        rs_dict = dict(zip(df_rs["Ticker"].astype(str).str.upper(), df_rs["RS_Rating"]))
    except Exception as e:
        print(f"❌ เกิดข้อผิดพลาดในการอ่านไฟล์ RS Ranking: {e}")
        return

    print("🚀 [Quant Engine] รันโมเดลผสม (SEPA + Weinstein Stage 2 + RS) ...")
    combo_passed = []

    for ticker, rs_rating in rs_dict.items():
        file_path = HISTORY_DIR / f"{ticker}.csv"
        if not file_path.exists():
            continue

        try:
            price_series, vol_series = load_price_volume(file_path, min_length=252)
            if price_series is None:
                continue

            # 🛡️ เช็คสภาพคล่อง (ADTV) เฉลี่ย 50 วันย้อนหลัง
            adtv_mb = calculate_adtv(price_series, vol_series, window=50)
            if adtv_mb < ADTV_MIN_MB:
                continue

            sma_50 = price_series.rolling(window=50).mean()
            sma_150 = price_series.rolling(window=150).mean()
            sma_200 = price_series.rolling(window=200).mean()

            curr_p = float(price_series.iloc[-1])
            c_sma50 = float(sma_50.iloc[-1])
            c_sma150 = float(sma_150.iloc[-1])
            c_sma200 = float(sma_200.iloc[-1])

            # True 52-week high/low from the day's actual intraday High/Low -
            # load_price_volume only carries Close/Volume, so re-read the raw
            # OHLC columns here rather than using Close for the high/low band.
            full_df = load_full_df(file_path)
            high_52w = float(pd.to_numeric(full_df["High"], errors="coerce").tail(252).max())
            low_52w = float(pd.to_numeric(full_df["Low"], errors="coerce").tail(252).min())

            sepa_1 = curr_p > c_sma150 and curr_p > c_sma200
            sepa_2 = c_sma150 > c_sma200
            sepa_3 = curr_p > c_sma50
            sepa_4 = c_sma50 > c_sma150 and c_sma50 > c_sma200
            sepa_5 = curr_p >= (1.30 * low_52w)
            sepa_6 = curr_p >= (0.75 * high_52w)

            past_sma150 = float(sma_150.iloc[-20])
            past_sma200 = float(sma_200.iloc[-20])
            sepa_7 = c_sma200 > past_sma200
            slope_150_pct = ((c_sma150 - past_sma150) / past_sma150) * 100

            if curr_p > c_sma150 and slope_150_pct > 1.0:
                stage = "Stage 2"
            elif curr_p < c_sma150 and slope_150_pct < -1.0:
                stage = "Stage 4"
            elif curr_p > c_sma150 and slope_150_pct <= 1.0:
                stage = "Stage 3"
            else:
                stage = "Stage 1"

            if (
                all([sepa_1, sepa_2, sepa_3, sepa_4, sepa_5, sepa_6, sepa_7])
                and stage == "Stage 2"
            ):
                pct_from_high = ((curr_p - high_52w) / high_52w) * 100
                combo_passed.append(
                    {
                        "Ticker": ticker,
                        "RS": rs_rating,
                        "Stage": stage,
                        "Price": round(curr_p, 2),
                        "SL(-8%)": round(curr_p * 0.92, 2),
                        "TP(+16%)": round(curr_p * 1.16, 2),
                        "SMA_50": round(c_sma50, 2),
                        "SMA_150": round(c_sma150, 2),
                        "SMA_200": round(c_sma200, 2),
                        "52W_High": round(high_52w, 2),
                        "%_From_High": f"{round(pct_from_high, 2)}%",
                        "ADTV(MB)": round(adtv_mb, 1),
                    }
                )
        except Exception as e:
            # print(f"⚠️ Error processing {ticker}: {e}")
            pass

    print("\n" + "=" * 90)
    print("🔥 SEPA + WEINSTEIN STAGE 2 ELITE SQUAD 🔥")
    print("=" * 90)

    if combo_passed:
        df_final = pd.DataFrame(combo_passed).sort_values(by="RS", ascending=False)
        df_final.to_csv(COMBO_FILE, index=False, encoding="utf-8-sig")
        df_final.reset_index(drop=True, inplace=True)
        df_final.index = df_final.index + 1

        print(df_final.to_string())
        print("-" * 90)
        print(f"✨ คัดได้หุ้นระดับพระกาฬทั้งหมด: {len(df_final)} ตัว")
    else:
        print("📉 ตลาดหมีจัด ไม่มีหุ้นตัวไหนผ่านเกณฑ์ควบ 2 สำนักเลย")


if __name__ == "__main__":
    run_combo_scanner()
