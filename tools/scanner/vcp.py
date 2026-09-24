"""VCP footprint — pure functions (ไม่มี I/O). ใช้จาก scan_sepa.py เป็นคอลัมน์เพิ่มเท่านั้น."""

import numpy as np
import pandas as pd

LOOKBACK = 325
MIN_BASE_BARS = 15
MIN_SWING_PCT = 3.0
MIN_SWING_TICKS = 3
MIN_LEG_BARS = 5
ADAPT_RATIO = 0.30
CONTRACT_RATIO = 0.75

VCP_KEYS = (
    "VCP_Weeks",
    "VCP_T",
    "VCP_MaxDepth",
    "VCP_FinalDepth",
    "VCP_Contracting",
    "VCP_VolRatio",
    "VCP_Pivot",
    "VCP_ToPivot",
    "VCP_Footprint",
)


def _empty(**overrides) -> dict:
    out = {k: None for k in VCP_KEYS}
    out.update(overrides)
    return out


def thai_tick(price: float) -> float:
    """ตาราง tick size ของ SET."""
    if price < 2:
        return 0.01
    if price < 5:
        return 0.02
    if price < 10:
        return 0.05
    if price < 25:
        return 0.10
    if price < 100:
        return 0.25
    if price < 200:
        return 0.50
    if price < 400:
        return 1.0
    return 2.0


def swing_threshold_pct(close: float) -> float:
    """เกณฑ์ swing ขั้นต่ำ (%) = max(3%, 3 ticks ของราคาปิด) — กันหุ้นราคาต่ำที่ 1 tick = หลาย %."""
    return max(MIN_SWING_PCT, MIN_SWING_TICKS * thai_tick(close) / close * 100.0)


def adaptive_swings(highs: np.ndarray, lows: np.ndarray, base_thr: float, min_leg_bars: int = MIN_LEG_BARS):
    """Leg กลางฐาน: Leg 1 = H0 (index 0) → ต่ำสุดหลัง H0; หลังจากนั้น zigzag แบบ reversal-confirmed.

    θ_k = max(base_thr, ADAPT_RATIO × depth ของ leg ก่อนหน้า) — swing high ยืนยันเมื่อราคาลงจากมัน ≥ θ,
    swing low ยืนยันเมื่อราคาขึ้นจากมัน ≥ θ · high ถัดไปสูงกว่า high ก่อนหน้าได้
    leg ที่ H→L สั้นกว่า min_leg_bars ไม่นับ · leg สุดท้ายที่ยังไม่ยืนยัน "ไม่ถูกรวม" (วัดแยกด้วย final_leg)

    คืน (legs, anchor) — anchor = index ของ swing low ที่ยืนยันล่าสุด (None = leg 1 ไม่ผ่านเกณฑ์)
    """
    n = len(highs)
    if n < 2:
        return [], None
    l1 = 1 + int(np.argmin(lows[1:]))
    d1 = (highs[0] - lows[l1]) / highs[0] * 100.0
    if d1 < base_thr:
        return [], None

    legs = []
    if l1 >= min_leg_bars or l1 == n - 1:
        legs.append({"hi": 0, "lo": l1, "depth": d1, "bars": l1, "final": False})
    anchor = l1
    if l1 == n - 1:
        return legs, anchor

    theta = max(base_thr, ADAPT_RATIO * d1)
    state = "up"
    hi_idx, hi_val = -1, -np.inf
    lo_idx, lo_val = -1, np.inf
    for i in range(l1 + 1, n):
        if state == "up":
            if highs[i] > hi_val:
                hi_idx, hi_val = i, highs[i]
            elif (hi_val - lows[i]) / hi_val * 100.0 >= theta:
                state = "down"
                lo_idx, lo_val = i, lows[i]
        else:
            if lows[i] < lo_val:
                lo_idx, lo_val = i, lows[i]
            elif (highs[i] - lo_val) / lo_val * 100.0 >= theta:
                depth = (hi_val - lo_val) / hi_val * 100.0
                if lo_idx - hi_idx >= min_leg_bars:
                    legs.append({"hi": hi_idx, "lo": lo_idx, "depth": depth, "bars": lo_idx - hi_idx, "final": False})
                    theta = max(base_thr, ADAPT_RATIO * depth)
                anchor = lo_idx
                state = "up"
                hi_idx, hi_val = i, highs[i]
    return legs, anchor


