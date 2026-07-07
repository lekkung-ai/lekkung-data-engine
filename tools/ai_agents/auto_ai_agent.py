import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
import schedule

# 🔌 เชื่อมต่อ config.py
sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import (COMBO_FILE, CORE_DIR, LINE_TOKEN, NEWS_TOOLS_DIR,
                    OLLAMA_MODEL, OLLAMA_URL, SCANNER_DIR, p)


def send_line_notify(message):
    """ส่งข้อความเข้า LINE"""
    if not LINE_TOKEN or LINE_TOKEN == "ใส่_LINE_TOKEN_ของคุณที่นี่":
        print("⚠️ ข้ามการส่ง LINE (ยังไม่ได้ใส่ Token ใน config.py)")
        return
    url = "https://notify-api.line.me/api/notify"
    headers = {"Authorization": f"Bearer {LINE_TOKEN}"}
    requests.post(url, headers=headers, data={"message": message})


def run_script(script_path):
    """สั่งรันไฟล์ Python ทีละไฟล์จาก Path ที่ระบุ"""
    print(f"\n⚙️ กำลังรัน: {script_path.name}...")
    try:
        # สั่งรันไฟล์ตามพิกัดที่แท้จริง
        result = subprocess.run(
            ["python", p(script_path)], capture_output=True, text=True, check=True
        )
        print(f"✅ {script_path.name} ทำงานเสร็จสมบูรณ์")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ เกิดข้อผิดพลาดใน {script_path.name}:\n{e.stderr}")
        return False


def analyze_with_ai():
    """ให้ AI อ่านไฟล์ Combo และเขียนสรุป"""
    print("\n🤖 กำลังปลุก AI ให้ตื่นมาวิเคราะห์ข้อมูล...")

    if not COMBO_FILE.exists():
        return "ตลาดหมีจัด วันนี้ไม่มีหุ้นผ่านเกณฑ์ Stage 2 เลยครับนาย!"

    try:
        df = pd.read_csv(COMBO_FILE).head(5)
        if df.empty:
            return "วันนี้ไม่มีหุ้นผ่านเกณฑ์ครับ"

        stock_data_text = df.to_string(index=False)

        prompt = f"""
        คุณคือ Quant Analyst ระดับโลก นี่คือรายชื่อหุ้น Top 5 ของไทยวันนี้ที่ผ่านกฎ Minervini SEPA และอยู่ใน Stage 2 ขาขึ้นแข็งแกร่ง
        ข้อมูลหุ้น:
        {stock_data_text}
        
        หน้าที่ของคุณ (ตอบเป็นภาษาไทยสั้นๆ อ่านง่าย):
        1. สรุปภาพรวมว่าหุ้นกลุ่มนี้แข็งแกร่งแค่ไหน
        2. เลือกหุ้นที่ดูแข็งแกร่งที่สุด 1 ตัว พร้อมบอกเหตุผล
        3. ให้คำแนะนำสั้นๆ ว่าจุดตัดขาดทุน (Stop Loss) ควรอยู่แถวไหน (อ้างอิงจาก SMA)
        """

        payload = {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False}
        response = requests.post(OLLAMA_URL, json=payload)
        response.raise_for_status()

        ai_insight = response.json().get("response", "")
        return f"\n📊 [AI Daily Quant Report]\n{ai_insight}"

    except Exception as e:
        return f"AI ขัดข้อง: {e}"


def daily_routine():
    """กระบวนการทำงานหลัก (Workflow) เรียงคิวแบบ Full Pipeline"""
    print(
        f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🚀 เริ่มต้นกระบวนการสแกนหุ้นประจำวัน..."
    )

    # 📌 จัดคิวการทำงาน (อ้างอิงพิกัดจาก config)
    scripts_to_run = [
        # 1. แผนกข้อมูลดิบ
        CORE_DIR / "1_get_symbols.py",
        CORE_DIR / "2_download_history.py",
        CORE_DIR / "3_calculate_rs.py",
        # 2. แผนกสแกนเนอร์
        SCANNER_DIR / "scan_sepa.py",
        SCANNER_DIR / "scan_wyckoff_4stages.py",
        SCANNER_DIR / "scan_pine_stages.py",
        SCANNER_DIR / "scan_accumulation_breakout.py",
        SCANNER_DIR / "sepa_stage2_combo.py",
        SCANNER_DIR / "weinstein_stage_analysis.py",
        # 3. แผนกข่าวสาร
        NEWS_TOOLS_DIR / "fetch_sepa_news.py",
        NEWS_TOOLS_DIR / "test_feed.py",
    ]

    for script in scripts_to_run:
        if script.exists():
            success = run_script(script)
            if not success:
                send_line_notify(f"❌ ระบบสแกนพังที่ขั้นตอน: {script.name}")
                return
        else:
            print(f"⚠️ ข้ามไฟล์ {script.name} (หาไฟล์ไม่พบ)")

    # ให้ AI วิเคราะห์ผลลัพธ์
    ai_summary = analyze_with_ai()
    print(ai_summary)

    # ส่งรายงานเข้า LINE
    send_line_notify(ai_summary)
    print("✨ จบกระบวนการประจำวัน รอทำงานใหม่พรุ่งนี้...")


if __name__ == "__main__":
    print("🕒 เปิดระบบ AI Agent รอเวลาทำงานตอน 17:30 น. ทุกวันจันทร์-ศุกร์...")

    # ตั้งเวลา
    schedule.every().monday.at("17:30").do(daily_routine)
    schedule.every().tuesday.at("17:30").do(daily_routine)
    schedule.every().wednesday.at("17:30").do(daily_routine)
    schedule.every().thursday.at("17:30").do(daily_routine)
    schedule.every().friday.at("17:30").do(daily_routine)

    while True:
        schedule.run_pending()
        time.sleep(60)
