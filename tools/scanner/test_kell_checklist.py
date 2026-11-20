"""kell_checklist (Oliver Kell 7 ข้อของทุกหุ้น) — ราคาจำลองใน tmp dir · ไม่ต่อเครือข่าย · ไม่เขียนลง data/

    python -m pytest tools/scanner/test_kell_checklist.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))
import scan_oliver_kell  # noqa: E402

N = 300  # แท่ง (≥ 220 ที่ scan_oliver_kell ต้องการ)


def write_prices(path: Path, close):
    close = np.asarray(close, dtype=float)
    idx = pd.bdate_range("2025-06-02", periods=len(close))
    pd.DataFrame(
        {"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close, "Volume": 2_000_000.0},
        index=idx,
    ).to_csv(path)


SHAPES = {
    # ขาขึ้นเรียบ 50 → 100: ผ่านครบ 7 ข้อ (ห่าง EMA10 < 3.5%)
    "PASS": np.linspace(50, 100, N),
    # ยืนสูง 150 แล้วลงไป 60 แล้วเด้ง → SMA200 วันนี้ต่ำกว่า 20 วันก่อน (ตก K3)
    "FAIL3": np.concatenate([np.full(100, 150.0), np.linspace(150, 60, 150), np.linspace(60, 110, 50)]),
    # ขาขึ้นแล้วย่อ 10 วันสุดท้าย → EMA10 ลดลงจาก 5 วันก่อน (ตก K6)
    "FAIL6": np.concatenate([np.linspace(50, 100, N - 10), np.linspace(99, 94, 10)]),
    # ขาขึ้นแล้วกระโดด +12% วันสุดท้าย → ห่าง EMA10/EMA20 เกิน 3.5% (ตก K7)
    "FAIL7": np.concatenate([np.linspace(50, 100, N - 1), [112.0]]),
    # ประวัติสั้น < 220 → ไม่อยู่ในทั้งสองไฟล์
    "SHORT": np.linspace(50, 100, 200),
}


def _patch(monkeypatch, tmp_path, hist, checklist_path):
    monkeypatch.setattr(scan_oliver_kell, "HISTORY_DIR", hist)
    monkeypatch.setattr(scan_oliver_kell, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(scan_oliver_kell, "RS_FILE", tmp_path / "no_rs.csv")
    monkeypatch.setattr(scan_oliver_kell, "KELL_CHECKLIST_FILE", checklist_path)
    monkeypatch.setattr(scan_oliver_kell, "fetch_fundamental_data", lambda: {})
    monkeypatch.setattr(scan_oliver_kell, "load_fund_tickers", lambda: set())
    monkeypatch.setattr(scan_oliver_kell, "iter_stock_files", lambda d: sorted(Path(d).glob("*.csv")))


@pytest.fixture
def run_scan(tmp_path, monkeypatch):
    hist = tmp_path / "history"
    hist.mkdir()
    for t, close in SHAPES.items():
        write_prices(hist / f"{t}.csv", close)
    ck_path = tmp_path / "kell_checklist.csv"
    _patch(monkeypatch, tmp_path, hist, ck_path)
    scan_oliver_kell.scan_oliver_kell_strict()
    kell = pd.read_csv(tmp_path / "oliver_kell_signals.csv", encoding="utf-8-sig")
    ck = pd.read_csv(ck_path, encoding="utf-8-sig").set_index("Ticker")
    return kell, ck


def ks(ck, t):
    return [bool(ck.loc[t, f"K{i}"]) for i in range(1, 8)]


def test_full_pass(run_scan):
    kell, ck = run_scan
    assert ks(ck, "PASS") == [True] * 7
    assert ck.loc["PASS", "Pass_Count"] == 7 and bool(ck.loc["PASS", "All_Pass"])
    row = kell.set_index("Ticker").loc["PASS"]
    assert ck.loc["PASS", "Price"] == row["Price"] and ck.loc["PASS", "EMA_10"] == row["EMA10"]
    assert min(ck.loc["PASS", "Dist_EMA10_Pct"], ck.loc["PASS", "Dist_EMA20_Pct"]) == row["Dist_EMA10_%"]


def test_fail_k3_sma200_not_rising(run_scan):
    _, ck = run_scan
    assert ck.loc["FAIL3", "SMA_200"] < ck.loc["FAIL3", "SMA_200_20D_Ago"]
    assert ks(ck, "FAIL3")[2] is False
    assert not bool(ck.loc["FAIL3", "All_Pass"])


def test_fail_k6_ema_not_rising(run_scan):
    _, ck = run_scan
    k = ks(ck, "FAIL6")
    assert k[5] is False
    assert ck.loc["FAIL6", "EMA_10"] < ck.loc["FAIL6", "EMA_10_5D_Ago"]
    assert not bool(ck.loc["FAIL6", "All_Pass"])


def test_fail_k7_extended(run_scan):
    _, ck = run_scan
    k = ks(ck, "FAIL7")
    assert k == [True] * 6 + [False]
    assert ck.loc["FAIL7", "Dist_EMA10_Pct"] > 3.5 and ck.loc["FAIL7", "Dist_EMA20_Pct"] > 3.5
    assert ck.loc["FAIL7", "Pass_Count"] == 6 and not bool(ck.loc["FAIL7", "All_Pass"])


def test_all_pass_set_equals_oliver_kell(run_scan):
    kell, ck = run_scan
    assert set(ck.index[ck["All_Pass"].astype(bool)]) == set(kell["Ticker"]) == {"PASS"}
    assert "SHORT" not in ck.index and "SHORT" not in set(kell["Ticker"])
    assert set(ck.index) == {"PASS", "FAIL3", "FAIL6", "FAIL7"}
    expected = ["Ticker", "Price", "SMA_50", "SMA_200", "SMA_200_20D_Ago", "EMA_10", "EMA_20", "EMA_10_5D_Ago",
                "EMA_20_5D_Ago", "Dist_EMA10_Pct", "Dist_EMA20_Pct"] + [f"K{i}" for i in range(1, 8)] + [
                "Pass_Count", "All_Pass"]
    assert list(ck.reset_index().columns) == expected
    # ผ่านครบ 7 ข้อ ⇔ อยู่ใน oliver_kell (ข้อมูลจำลองไม่มีแถว error)
    assert set(ck.index[ck["Pass_Count"] == 7]) == set(kell["Ticker"])


def test_checklist_write_error_does_not_break_scan(tmp_path, monkeypatch, capsys):
    hist = tmp_path / "history"
    hist.mkdir()
    write_prices(hist / "PASS.csv", SHAPES["PASS"])
    _patch(monkeypatch, tmp_path, hist, tmp_path / "no_such_dir" / "kell_checklist.csv")
    scan_oliver_kell.scan_oliver_kell_strict()
    assert "kell_checklist เขียนไม่สำเร็จ" in capsys.readouterr().out
    assert list(pd.read_csv(tmp_path / "oliver_kell_signals.csv", encoding="utf-8-sig")["Ticker"]) == ["PASS"]


def test_row_error_does_not_break_scan(tmp_path, monkeypatch, capsys):
    hist = tmp_path / "history"
    hist.mkdir()
    write_prices(hist / "PASS.csv", SHAPES["PASS"])
    _patch(monkeypatch, tmp_path, hist, tmp_path / "kell_checklist.csv")

    def boom(*a, **k):
        raise ValueError("boom")

    monkeypatch.setattr(scan_oliver_kell, "kell_checklist_row", boom)
    scan_oliver_kell.scan_oliver_kell_strict()
    assert "kell_checklist row error PASS" in capsys.readouterr().out
    assert list(pd.read_csv(tmp_path / "oliver_kell_signals.csv", encoding="utf-8-sig")["Ticker"]) == ["PASS"]
    assert not (tmp_path / "kell_checklist.csv").exists()


def test_convert_to_json_maps_checklist(tmp_path, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("convert_to_json", HERE.parents[1] / "data/results/convert_to_json.py")
    conv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(conv)
    assert conv.FILES["kell_checklist"] == "kell_checklist.csv"
    assert "kell_checklist" in conv.COMPACT_JSON_SCANS

    monkeypatch.setattr(conv, "INPUT_DIR", tmp_path)
    assert "kell_checklist" not in conv.build_individual_jsons()

    row = scan_oliver_kell.kell_checklist_row("AAA", 10.123, 9, 8, 7.5, 10, 9.8, 9.9, 9.7)
    row["All_Pass"] = True
    row2 = scan_oliver_kell.kell_checklist_row("BBB", 12, 9, 8, 7.5, 10, 9.8, 9.9, 9.7)
    row2["All_Pass"] = False
    assert scan_oliver_kell.kell_checklist_row("NAN", float("nan"), 9, 8, 7.5, 10, 9.8, 9.9, 9.7) is None
    scan_oliver_kell.write_kell_checklist([row, row2], tmp_path / "kell_checklist.csv")
    recs = {r["Ticker"]: r for r in conv.build_individual_jsons()["kell_checklist"]}
    assert recs["AAA"]["Price"] == 10.12 and recs["AAA"]["K7"] is True and recs["AAA"]["Pass_Count"] == 7
    assert recs["AAA"]["All_Pass"] is True
    assert recs["BBB"]["K7"] is False and recs["BBB"]["Pass_Count"] == 6 and recs["BBB"]["Dist_EMA10_Pct"] == 20.0
