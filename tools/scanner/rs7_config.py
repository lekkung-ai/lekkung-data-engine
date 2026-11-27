"""เกณฑ์ทั้งหมดของ RS-7 (Minervini 7 รูปแบบหุ้นผู้นำ) — ที่เดียว · scan_rs7.py อ่านจากที่นี่และเขียนลง rs7.json
(หน้าเว็บโชว์เกณฑ์จาก rs7.json["config"]) · ยังไม่ backtest: เป็นค่าตั้งต้น preset SET ตามเอกสารสเปก
(mockups/rs7/rs7-spec.txt) + ที่ผู้ใช้ตัดสิน 2026-11-27

ไม่อยู่ในไฟล์นี้ (ใช้ของเดิมของระบบ ห้ามตั้งซ้ำ):
  FTD            → config.FTD_MIN_GAIN_PCT / FTD_WINDOW_* ผ่าน calculate_breadth.compute_ftd_dd_detail
  RS Rating      → tools/core/rs_score.py (สูตร 3_calculate_rs)
  Trend Template → scan_sepa.trend_template (ข้อ 1–7) + RS Rating ≥ rs_rating_min (ข้อ 8)
  สภาพคล่อง      → config.ADTV_MIN_MB (มูลค่าเฉลี่ย 50 วัน, utils.calculate_adtv)
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import ADTV_MIN_MB  # noqa: E402

RS7_CONFIG = {
    # ── ช่วงดัชนี (SET) — ของแท็บนี้ ไม่แทน market_stage เดิม · ลำดับเมื่อเข้าหลายช่วง: A > C > B
    #    (B = เพิ่งเด้งจาก low จริงและยังไม่เข้าเงื่อนไข C)
    "corr_th": 0.08,              # ดัชนีลงจาก high 252 วัน (ราคาปิด) ≥ 8% = ปรับฐาน
    "near_low": 0.04,             # ช่วง A: ดัชนีห่าง low 126 วันไม่เกิน 4%
    "idx_high_win": 252,
    "idx_low_win": 126,
    "corr_recent_win": 126,       # ช่วง B/C ต้องเคยปรับฐานจริงภายใน 126 วัน
    "phase_b_bsl": [4, 30],       # ช่วง B: แท่งนับจาก low ดัชนี
    "phase_b_ftd_within": 15,     # ... และมี FTD (ของระบบ) ภายใน 15 วัน หลัง low
    "phase_c_rally": 0.10,        # ช่วง C: ดัชนี > SMA50 ที่กำลังขึ้น และ (เด้ง ≥ 10% หรือ bsl > 30)
    "phase_c_bsl_gt": 30,
    "phase_c_sma50_rising_bars": 5,

    # ── หุ้นที่สแกน
    "min_adtv_mb": ADTV_MIN_MB,   # มูลค่าซื้อขายเฉลี่ย 50 วัน (ล้านบาท) — เกณฑ์เดิมของระบบ
    "min_price": 2.0,
    "min_bars": 260,

    # ── ตัวแปรร่วม
    "rs_win": 252,                # RS line high window (tools/core/rs_line.py)
    "rs_near": 0.97,              # RS_near = RS ≥ 97% ของ high
    "pivot_win": 20,              # pivot = High สูงสุด 20 วันก่อนหน้า
    "vol_avg_win": 50,
    "vol_mult": 1.5,              # BO = ปิดเหนือ pivot + วอลุ่ม ≥ 1.5 × เฉลี่ย 50 วัน
    "ants_win": 15,               # มด = จำนวนวันปิดบวกใน 15 วัน
    "rs_rating_min": 70,

    # ── 1 Predictive (ช่วง A)
    "pred_dist": 0.15, "pred_tight10": 0.10, "pred_rel20": 0.10,
    # ── 2 Coincident (ช่วง B)
    "coin_dist": 0.25, "coin_low_lag": 5,
    # ── 3 Confirming (ช่วง C)
    "conf_tight30": 0.25, "conf_tight10": 0.08, "conf_setup_to_pivot": 0.05,
    # ── 4 Sore thumbs (ดัชนี < SMA50 และ 63 วัน ≤ −5%)
    "sore_idx_ret63": -0.05, "sore_ret63": 0.10, "sore_spread63": 0.15, "sore_dist": 0.10,
    "sore_sma50_rising_bars": 10, "sore_rsnh_within": 5,
    # ── 5 Fast rebound (bsl 3–40)
    "fast_bsl": [3, 40], "fast_min": 0.20, "fast_mult": 2.0, "fast_reach_pct": 0.05, "fast_days": 40,
    "fast_low_pad": 5, "fast_pb": [-0.10, -0.03], "fast_ext_sma20": 0.10,
    # ── 6 Lead and rest (เด้ง ≥ 8%, bsl 10–120)
    "rest_rally": 0.08, "rest_bsl": [10, 120], "rest_rs_at_low": 0.97, "rest_tight20": 0.15,
    "rest_dist": 0.15, "rest_rsgap": [-0.15, -0.03],
    # ── 7 Failure reset — Pivot reset / Base reset
    "piv_bo_age": [3, 40], "piv_squat": 0.03, "piv_base_win": 20, "piv_tight10": 0.10, "piv_floor": 0.93,
    "piv_min_rs_rating": 70, "piv_max_dist": 0.25,     # ตัวกรองเดียวกับ Base reset (ผู้ใช้ตัดสิน 2026-11-27)
    "base_bo_lookback": 90, "base_min_days": 15, "base_win": 15, "base_tight15": 0.12,
    "base_min_rs_rating": 70, "base_max_dist": 0.25,   # ตัวกรองที่ผู้ใช้เพิ่ม (กันหุ้นอย่าง MTC)
    "rs_up_bars": 20,

    # ── ผลลัพธ์
    "series_days": 252,
}
