"""NVDR: สร้างประวัติ 5 วันจาก archive stockdesk (data/history/<วัน>/nvdr.json) — ไฟล์ปลอมทั้งหมด
(ไม่ยิง SET · ไม่รัน pipeline จริง)

    python -m pytest tools/market/test_download_nvdr.py
"""
import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


dn = _load("download_nvdr_t", "tools/market/download_nvdr.py")
c2j = _load("convert_to_json_t", "data/results/convert_to_json.py")

SYMS = ["AAA", "BBB", "CCC"]


def _rows(date, net_base):
    return [{"Date": date, "Symbol": s, "Buy": float(net_base + i + 100), "Sell": 100.0,
             "Total": float(net_base + i + 200), "Net": float(net_base + i)} for i, s in enumerate(SYMS)]


def _write_day(archive, folder, latest, net_base, extra=None):
    d = archive / folder
    d.mkdir(parents=True, exist_ok=True)
    payload = {"latest_date": latest, "today": _rows(latest, net_base), "5d": []}
    (d / "nvdr.json").write_text(json.dumps(payload) if extra is None else extra, encoding="utf-8")


def _today(date="2026-11-20", net_base=1000):
    return pd.DataFrame(_rows(date, net_base))


@pytest.fixture
def env(tmp_path, monkeypatch):
    csv = tmp_path / "data" / "nvdr_history.csv"
    csv.parent.mkdir(parents=True)
    # csv เดิมใน repo = ข้อมูล ก.ค. ค้าง → ห้ามโผล่ในยอดเมื่อมี archive
    pd.DataFrame(_rows("2026-07-21", 9_000_000) + _rows("2026-07-20", 9_000_000)).to_csv(csv, index=False)
    monkeypatch.setattr(dn, "NVDR_CSV", csv)
    archive = tmp_path / "stockdesk" / "data" / "history"
    archive.mkdir(parents=True)
    return tmp_path, csv, archive


def _build(tmp_path, csv):
    """รัน convert_to_json.build_nvdr กับ csv ใน tmp (build_nvdr หา csv จาก __file__)"""
    c2j.__file__ = str(csv.parent / "results" / "convert_to_json.py")
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    c2j.build_nvdr(out)
    return json.loads((out / "nvdr.json").read_text(encoding="utf-8"))


# วันสแกน → latest_date (ช้า 1 วันทำการ)
DAYS = [("2026-11-13", "2026-11-12"), ("2026-11-16", "2026-11-13"), ("2026-11-17", "2026-11-16"),
        ("2026-11-18", "2026-11-17"), ("2026-11-19", "2026-11-18"), ("2026-11-20", "2026-11-19")]


def test_rebuild_from_archive_5d_uses_last_5_real_dates(env):
    tmp_path, csv, archive = env
    for i, (folder, latest) in enumerate(DAYS):
        _write_day(archive, folder, latest, net_base=i * 10)
    assert dn.update_nvdr_history(_today(), [archive], scrape=False) == 0
    out = _build(tmp_path, csv)
    assert out["latest_date"] == "2026-11-20"
    assert out["today"] == _rows("2026-11-20", 1000)
    # 5 วัน = 20, 19, 18, 17, 16 พ.ย. (net_base 1000, 50, 40, 30, 20) · ไม่มี ก.ค. ไม่มี 13 / 12 พ.ย.
    want = {s: 1000 + 50 + 40 + 30 + 20 + 5 * i for i, s in enumerate(SYMS)}
    assert {r["Symbol"]: r["Net"] for r in out["5d"]} == want
    assert "2026-07-21" not in set(pd.read_csv(csv)["Date"])


def test_scraped_day_wins_over_archive_same_date(env):
    tmp_path, csv, archive = env
    for i, (folder, latest) in enumerate(DAYS):
        _write_day(archive, folder, latest, net_base=i * 10)
    # รันซ้ำวันเดียวกัน: archive มี 19 พ.ย. แล้ว · SET ยังให้ 19 พ.ย. ค่าใหม่
    assert dn.update_nvdr_history(_today("2026-11-19", 7000), [archive], scrape=False) == 0
    out = _build(tmp_path, csv)
    assert out["latest_date"] == "2026-11-19"
    assert out["today"] == _rows("2026-11-19", 7000)
    assert len(pd.read_csv(csv).query("Date == '2026-11-19'")) == len(SYMS)


def test_no_archive_falls_back_to_legacy_csv_and_fails_step(env):
    tmp_path, csv, archive = env
    assert dn.update_nvdr_history(_today(), [tmp_path / "missing"], scrape=False) == 1
    dates = set(pd.read_csv(csv)["Date"])
    assert dates == {"2026-11-20", "2026-07-21", "2026-07-20"}  # พฤติกรรมเดิม


