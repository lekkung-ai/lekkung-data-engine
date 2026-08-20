"""
Master Pipeline Runner for Lekkung Quant.
Provides a CLI to run various data fetching and scanning modules.
"""
import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Force UTF-8 encoding for standard output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore

# 🔌 ดึงพิกัดแผนกต่างๆ มาจาก config
from config import (AI_AGENTS_DIR, CORE_DIR, LOG_FILE, NEWS_TOOLS_DIR,
                    SCANNER_DIR, TOOLS_DIR, DATA_DIR, p)

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# สคริปต์ที่ returncode != 0 ในรอบนี้ - pipeline ยังรันต่อจนจบ (scan ตัวหนึ่งพัง
# ไม่ควรทำให้ทั้งรอบล่ม) แต่ต้อง "บันทึกไว้" ไม่ใช่กลืนเงียบ เดิม run_script แค่
# log แล้ว return ทำให้ scan ล่มทั้งตัวก็ยังขึ้น pipeline_status = success
FAILED_STEPS: list[str] = []
RUN_STATUS_FILE = DATA_DIR / "results" / "run_status.json"


def run_script(script_path: Path) -> None:
    """
    Run a python script using subprocess.
    If the script fails or is not found, it logs the error and exits the program.
    """
    logger.info("=" * 60)
    logger.info(f"▶️ กำลังสั่งรัน: {script_path.name}")
    logger.info("=" * 60)

    if not script_path.exists():
        logger.error(f"❌ Error: ไม่พบไฟล์ {script_path.name} ใน {script_path.parent}")
        sys.exit(1)

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    
    result = subprocess.run([sys.executable, p(script_path)], env=env)

    if result.returncode != 0:
        logger.error(
            f"❌ Error: เกิดข้อผิดพลาดขณะรัน {script_path.name} (ข้ามไปทำสคริปต์ถัดไป)"
        )
        FAILED_STEPS.append(script_path.name)
        return

    logger.info(f"✅ {script_path.name} ทำงานเสร็จสมบูรณ์")


def write_run_status(mode: str) -> None:
    """
    เขียนสรุปผลรอบนี้ลง data/results/run_status.json ให้ขั้นตอนถัดไปอ่านต่อได้
    ว่ามี step ไหนพังบ้าง - run_all.py ยัง exit 0 เสมอ (ไม่หยุดกลางคัน ไม่ทำให้
    workflow ล้มทั้งรอบเพราะ scan ตัวเดียว) แต่จะไม่เงียบอีกต่อไป
    """
    payload = {
        "finished_at": datetime.now(timezone(timedelta(hours=7))).strftime("%Y-%m-%dT%H:%M:%S+07:00"),
        "mode": mode,
        "failed_steps": FAILED_STEPS,
    }
    try:
        RUN_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        RUN_STATUS_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:  # noqa: BLE001
        logger.error(f"⚠️ เขียน {RUN_STATUS_FILE.name} ไม่สำเร็จ: {e}")

    if FAILED_STEPS:
        logger.error("=" * 60)
        logger.error(f"❌ PIPELINE FAILED STEPS ({len(FAILED_STEPS)}): {', '.join(FAILED_STEPS)}")
        logger.error("   ข้อมูลของ scan เหล่านี้อาจไม่อัปเดตรอบนี้ (MISSING GUARD จะคงไฟล์เดิมไว้)")
        logger.error("=" * 60)
    else:
        logger.info("✅ ทุก step รันผ่านหมด ไม่มี failed step")


def main():
    parser = argparse.ArgumentParser(
        description="Lekkung Quant - Master Pipeline Runner"
    )
    parser.add_argument(
        "mode",
        nargs="?",
        choices=["stock", "scan", "news", "all"],
        help="โหมด: stock, scan, news, หรือ all",
    )

    args = parser.parse_args()

    if not args.mode:
        print("\n" + "🌟" * 20)
        print("   Lekkung Quant - Master Runner")
        print("🌟" * 20)
        print("1. 📥 อัปเดตราคาหุ้น (Stock)")
        print("2. 📊 รันระบบสแกนหุ้น (Scan)")
        print("3. 📰 ดึงข่าวล่าสุด (News)")
        print("4. 🚀 รันทั้งหมดจบในปุ่มเดียว (All)")
        print("-" * 40)

        choice = input("👉 โปรดระบุหมายเลขที่ต้องการ (1-4): ").strip()
        mode_map = {"1": "stock", "2": "scan", "3": "news", "4": "all"}
        args.mode = mode_map.get(choice, "all")

    stock_scripts = [
        CORE_DIR / "1_get_symbols.py",
        CORE_DIR / "2_download_history.py",
        CORE_DIR / "3_calculate_rs.py",
        CORE_DIR / "4_calculate_sector_rs.py",
        TOOLS_DIR / "market" / "download_nvdr.py",
    ]

    scan_scripts = [
        SCANNER_DIR / "scan_sepa.py",
        SCANNER_DIR / "scan_wyckoff_4stages.py",
        SCANNER_DIR / "scan_pine_stages.py",
        SCANNER_DIR / "scan_accumulation_breakout.py",
        SCANNER_DIR / "sepa_stage2_combo.py",
        SCANNER_DIR / "weinstein_stage_analysis.py",
        SCANNER_DIR / "scan_oliver_kell.py",
        SCANNER_DIR / "scan_weinstein_breakout.py",
        SCANNER_DIR / "scan_lekkung_growth.py",  # 👈 เพิ่ม Lekkung Scan
        SCANNER_DIR / "scan_canslim.py",         # 👈 เพิ่ม CANSLIM Scan
        SCANNER_DIR / "scan_stage_all.py",       # 👈 กู้คืนให้ Vercel
        SCANNER_DIR / "scan_ppbp.py",            # 👈 กู้คืนให้ Vercel
        DATA_DIR / "results" / "convert_to_json.py", # 👈 สร้างไฟล์ JSON สำหรับ Dashboard
        SCANNER_DIR / "calculate_breadth.py",        # 👈 SET Market Breadth + FTD/DD + sparklines (/breadth page)
        SCANNER_DIR / "build_topmover_charts.py",    # 👈 mini candlestick + EMA200 bundle (/top-movers page)
        SCANNER_DIR / "fetch_topmover_ranking.py",   # 👈 today's ranking snapshot, archived daily for /top-movers ย้อนหลัง view
        TOOLS_DIR / "macro" / "fetch_macro.py",       # 👈 Commodity/FX (/macro page)
    ]

    news_scripts = [
        NEWS_TOOLS_DIR / "fetch_sepa_news.py",
        NEWS_TOOLS_DIR / "test_feed.py",
    ]



    logger.info(f"⚡ โหมดที่เลือก: [{args.mode.upper()}]")

    if args.mode in ["stock", "all"]:
        for script in stock_scripts:
            run_script(script)

    if args.mode in ["scan", "all"]:
        for script in scan_scripts:
            run_script(script)

    if args.mode in ["news", "all"]:
        for script in news_scripts:
            run_script(script)



    write_run_status(args.mode)

    logger.info("🎉 จบการทำงาน Master Pipeline เรียบร้อยแล้วครับนาย!")


if __name__ == "__main__":
    main()
