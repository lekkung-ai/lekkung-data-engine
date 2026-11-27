"""Unit tests สำหรับ calculate_breadth.compute_ftd_dd (FTD / rally attempt) — series สังเคราะห์
รันด้วย `python test_ftd_rally_expiry.py` หรือ pytest.

เกณฑ์ที่ใช้มาจาก config.py ตามจริง (FTD_MIN_GAIN_PCT / FTD_WINDOW_START_DAY / FTD_WINDOW_END_DAY)
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from calculate_breadth import compute_ftd_dd  # noqa: E402
from config import FTD_MIN_GAIN_PCT, FTD_WINDOW_END_DAY, FTD_WINDOW_START_DAY  # noqa: E402

UP = 0.10            # % วันบวกเล็กๆ (ไม่ถึงเกณฑ์ FTD)
BIG = FTD_MIN_GAIN_PCT + 0.5
DOWN = -0.10         # % วันลบเล็กๆ (ไม่ถึงเกณฑ์ DD ที่ 0.2% → ไม่มี DD marker มากวน)


def series(moves, start=100.0):
    """moves = % เปลี่ยนแปลงรายวัน (แท่งแรก = start) · Low = Close × 0.999 · Volume เพิ่มทุกวัน (higher_vol เสมอ)."""
    close = [start]
    for m in moves:
        close.append(close[-1] * (1 + m / 100))
    n = len(close)
    return pd.DataFrame({"Close": close, "Low": [c * 0.999 for c in close], "Volume": [1000.0 + i for i in range(n)]})


def rally(day_moves):
    """แท่ง 0 = ฐาน, แท่ง 1 = ปิดลบ (pullback) แล้ว day_moves[0] = วันที่ 1 ของ rally attempt."""
    return [DOWN] + list(day_moves)


def ftd_bars(moves):
    m = compute_ftd_dd(series(moves))
    return [i for i, x in enumerate(m) if x == "FTD"]


def day_bar(day):  # bar index ของ "วันที่ day" ใน rally()
    return day + 1


def test_ftd_on_first_allowed_day():
    moves = rally([UP] * (FTD_WINDOW_START_DAY - 1) + [BIG])
    assert ftd_bars(moves) == [day_bar(FTD_WINDOW_START_DAY)]


def test_no_ftd_before_window():
    moves = rally([UP] * (FTD_WINDOW_START_DAY - 2) + [BIG] + [UP] * 3)
    assert ftd_bars(moves) == []


def test_ftd_on_last_allowed_day():
    moves = rally([UP] * (FTD_WINDOW_END_DAY - 1) + [BIG])
    assert ftd_bars(moves) == [day_bar(FTD_WINDOW_END_DAY)]


def test_no_ftd_after_window_attempt_expired():
    # วันที่ 11 บวกแรง ไม่นับ และวันที่ขึ้นต่อเนื่องหลังหมดอายุไม่เริ่ม rally ใหม่ (เดิม: ค้างนับไปตลอด)
    moves = rally([UP] * FTD_WINDOW_END_DAY + [BIG] + [UP] * 5 + [BIG] + [UP] * 20 + [BIG])
    assert ftd_bars(moves) == []


def test_expired_then_pullback_starts_new_attempt():
    # หมดอายุ (วันที่ 10 ปิดบวก) → ขึ้นต่ออีก 3 วัน → ปิดลบ 1 วัน → วันบวกถัดไป = วันที่ 1 ใหม่ → FTD วันที่ 4
    old = [UP] * FTD_WINDOW_END_DAY + [UP] * 3 + [DOWN]
    new = [UP] * (FTD_WINDOW_START_DAY - 1) + [BIG]
    moves = rally(old + new)
    assert ftd_bars(moves) == [len(moves)]


def test_day10_down_close_next_up_day_is_new_day1():
    # วันที่ 10 ปิดลบ (ไม่หลุด low วันที่ 1) → หมดอายุ แล้ววันบวกถัดไปเป็นวันที่ 1 ทันที
    old = [UP] * (FTD_WINDOW_END_DAY - 1) + [DOWN]
    new = [UP] * (FTD_WINDOW_START_DAY - 1) + [BIG]
    moves = rally(old + new)
    assert ftd_bars(moves) == [len(moves)]


def test_new_attempt_count_starts_fresh_after_expiry():
    # ถ้านับต่อจากรอบเดิม BIG นี้จะตกวันที่ 2 ของรอบใหม่ → ต้องไม่เกิด
    old = [UP] * FTD_WINDOW_END_DAY + [DOWN]
    moves = rally(old + [UP, BIG])
    assert ftd_bars(moves) == []


def test_undercut_day1_low_restarts_count():
    # วันที่ 1 → ขึ้น 2 วัน → หลุด low วันที่ 1 → วันบวกถัดไปเป็นวันที่ 1 ใหม่ → FTD วันที่ 4 ของรอบใหม่
    old = [UP, UP, UP, -1.0]
    new = [UP] * (FTD_WINDOW_START_DAY - 1) + [BIG]
    moves = rally(old + new)
    assert ftd_bars(moves) == [len(moves)]


def test_after_ftd_down_close_starts_watching_again():
    first = [UP] * (FTD_WINDOW_START_DAY - 1) + [BIG]
    later = [UP, UP, DOWN] + [UP] * (FTD_WINDOW_START_DAY - 1) + [BIG]
    moves = rally(first + later)
    assert ftd_bars(moves) == [day_bar(FTD_WINDOW_START_DAY), len(moves)]


def test_after_ftd_no_second_ftd_without_pullback():
    moves = rally([UP] * (FTD_WINDOW_START_DAY - 1) + [BIG] + [UP] * 5 + [BIG])
    assert ftd_bars(moves) == [day_bar(FTD_WINDOW_START_DAY)]


def test_ftd_needs_higher_volume():
    df = series(rally([UP] * (FTD_WINDOW_START_DAY - 1) + [BIG]))
    df.loc[day_bar(FTD_WINDOW_START_DAY), "Volume"] = 1.0
    assert "FTD" not in compute_ftd_dd(df)


def test_dd_marker_independent_of_rally_state():
    # DD นับทุกครั้ง: ปิดลบ ≥ 0.2% + volume มากกว่า ไม่ว่าอยู่สถานะไหน (รวมช่วงหลังหมดอายุ)
    moves = rally([UP] * FTD_WINDOW_END_DAY + [UP, -0.5, UP, -0.3])
    m = compute_ftd_dd(series(moves))
    assert [i for i, x in enumerate(m) if x == "DD"] == [len(moves) - 2, len(moves)]


if __name__ == "__main__":
    fns = [v for k, v in dict(globals()).items() if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"OK {len(fns)} tests")
