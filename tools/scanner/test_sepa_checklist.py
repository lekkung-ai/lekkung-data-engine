"""sepa_checklist (SEPA 8 ข้อของทุกหุ้น) — ราคาจำลองใน tmp dir · ไม่ต่อเครือข่าย · ไม่เขียนลง data/

    python -m pytest tools/scanner/test_sepa_checklist.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))
import scan_sepa  # noqa: E402

N = 300  # แท่ง (≥ 250 ที่ scan_sepa ต้องการ)


def write_prices(path: Path, close):
    close = np.asarray(close, dtype=float)
    idx = pd.bdate_range("2025-06-02", periods=len(close))
    pd.DataFrame(
        {"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close, "Volume": 2_000_000.0},
        index=idx,
    ).to_csv(path)


SHAPES = {
    # ขาขึ้นเรียบ 50 → 100: ผ่าน 7 ข้อแรก
    "PASS": np.linspace(50, 100, N),
    "LOWRS": np.linspace(50, 100, N),
    "NORS": np.linspace(50, 100, N),
    "NANRS": np.linspace(50, 100, N),
    # ยืนสูง 150 แล้วลงไป 60 แล้วเด้ง → SMA200 วันนี้ต่ำกว่า 1 เดือนก่อน (ตกข้อ 3)
    "FAIL3": np.concatenate([np.full(100, 150.0), np.linspace(150, 60, 150), np.linspace(60, 110, 50)]),
    # ขึ้นช้า 100 → 120: เหนือจุดต่ำ 52 สัปดาห์ไม่ถึง 30% (ตกข้อ 6)
    "FAIL6": np.linspace(100, 120, N),
    # ประวัติสั้น → ไม่อยู่ในทั้งสองไฟล์
    "SHORT": np.linspace(50, 100, 120),
}
RS = {"PASS": 92, "LOWRS": 55, "NANRS": float("nan"), "FAIL3": 90, "FAIL6": 88, "SHORT": 99}


@pytest.fixture
def run_scan(tmp_path, monkeypatch):
    hist = tmp_path / "history"
    hist.mkdir()
    for t, close in SHAPES.items():
        write_prices(hist / f"{t}.csv", close)
    rs_file = tmp_path / "rs_ranking.csv"
    pd.DataFrame({"Ticker": list(RS), "RS_Rating": list(RS.values())}).to_csv(rs_file, index=False)

    out = {"sepa": tmp_path / "sepa_result.csv", "checklist": tmp_path / "sepa_checklist.csv"}
    monkeypatch.setattr(scan_sepa, "HISTORY_DIR", hist)
    monkeypatch.setattr(scan_sepa, "RS_FILE", rs_file)
    monkeypatch.setattr(scan_sepa, "SEPA_FILE", out["sepa"])
    monkeypatch.setattr(scan_sepa, "SEPA_CHECKLIST_FILE", out["checklist"])
    monkeypatch.setattr(scan_sepa, "fetch_fundamental_data", lambda: {})
    monkeypatch.setattr(scan_sepa, "load_fund_tickers", lambda: set())
    monkeypatch.setattr(scan_sepa, "iter_stock_files", lambda d: sorted(Path(d).glob("*.csv")))
    scan_sepa.scan_sepa()
    sepa = pd.read_csv(out["sepa"], encoding="utf-8-sig")
    ck = pd.read_csv(out["checklist"], encoding="utf-8-sig").set_index("Ticker")
    return sepa, ck


def conds_of(ck, t):
    return [bool(ck.loc[t, f"T{i}"]) for i in range(1, 9)]


def test_full_pass(run_scan):
    sepa, ck = run_scan
    assert conds_of(ck, "PASS") == [True] * 8
    assert ck.loc["PASS", "Pass_Count"] == 8 and bool(ck.loc["PASS", "All_Pass"])
    assert ck.loc["PASS", "RS_Rating"] == 92
    # ตัวเลขชุดเดียวกับ sepa_result
    row = sepa.set_index("Ticker").loc["PASS"]
    for col in ("Price", "SMA_50", "SMA_200", "52W_High"):
        assert ck.loc["PASS", col] == row[col]


def test_fail_sma200_not_rising(run_scan):
    _, ck = run_scan
    assert ck.loc["FAIL3", "SMA_200"] < ck.loc["FAIL3", "SMA_200_1M_Ago"]
    assert conds_of(ck, "FAIL3")[2] is False
    assert not bool(ck.loc["FAIL3", "All_Pass"])


def test_fail_30pct_above_low(run_scan):
    _, ck = run_scan
    t = conds_of(ck, "FAIL6")
    assert t[5] is False
    assert ck.loc["FAIL6", "Price"] < 1.30 * ck.loc["FAIL6", "52W_Low"]
    assert t[:5] == [True] * 5 and t[6] is True and t[7] is True
    assert ck.loc["FAIL6", "Pass_Count"] == 7 and not bool(ck.loc["FAIL6", "All_Pass"])


@pytest.mark.parametrize("ticker, rs", [("LOWRS", 55), ("NORS", None), ("NANRS", None)])
def test_rs_rule(run_scan, ticker, rs):
    _, ck = run_scan
    t = conds_of(ck, ticker)
    assert t[:7] == [True] * 7 and t[7] is False
    assert ck.loc[ticker, "Pass_Count"] == 7 and not bool(ck.loc[ticker, "All_Pass"])
    if rs is None:
        assert pd.isna(ck.loc[ticker, "RS_Rating"])
    else:
        assert ck.loc[ticker, "RS_Rating"] == rs


def test_all_pass_set_equals_sepa_result(run_scan):
    sepa, ck = run_scan
    assert set(ck.index[ck["All_Pass"].astype(bool)]) == set(sepa["Ticker"]) == {"PASS"}
    assert "SHORT" not in ck.index and "SHORT" not in set(sepa["Ticker"])
    assert set(ck.index) == {"PASS", "LOWRS", "NORS", "NANRS", "FAIL3", "FAIL6"}
    expected_cols = ["Ticker", "Price", "SMA_50", "SMA_150", "SMA_200", "SMA_200_1M_Ago", "52W_Low", "52W_High",
                     "RS_Rating"] + [f"T{i}" for i in range(1, 9)] + ["Pass_Count", "All_Pass"]
    assert list(ck.reset_index().columns) == expected_cols


def test_checklist_write_error_does_not_break_scan(tmp_path, monkeypatch, capsys):
    """เขียน sepa_checklist ไม่ได้ → sepa_result ยังเขียนได้ตามปกติ"""
    hist = tmp_path / "history"
    hist.mkdir()
    write_prices(hist / "PASS.csv", SHAPES["PASS"])
    rs_file = tmp_path / "rs.csv"
    pd.DataFrame({"Ticker": ["PASS"], "RS_Rating": [92]}).to_csv(rs_file, index=False)
    monkeypatch.setattr(scan_sepa, "HISTORY_DIR", hist)
    monkeypatch.setattr(scan_sepa, "RS_FILE", rs_file)
    monkeypatch.setattr(scan_sepa, "SEPA_FILE", tmp_path / "sepa_result.csv")
    monkeypatch.setattr(scan_sepa, "SEPA_CHECKLIST_FILE", tmp_path / "no_such_dir" / "sepa_checklist.csv")
    monkeypatch.setattr(scan_sepa, "fetch_fundamental_data", lambda: {})
    monkeypatch.setattr(scan_sepa, "load_fund_tickers", lambda: set())
    monkeypatch.setattr(scan_sepa, "iter_stock_files", lambda d: sorted(Path(d).glob("*.csv")))
    scan_sepa.scan_sepa()
    assert "sepa_checklist เขียนไม่สำเร็จ" in capsys.readouterr().out
    assert list(pd.read_csv(tmp_path / "sepa_result.csv", encoding="utf-8-sig")["Ticker"]) == ["PASS"]


def test_convert_to_json_maps_checklist(tmp_path, monkeypatch):
    """convert_to_json: sepa_checklist.csv → records (RS int/null, T เป็น bool) · ไม่มีไฟล์ = ไม่เขียน (MISSING GUARD)"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("convert_to_json", HERE.parents[1] / "data/results/convert_to_json.py")
    conv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(conv)
    assert conv.FILES["sepa_checklist"] == "sepa_checklist.csv"

    monkeypatch.setattr(conv, "INPUT_DIR", tmp_path)
    assert "sepa_checklist" not in conv.build_individual_jsons()

    row = scan_sepa.checklist_row("AAA", 10.123, 9, 8, 7, 6.5, 5, 11, 91.0, [True] * 7, True)
    row["All_Pass"] = True
    row2 = scan_sepa.checklist_row("BBB", 10, 9, 8, 7, 6.5, 5, 11, None, [True] * 6 + [False], False)
    row2["All_Pass"] = False
    scan_sepa.write_checklist([row, row2], tmp_path / "sepa_checklist.csv")
    recs = {r["Ticker"]: r for r in conv.build_individual_jsons()["sepa_checklist"]}
    assert recs["AAA"]["RS_Rating"] == 91 and recs["AAA"]["Price"] == 10.12 and recs["AAA"]["T8"] is True
    assert recs["BBB"]["RS_Rating"] is None and recs["BBB"]["T7"] is False and recs["BBB"]["Pass_Count"] == 6