def final_leg(highs: np.ndarray, lows: np.ndarray, anchor: int):
    """Leg ฝั่งขวา: max(High) หลัง swing low ที่ยืนยันล่าสุด → min(Low) หลัง high นั้นถึงวันนี้.

    คืน (hi_idx, depth%) — pivot = highs[hi_idx] ≥ close เสมอ เพราะแท่งล่าสุดอยู่ในช่วงที่หา max
    """
    n = len(highs)
    if anchor + 1 >= n:
        return anchor, 0.0
    hi = anchor + 1 + int(np.argmax(highs[anchor + 1 :]))
    if hi + 1 >= n:
        return hi, 0.0
    return hi, (highs[hi] - lows[hi + 1 :].min()) / highs[hi] * 100.0


def _r1(x):
    return None if x is None or not np.isfinite(x) else round(float(x), 1)


def vcp_metrics(df: pd.DataFrame) -> dict:
    """คำนวณ VCP footprint จาก DataFrame ที่มีคอลัมน์ High/Low/Close/Volume (เรียงเก่า→ใหม่)."""
    try:
        if df is None or len(df) < MIN_BASE_BARS + 2:
            return _empty()
        w = df.iloc[-LOOKBACK:]
        H = w["High"].to_numpy(dtype=float)
        L = w["Low"].to_numpy(dtype=float)
        C = w["Close"].to_numpy(dtype=float)
        V = w["Volume"].to_numpy(dtype=float)
        if not (np.isfinite(H).all() and np.isfinite(L).all() and np.isfinite(C).all()):
            return _empty()

        n = len(H)
        start = int(np.argmax(H))
        bars = n - 1 - start
        if bars < MIN_BASE_BARS:
            return _empty(VCP_T=0, VCP_Footprint="no base")

        close = C[-1]
        if close <= 0 or H[start] <= 0:
            return _empty()

        h, l, v = H[start:], L[start:], V[start:]
        thr = swing_threshold_pct(close)
        mid_legs, anchor = adaptive_swings(h, l, thr)

        if anchor is None:
            # ไม่มี leg ≥ thr เลย → ฐานแคบ: leg เดียวที่ต่ำกว่าเกณฑ์ (T=0), pivot = ยอดฐาน
            dd = (h[0] - l[1:].min()) / h[0] * 100.0
            counted, final_depth, last_hi = [], dd, 0
            max_depth = dd
        else:
            f_hi, f_depth = final_leg(h, l, anchor)
            counted = [x["depth"] for x in mid_legs]
            counted_hi = [x["hi"] for x in mid_legs]
            if f_depth >= thr:  # final leg ใช้เกณฑ์คงที่ ไม่ใช้ adaptive θ
                counted.append(f_depth)
                counted_hi.append(f_hi)
            if counted:
                # Pivot / FinalDepth / VolRatio อิง T สุดท้ายที่นับได้ (ตามหนังสือ)
                final_depth, last_hi = counted[-1], counted_hi[-1]
                max_depth = max(counted)
            else:
                final_depth, last_hi = f_depth, f_hi
                max_depth = f_depth

        t_count = len(counted)

        contracting = False
        if t_count >= 2:
            contracting = all(counted[k] / counted[k - 1] <= CONTRACT_RATIO for k in range(1, len(counted)))

        vol_ratio = None
        base_vol = V[-51:-1]
        leg_vol = v[last_hi:]
        if len(base_vol) > 0 and len(leg_vol) > 0:
            avg = base_vol.mean()
            if avg > 0:
                vol_ratio = leg_vol.mean() / avg

        pivot = h[last_hi]
        to_pivot = (pivot - close) / pivot * 100.0 if pivot > 0 else None
        weeks = bars // 5

        return {
            "VCP_Weeks": weeks,
            "VCP_T": t_count,
            "VCP_MaxDepth": _r1(max_depth),
            "VCP_FinalDepth": _r1(final_depth),
            "VCP_Contracting": bool(contracting),
            "VCP_VolRatio": None if vol_ratio is None else round(float(vol_ratio), 2),
            "VCP_Pivot": None if pivot is None else round(float(pivot), 2),
            "VCP_ToPivot": _r1(to_pivot),
            "VCP_Footprint": f"{weeks}W {round(max_depth)}/{round(final_depth)} {t_count}T",
        }
    except Exception:
        return _empty()