def test_empty_archive_dir_counts_as_missing(env):
    tmp_path, csv, archive = env
    (archive / "2026-11-20").mkdir()  # โฟลเดอร์มี แต่ไม่มี nvdr.json (checkout ล้มกลางทาง)
    assert dn.update_nvdr_history(_today(), [archive], scrape=False) == 1
    assert "2026-07-21" in set(pd.read_csv(csv)["Date"])


def test_corrupt_day_is_skipped_and_reported(env, capsys):
    tmp_path, csv, archive = env
    for i, (folder, latest) in enumerate(DAYS):
        _write_day(archive, folder, latest, net_base=i * 10, extra="{broken" if folder == "2026-11-18" else None)
    assert dn.update_nvdr_history(_today(), [archive], scrape=False) == 1
    assert "2026-11-18/nvdr.json" in capsys.readouterr().out
    dates = sorted(set(pd.read_csv(csv)["Date"]), reverse=True)
    # โฟลเดอร์ 2026-11-18 (latest 17 พ.ย.) เสีย → 17 พ.ย. หาย
    assert "2026-11-17" not in dates and dates[:5] == ["2026-11-20", "2026-11-19", "2026-11-18", "2026-11-16", "2026-11-13"]


def test_wrong_shape_day_is_skipped(env):
    tmp_path, csv, archive = env
    for i, (folder, latest) in enumerate(DAYS):
        _write_day(archive, folder, latest, net_base=i * 10)
    bad = {"latest_date": "2026-11-18", "today": [{"Symbol": "AAA", "Net": 1.0}], "5d": []}
    (archive / "2026-11-19" / "nvdr.json").write_text(json.dumps(bad), encoding="utf-8")
    assert dn.update_nvdr_history(_today(), [archive], scrape=False) == 1
    assert "2026-11-18" not in set(pd.read_csv(csv)["Date"])


def test_only_two_days_uses_what_exists(env, capsys):
    tmp_path, csv, archive = env
    for i, (folder, latest) in enumerate(DAYS[-2:]):
        _write_day(archive, folder, latest, net_base=i * 10)
    assert dn.update_nvdr_history(_today(), [archive], scrape=False) == 0
    assert "แค่ 3 วัน" in capsys.readouterr().out
    out = _build(tmp_path, csv)
    assert {r["Symbol"]: r["Net"] for r in out["5d"]}["AAA"] == 1000 + 0 + 10


def test_duplicate_latest_date_newest_folder_wins(env):
    tmp_path, csv, archive = env
    for i, (folder, latest) in enumerate(DAYS):
        _write_day(archive, folder, latest, net_base=i * 10)
    # snapshot วันหยุด / สแกนซ้ำ: โฟลเดอร์ 21 พ.ย. latest_date 19 พ.ย. ซ้ำ ค่าใหม่กว่า
    _write_day(archive, "2026-11-21", "2026-11-19", net_base=500)
    assert dn.update_nvdr_history(_today(), [archive], scrape=False) == 0
    hist = pd.read_csv(csv)
    assert hist.query("Date == '2026-11-19' and Symbol == 'AAA'")["Net"].tolist() == [500.0]
    assert hist["Date"].nunique() == 7  # 20 + 19,18,17,16,13,12 (ซ้ำนับครั้งเดียว)


def test_archive_reads_at_most_max_dates(env):
    tmp_path, csv, archive = env
    for k in range(15):
        _write_day(archive, f"2026-10-{k + 1:02d}", f"2026-09-{k + 1:02d}", net_base=k)
    hist, problems, _ = dn.load_archive_history(archive, max_dates=10)
    assert problems == [] and hist["Date"].nunique() == 10 and hist["Date"].min() == "2026-09-06"


def test_scrape_failed_archive_ok_writes_archive_and_fails_step(env):
    tmp_path, csv, archive = env
    for i, (folder, latest) in enumerate(DAYS):
        _write_day(archive, folder, latest, net_base=i * 10)
    assert dn.update_nvdr_history(None, [archive], scrape=False) == 1
    out = _build(tmp_path, csv)
    assert out["latest_date"] == "2026-11-19"  # ของรอบก่อน ไม่ใช่ 21 ก.ค.


def test_scrape_failed_no_archive_keeps_csv(env):
    tmp_path, csv, archive = env
    before = csv.read_bytes()
    assert dn.update_nvdr_history(None, [tmp_path / "missing"], scrape=False) == 1
    assert csv.read_bytes() == before


def test_failed_exit_code_lands_in_failed_steps(tmp_path):
    """run_all.run_script: exit != 0 → FAILED_STEPS (pipeline รันต่อ)"""
    ra = _load("run_all_t", "run_all.py")
    script = tmp_path / "download_nvdr.py"
    script.write_text("import sys; sys.exit(1)\n", encoding="utf-8")
    ra.FAILED_STEPS.clear()
    ra.run_script(script)
    assert ra.FAILED_STEPS == ["download_nvdr.py"]
