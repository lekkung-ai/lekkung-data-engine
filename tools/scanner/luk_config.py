"""เกณฑ์ทั้งหมดของ Martin Luk scan (SET) — ที่เดียว · scan_luk.py อ่านจากที่นี่และเขียนลง luk.json["config"]

ที่มา: mockups/luk/scan_martin_luk.py (Params / MarketCfg "thailand") + ที่ผู้ใช้ตัดสิน 2026-11-28:
ผู้นำ = Top 30 ตาม RS Rating ของระบบ · ไม่มีตัวกรอง market cap · stop ขั้นต่ำ 3 tick · ฝั่ง short = รายการหุ้นอ่อนแรง
ไม่อยู่ในไฟล์นี้ (ใช้ของเดิม): RS Rating (rs_ranking.csv / tools/core/rs_score.py) · tick size (vcp.thai_tick)
"""

LUK_CONFIG = {
    # ── universe / ตัวกรอง
    "min_bars": 80,               # ต้องมีประวัติอย่างน้อยเท่านี้ (เหมือน build() ของต้นแบบ)
    "min_price": 2.0,             # บาท
    "min_value20_mb": 50.0,       # มูลค่าซื้อขายเฉลี่ย 20 วัน (ล้านบาท)
    "min_adr_pct": 3.0,           # ADR 20 วัน (%)
    "leader_top_n": 30,           # ผู้นำ = Top N ตาม RS Rating ในหุ้นที่ผ่านตัวกรอง

    # ── ตัวชี้วัด
    "ema_fast": 9, "ema_mid": 21, "ema_slow": 50,
    "adr_win": 20, "value_win": 20, "vol_avg_win": 50,
    "ema_rising_bars": 5,         # EMA21 สูงกว่า 5 แท่งก่อน = กำลังขึ้น

    # ── LONG_PULLBACK (เฉพาะผู้นำ)
    "pb_high_win": 20, "pb_min_bars": 3, "pb_max_bars": 8, "pb_zone_adr": 0.6, "pb_avwap_win": 63,
    # ── EPISODIC_PIVOT
    "ep_min_gap_pct": 8.0, "ep_min_rvol": 3.0, "ep_record_win": 252,
    # ── SHORT_WEAK_BOUNCE (หุ้นอ่อนแรง)
    "sb_leader_perf6m": 30.0, "sb_leader_near_high": 0.97, "sb_leader_high_win": 90, "sb_year_win": 252,
    "sb_breakdown_lookback": 15, "sb_breakdown_vol_mult": 1.3, "sb_bounce_min": 2, "sb_bounce_max": 5,
    "sb_zone_adr": 1.0, "sb_light_vol": 0.9,
    # ── PARABOLIC_SHORT (หุ้นอ่อนแรง)
    "para_min_up_bars": 5, "para_up_win": 7, "para_min_5d_gain": 25.0, "para_min_ext_adr": 3.0,
    "para_base_from": 27, "para_base_to": 7,   # ADR ก่อนวิ่ง = แท่ง −27 ถึง −8

    # ── stop / ขนาด position (ฝั่ง long เท่านั้น)
    "intraday_stop_pct": 1.5,     # stop ปกติจากกราฟ 1/5 นาทีของ Luk
    "min_stop_ticks": 3,          # stop ต้องห่างอย่างน้อย 3 tick (SET)
    "daily_stop_floor_pct": 0.25,
    "risk_pct": 0.3,              # ความเสี่ยงต่อไม้ (% ของพอร์ต) ตั้งต้น
    "max_position_pct": 35.0,

    # ── regime (ของแท็บนี้ · คนละอย่างกับ market_stage และช่วง A/B/C)
    "reg_long_above21": 60.0, "reg_long_stacked": 55.0, "reg_short_above21": 30.0,
    "reg_def_above21": 40.0, "reg_def_failed": 25.0, "reg_half_risk_mult": 0.5,
    "fail_high_win_short": 5, "fail_high_win_long": 20,

    # ── คำเตือนธีม
    "theme_min_count": 3,         # ฝั่ง long วันเดียวกัน subsector เดียวกัน ≥ 3 ตัว
}
