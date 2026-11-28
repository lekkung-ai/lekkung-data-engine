"""Unit tests สำหรับ tools/core/indicators.py — ค่าคำนวณมือ"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.core.indicators import adr_pct, avwap, ema, gap_pct, perf_pct, value_avg, volume_avg_prior  # noqa: E402


def test_ema_recursive_by_hand():
    c = pd.Series([10.0, 11.0, 12.0, 13.0])
    k = 2 / (3 + 1)
    e = [10.0]
    for x in c.iloc[1:]:
        e.append(x * k + e[-1] * (1 - k))
    assert np.allclose(ema(c, 3).to_numpy(), e)


def test_ema_matches_luk_prototype_formula():
    # scan_martin_luk.ema(): out[0] = x0, out[i] = x*k + out[i-1]*(1-k)
    c = pd.Series(np.linspace(5, 9, 40) + np.sin(np.arange(40)))
    k, out = 2 / 22, []
    for i, x in enumerate(c):
        out.append(x if i == 0 else x * k + out[-1] * (1 - k))
    assert np.allclose(ema(c, 21).to_numpy(), out)


def test_adr_pct_by_hand():
    h = pd.Series([11.0, 10.5, 12.0])
    l = pd.Series([10.0, 10.0, 0.0])          # low 0 ไม่นับ
    r = adr_pct(h, l, n=2)
    assert abs(r.iloc[1] - ((0.1 + 0.05) / 2 * 100)) < 1e-9
    assert abs(r.iloc[2] - 5.0) < 1e-9        # แท่ง 3 ถูกตัด เหลือแท่ง 2


def test_value_and_prior_volume_and_gap_perf():
    c = pd.Series([10.0, 20.0, 30.0])
    v = pd.Series([1.0, 2.0, 3.0])
    assert value_avg(c, v, 2).tolist() == [10.0, 25.0, 65.0]
    assert volume_avg_prior(v, 2).iloc[2] == 1.5 and np.isnan(volume_avg_prior(v, 2).iloc[0])
    o = pd.Series([10.0, 11.0, 27.0])
    assert np.allclose(gap_pct(o, c).iloc[1:].to_numpy(), [10.0, 35.0])
    assert abs(perf_pct(c, 2).iloc[2] - 200.0) < 1e-9


def test_avwap_by_hand():
    h, l, c = [10.0, 12.0, 14.0], [8.0, 10.0, 12.0], [9.0, 11.0, 13.0]
    v = [100.0, 300.0, 0.0]
    # typical: 9, 11, 13 → (9*100 + 11*300) / 400 = 10.5 · แท่ง vol 0 ไม่มีผล
    assert abs(avwap(h, l, c, v, 0, 2) - 10.5) < 1e-12
    assert avwap(h, l, c, v, 2, 2) is None


if __name__ == "__main__":
    fns = [v for k, v in dict(globals()).items() if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"OK {len(fns)} tests")
