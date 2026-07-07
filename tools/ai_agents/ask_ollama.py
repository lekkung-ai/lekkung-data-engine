import sys
from pathlib import Path

import pandas as pd
import requests

# 🔌 เชื่อมต่อ config.py
sys.path.append(str(Path(__file__).resolve().parents[2]))
from config import OLLAMA_MODEL, OLLAMA_URL, SEPA_FILE


def analyze_with_ollama():
    if not SEPA_FILE.exists():
        print(f"❌ ไม่พบไฟล์ '{SEPA_FILE.name}' กรุณารันสแกน SEPA ก่อนครับ")
        return

    # 1. อ่านข้อมูลหุ้นที่ผ่านเกณฑ์
    try:
        df = pd.read_csv(SEPA_FILE)
        if df.empty:
            print("📉 ไม่มีหุ้นผ่านเกณฑ์ให้วิเคราะห์")
            return

        # เลือกเอาแค่ Top 10 มาวิเคราะห์ ป้องกัน Context ยาวเกินไป
        top_stocks = df.head(10).to_string(index=False)
    except Exception as e:
        print(f"❌ Error อ่านไฟล์: {e}")
        return

    # 2. สร้าง Prompt สำหรับ AI
    system_prompt = """
    คุณคือนักวิเคราะห์หุ้นสาย Quant และ Trend Following ระดับโลก 
    ฉันจะส่งตารางรายชื่อหุ้นที่ผ่านเกณฑ์ Minervini SEPA (Trend Template) มาให้
    หน้าที่ของคุณคือ:
    1. สรุปภาพรวมว่าหุ้นกลุ่มไหน/อุตสาหกรรมไหน กำลังมาแรงที่สุดจากรายชื่อนี้
    2. แนะนำข้อควรระวังในการเทรดหุ้นลักษณะนี้ในสภาวะตลาดปัจจุบัน
    ตอบเป็นภาษาไทยให้อ่านง่ายและเป็นมืออาชีพ
    """

    full_prompt = f"{system_prompt}\n\nข้อมูลหุ้น Top 10 ของวันนี้:\n{top_stocks}"

    print(
        f"🤖 กำลังส่งข้อมูลให้ Ollama (Model: {OLLAMA_MODEL}) วิเคราะห์... อาจใช้เวลาสักครู่"
    )

    # 3. ยิง Request ไปที่ Ollama
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": full_prompt,
        "stream": False,  # รอรับคำตอบรวดเดียว
    }

    try:
        response = requests.post(OLLAMA_URL, json=payload)
        response.raise_for_status()

        result = response.json()
        ai_answer = result.get("response", "")

        print("\n" + "=" * 80)
        print("🎯 [OLLAMA ANALYST INSIGHT]")
        print("=" * 80)
        print(ai_answer)
        print("=" * 80)

    except requests.exceptions.ConnectionError:
        print("❌ เชื่อมต่อ Ollama ไม่ได้! ตรวจสอบว่าเปิดโปรแกรม Ollama ไว้หรือยัง")
    except Exception as e:
        print(f"❌ เกิดข้อผิดพลาด: {e}")


if __name__ == "__main__":
    analyze_with_ollama()
