"""เกณฑ์ทั้งหมดของ Overview v2 (หน้า / ของ stockdesk) — ที่เดียว · build_overview.py อ่านจากที่นี่และเขียนลง
overview.json (criteria / rule_version) · ผลสืบ 2026-11-28: stockdesk/scratch/overview_2026-11-28/

ไม่อยู่ในไฟล์นี้ (ใช้ของเดิมของระบบ ห้ามตั้งซ้ำ):
  สภาพคล่องผู้นำ  → config.ADTV_MIN_MB (มูลค่าเฉลี่ย 50 วัน, utils.calculate_adtv)
  สภาพตลาด       → breadth.json market_stage (calculate_breadth.classify_market_stage · การ์ดสภาพตลาดเดิม)
  FTD            → calculate_breadth.compute_ftd_dd_detail (ผ่าน scan_rs7.index_features)
  ช่วงดัชนี A/B/C → scan_rs7.index_features (rs7_config)
  EMA / มูลค่า / perf / วอลุ่มเฉลี่ย → tools/core/indicators.py · กองทุน/REIT → utils.is_fund_ticker
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import ADTV_MIN_MB  # noqa: E402

RULE_VERSION = "v1"

# ── 1. ผู้นำ
LEADER = {
    "adtv50_min_mb": ADTV_MIN_MB,     # มูลค่าเฉลี่ย 50 วัน (ล้านบาท) — เกณฑ์เดียวกับ SEPA/Kell/RS-7
    "stages": ["S.Bull", "Bull"],     # Stage จาก market_stage.json (ค่าเดียวกับหน้า /market-stage)
    "rs_min": 80,                     # RS Rating (rs_ranking.json)
    "price_min": 2.0,                 # บาท
    "max_below_high_pct": 25.0,       # ห่าง high 52 สัปดาห์ (คอลัมน์ 52W_High ในไฟล์ราคา) ไม่เกิน 25%
    "ema_n": 10,
    "tag_up_pct": 3.0,                # up = วันนี้ ≥ +3%
    "pp_window_days": 20,             # ป้าย PP: มีรายการ PP ภายใน 20 วันทำการล่าสุด (รวมวันนี้)
}

# ── 2. Strength / Weakness (เทียบ SET)
GROUP = {
    "member_val20_min_mb": 2.0,       # สมาชิกกลุ่ม: มูลค่าเฉลี่ย 20 วัน ≥ 2 ลบ.
    "member_need_volume": True,       # ... และวันนั้นมีวอลุ่ม > 0
    "min_members": 5,                 # กลุ่มเหลือ < 5 ตัว → ไม่จัดอันดับ (อยู่ใน skipped)
    "vs_set_min": 1.0,                # Strength: ค่ากลาง % กลุ่ม − % SET ≥ +1.0 / Weakness ≤ −1.0
    "share_min": 0.60,                # ... และขึ้น (หรือลง) ≥ 60% ของกลุ่ม
    "perf_n": 5,                      # med5_vs_set: ค่ากลาง perf 5 วันของสมาชิก − perf 5 วันของ SET
    "val_n": 20,
    "top_n": 4,                       # top4 / bot4
    # near (เกือบผ่าน · ฝั่งละไม่เกิน near_max): ผ่านเงื่อนไขหนึ่ง อีกเงื่อนไขขาดไม่เกินระยะนี้
    "near_max": 2,
    "near_vs_set_gap": 0.5,           # ค่ากลาง−SET ขาดไม่เกิน 0.5 จุด
    "near_share_gap": 0.10,           # สัดส่วนขึ้น/ลง ขาดไม่เกิน 10 จุด%
}
# ticker ที่ไม่ใช่หุ้นสามัญ (warrant / DW) — กันไว้อีกชั้น (universe ปกติไม่มี และ sector_map ไม่มี)
NON_COMMON_SUFFIXES = ("-W", "-F", "-R", "-P")

# ── 3. ไฟสัญญาณ
LIGHT = {
    "stage_ok": "Uptrend",            # (a) breadth.json market_stage.stage
    "phase_ok": "C",                  # (b) scan_rs7.index_features phase
    "ftd_within_days": 25,            # (c) FTD ภายใน 25 วันทำการ (รวมวันนี้) ...
    "ma50_min_pct": 40.0,             # ... หรือ % หุ้นเหนือ SMA50 ≥ 40%
}
# ผ่านกี่ข้อ → สี (0 = red, 1–2 = yellow, 3 = green)
LIGHT_COLOR = {0: "red", 1: "yellow", 2: "yellow", 3: "green"}

# ── 4. Volume (มูลค่าซื้อขาย universe ใน SET_INDEX.csv Volume)
VOLUME = {"avg_n": 50}                # ค่าเฉลี่ย 50 วัน "ก่อนหน้า" (indicators.volume_avg_prior)

# ── 5. Scanner Δ (นับเหมือนหน้า Overview: app/page.tsx)
SCANNER_FILES = {
    "sepa": "sepa.json",              # จำนวนแถว
    "kell": "oliver_kell.json",
    "breakout": "breakout.json",
    "dual": "combined.json",          # แถวที่ sepa และ kell เป็น true
    "stage2": "market_stage.json",    # Stage = S.Bull หรือ Bull
}

# ── ประโยคไฟสัญญาณ (template ตายตัว ไม่ใช้ AI) · ตัวแปร: {set_chg} {ma50} {ma200} {stage} {stage_th}
#    {dd} {phase} {phase_th} {n_pass} {missing} {last_ftd_th}
STAGE_TH = {"Uptrend": "ขาขึ้น", "Under Pressure": "ถูกกดดัน", "Correction": "ปรับฐาน"}
PHASE_TH = {"A": "ช่วง A (ใกล้จุดต่ำ)", "B": "ช่วง B (เพิ่งมี FTD หลังจุดต่ำ)", "C": "ช่วง C (ฟื้นตัว)",
            "-": "ไม่เข้าช่วง A/B/C"}
SENTENCE = {
    # สีแดง: ดัชนีวันนี้บวก แต่เช็คไม่ผ่านเลย → เน้นว่าหุ้นส่วนใหญ่ยังไม่ตาม
    ("red", "up"): "ดัชนี {set_chg} แต่หุ้นส่วนใหญ่ยังอยู่ในขาลง — หุ้นเหนือ SMA50 มีแค่ {ma50}%",
    ("red", "down"): "ดัชนี {set_chg} ตลาดยัง{stage_th} (DD {dd} วัน) — หุ้นเหนือ SMA50 มีแค่ {ma50}%",
    ("yellow", "up"): "ดัชนี {set_chg} ตลาดเริ่มดีขึ้นแต่ยังไม่ครบ — ผ่าน {n_pass}/3 ข้อ ยังขาด: {missing}",
    ("yellow", "down"): "ดัชนี {set_chg} ตลาดยังไม่ชัด — ผ่าน {n_pass}/3 ข้อ ยังขาด: {missing}",
    ("green", "up"): "ดัชนี {set_chg} ตลาดเป็นขาขึ้นครบ 3 ข้อ — หุ้นเหนือ SMA50 {ma50}%",
    ("green", "down"): "ดัชนี {set_chg} แต่ภาพรวมยังเป็นขาขึ้นครบ 3 ข้อ — หุ้นเหนือ SMA50 {ma50}%",
}
ACTION = {
    "red": "ตลาดไม่เอื้อ: ลดขนาดไม้ ถือเงินสดเป็นหลัก ดูหุ้นผู้นำไว้ก่อน",
    "yellow": "ตลาดยังไม่ชัด: ไม้เล็ก เลือกเฉพาะหุ้นผู้นำ ตั้งจุดตัดขาดทุนให้ชัด",
    "green": "ตลาดเอื้อ: เพิ่มน้ำหนักกับหุ้นผู้นำที่ผ่านเกณฑ์ได้",
}
# ข้อที่ไม่ผ่าน (ใช้ใน {missing} คั่นด้วย " · ")
MISSING = {
    "market_stage": "สภาพตลาดยังไม่ Uptrend ({stage})",
    "index_phase": "ดัชนียังไม่อยู่ช่วง C ({phase})",
    "ftd_or_breadth": "ไม่มี FTD ใน {ftd_days} วัน และหุ้นเหนือ SMA50 ต่ำกว่า {ma50_min}%",
}
# hint ของแต่ละข้อ (บรรทัดเล็กใต้ข้อในการ์ด)
HINT = {
    "market_stage": "{stage} · DD {dd} วันใน 25 วัน",
    "index_phase": "{phase_th} · ห่าง high {idx_dd}% · จาก low {rally}%",
    "ftd_or_breadth": "FTD ล่าสุด {last_ftd_th} · เหนือ SMA50 {ma50}% · SMA200 {ma200}%",
}
LIGHT_FALLBACK_SENTENCE = "ข้อมูลไฟสัญญาณไม่ครบ — ดูการ์ดสภาพตลาด"
