"""ด่านราคาค้างก่อน push + health_check H14 + การเตือน NON_TRADING_DAY วันธรรมดา — ใช้ไฟล์ปลอมทั้งหมด
(ไม่รัน pipeline จริง · ไม่ต่อเครือข่าย)

    python -m pytest tools/core/test_price_freshness.py
"""
import importlib
import importlib.util
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.core import price_freshness as pf  # noqa: E402
from tools.core.set_holidays import is_set_holiday, non_trading_warning  # noqa: E402


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


gate = _load("price_freshness_gate", "scripts/price_freshness_gate.py")
health = _load("health_check", "scripts/health_check.py")

UNIVERSE = 873


def payload(fallback: int = 0, outage: bool = False, bulk_failed: int = 7, universe: int = UNIVERSE) -> dict:
    names = [f"STK{i:03d}" for i in range(fallback)]
    return pf.build_payload(
        reference_date="2026-10-22", stock_data_date="2026-10-22", universe_size=universe,
        fresh_count=max(0, universe - fallback - 7), fallback_tickers=names,
        dropped_tickers=["CIMBT", "GLAND", "LRH", "TIPAK", "VAYU1", "HAPPY26", "QUICK"],
        bulk_failed_count=bulk_failed, outage_suspected=outage, retry_skipped=outage)


def write(tmp_path: Path, data) -> Path:
    path = tmp_path / "meta" / "price_freshness.json"
    if data is not None:
        pf.write_payload(path, data)
    return path


# ---- 4 กรณีหลัก: ปกติ / ค้าง 1.5% / ค้าง 5% / outage ---------------------------------------------------

CASES = {
    "normal": dict(fallback=0, expect_level=pf.OK, expect_exit=0, expect_h14=True),
    "stale_1_5pct": dict(fallback=13, expect_level=pf.WARN, expect_exit=0, expect_h14="WARN"),   # 13/873 = 1.49%
    "stale_5pct": dict(fallback=44, expect_level=pf.FAIL, expect_exit=1, expect_h14=False),      # 44/873 = 5.04%
    "outage": dict(fallback=873, outage=True, bulk_failed=873, expect_level=pf.FAIL, expect_exit=1, expect_h14=False),
}


@pytest.mark.parametrize("name", CASES)
def test_judge_gate_and_h14(name, tmp_path, capsys):
    c = dict(CASES[name])
    expect_level, expect_exit, expect_h14 = c.pop("expect_level"), c.pop("expect_exit"), c.pop("expect_h14")
    data = payload(**c)
    path = write(tmp_path, data)

    level, message = pf.judge(data)
    assert level == expect_level

    assert gate.run(path) == expect_exit
    out = capsys.readouterr().out
    assert (f"::error::" in out) == (expect_exit == 1)
    assert (f"::warning::" in out) == (expect_level == pf.WARN)

    (hid, _, result, value), = health.check_h14(path)
    assert hid == "H14" and result == expect_h14
    assert value == message


def test_error_message_has_count_and_ten_example_names(tmp_path, capsys):
    data = payload(fallback=44)
    assert gate.run(write(tmp_path, data)) == 1
    out = capsys.readouterr().out
    assert "44/873 ตัว (5.0%)" in out
    for i in range(10):
        assert f"STK{i:03d}" in out
    assert "STK010" not in out                      # ตัวอย่างไม่เกิน 10 ตัว
    assert "อีก 34 ตัว" in out
    assert "ไม่ push ขึ้นเว็บ" in out
    print(out)  # pytest -s: ดูข้อความจริงที่ผู้ใช้เห็น


def test_outage_message_says_yahoo(tmp_path, capsys):
    assert gate.run(write(tmp_path, payload(fallback=873, outage=True, bulk_failed=873))) == 1
    out = capsys.readouterr().out
    assert "Yahoo น่าจะล่ม" in out and "ข้ามการดึงซ้ำทีละตัว" in out and "873" in out
    print(out)


def test_thresholds_are_strictly_above_one_and_two_percent():
    # 873 ตัว: 8 = 0.92% ปกติ · 9 = 1.03% WARN · 17 = 1.95% WARN · 18 = 2.06% FAIL
    assert pf.judge(payload(fallback=8))[0] == pf.OK
    assert pf.judge(payload(fallback=9))[0] == pf.WARN
    assert pf.judge(payload(fallback=17))[0] == pf.WARN
    assert pf.judge(payload(fallback=18))[0] == pf.FAIL
    # 100 ตัว: 1% พอดี = ปกติ · 2% พอดี = WARN · 3 ตัว = FAIL
    assert pf.judge(payload(fallback=1, universe=100))[0] == pf.OK
    assert pf.judge(payload(fallback=2, universe=100))[0] == pf.WARN
    assert pf.judge(payload(fallback=3, universe=100))[0] == pf.FAIL


def test_dropped_tickers_do_not_count():
    # 7 ตัวที่ถูกตัดประจำ (0.8% ของ 873) ไม่ถูกนับเป็นราคาค้าง · ตัดมาก ๆ ก็ไม่ทำให้ด่านล้ม (ใช้ outage/H4 จับแทน)
    d = payload(fallback=0)
    d["dropped_tickers"] = [f"D{i}" for i in range(100)]
    d["dropped_count"] = 100
    assert pf.judge(d)[0] == pf.OK


# ---- ไม่มีไฟล์ -------------------------------------------------------------------------------------------

def test_missing_file_gate_blocks_but_h14_only_warns(tmp_path, capsys):
    path = write(tmp_path, None)
    assert gate.run(path) == 1                       # ขั้นราคาไม่จบ = ไม่รู้ว่าสดไหม = ไม่ push
    assert "::error::" in capsys.readouterr().out
    (_, _, result, value), = health.check_h14(path)  # รอบแรกหลัง deploy ไม่ทำให้ health check แดง
    assert result == "WARN" and "not found" in value


def test_unreadable_file_is_treated_as_missing(tmp_path):
    path = tmp_path / "meta" / "price_freshness.json"
    path.parent.mkdir()
    path.write_text("{not json", encoding="utf-8")
    assert pf.read_payload(path) is None
    assert gate.run(path) == 1


# ---- รอบวันหยุด: ด่านไม่ทำให้ job แดง -----------------------------------------------------------------------

WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/daily-scan.yml").read_text(encoding="utf-8"))
STEPS = WORKFLOW["jobs"]["run-pipeline"]["steps"]


def _step(prefix: str) -> int:
    return next(i for i, s in enumerate(STEPS) if s["name"].startswith(prefix))


def _cond(expr: str, non_trading: str) -> bool:
    """ประเมินเงื่อนไข `if:` แบบง่าย ๆ ของ workflow (เฉพาะรูป steps.run_pipeline.outputs.non_trading != 'true')"""
    return eval(expr.replace("steps.run_pipeline.outputs.non_trading", repr(non_trading)))


def test_workflow_gate_runs_before_copy_and_push_and_is_skipped_on_holidays():
    g = _step("Price freshness gate")
    assert g < _step("Copy JSON to stockdesk") < _step("Commit and Push to stockdesk")
    assert g > _step("Run Master Pipeline")
    assert "price_freshness_gate.py" in STEPS[g]["run"]
    cond = STEPS[g]["if"]
    assert _cond(cond, "false") is True              # วันซื้อขายปกติ: ด่านทำงาน
    assert _cond(cond, "true") is False              # NON_TRADING_DAY (guard ข้ามรอบ): ด่านถูกข้าม → job ไม่แดงเพราะด่าน
    assert WORKFLOW["jobs"]["run-pipeline"]["timeout-minutes"] == 60


def test_workflow_health_check_runs_after_push_and_always():
    assert _step("Commit and Push to stockdesk") < _step("Data health check")
    assert STEPS[_step("Data health check")]["if"] == "always()"
    assert STEPS[_step("Price freshness gate")]["id"] == "price_gate"
    assert "price_gate" in STEPS[_step("Record pipeline status")]["run"]


def test_h14_on_non_trading_day_never_fails_the_job(tmp_path):
    path = write(tmp_path, payload(fallback=873, outage=True, bulk_failed=873))
    (_, _, result, value), = health.check_h14(path, non_trading_day=True)
    assert result == "WARN" and "nothing pushed" in value


def test_holiday_run_end_to_end_with_stale_file(tmp_path, capsys):
    """วันหยุด: ด่านถูกข้าม (ไม่เรียก gate เลย) + H14 ลดเป็น WARN → health_check ทั้งรอบ exit 0 ในส่วนของราคา"""
    path = write(tmp_path, payload(fallback=400))
    assert _cond(STEPS[_step("Price freshness gate")]["if"], "true") is False
    results = health.check_h14(path, non_trading_day=True) + health.check_h14_weekday_skip(date(2026, 10, 23))
    assert [r[2] for r in results] == ["WARN"]       # 23 ต.ค. = วันปิยมหาราช อยู่ในรายการวันหยุด → ไม่มีแถวเตือนเพิ่ม


# ---- NON_TRADING_DAY วันธรรมดาที่ไม่ใช่วันหยุด SET -------------------------------------------------------

def test_holiday_list_2026():
    for d in [date(2026, 10, 13), date(2026, 10, 23), date(2026, 12, 31), date(2026, 4, 14), date(2026, 6, 1),
              date(2026, 12, 7), date(2026, 10, 16)]:          # 16 ต.ค. = วันหยุดพิเศษที่ประกาศภายหลัง (SPECIAL)
        assert is_set_holiday(d), d
    for d in [date(2026, 10, 14), date(2026, 10, 22), date(2026, 10, 26), date(2026, 12, 5)]:
        assert not is_set_holiday(d), d
    # ปฏิทิน SET ปี 2026 ในไฟล์ต้องมี 21 วันหยุดตามปฏิทิน + 1 วันพิเศษ
    from tools.core.set_holidays import SPECIAL, _HOLIDAYS
    assert len(_HOLIDAYS[2026]) == 21 and len(SPECIAL) == 1
    assert all(d.weekday() < 5 for d in _HOLIDAYS[2026]), "วันหยุดชดเชยต้องตกวันทำการ"


def test_weekday_not_in_holiday_list_warns():
    msg = non_trading_warning(date(2026, 10, 22))              # พฤหัส ธรรมดา
    assert msg and "อาจเป็นแหล่งข้อมูลล่ม" in msg and "ไม่ใช่วันหยุด" in msg and "2026-10-22" in msg
    (row,) = health.check_h14_weekday_skip(date(2026, 10, 22))
    assert row[0] == "H14" and row[2] == "WARN"


def test_listed_holiday_and_weekend_do_not_warn():
    assert non_trading_warning(date(2026, 10, 23)) is None     # วันปิยมหาราช
    assert non_trading_warning(date(2026, 10, 24)) is None     # เสาร์
    assert non_trading_warning(date(2026, 10, 25)) is None     # อาทิตย์
    assert health.check_h14_weekday_skip(date(2026, 10, 23)) == []


def test_year_without_calendar_still_warns_and_says_so():
    msg = non_trading_warning(date(2027, 3, 3))
    assert msg and "ยังไม่มีรายการวันหยุดปี 2027" in msg


# ---- ขั้นดาวน์โหลด: ข้ามการดึงซ้ำเมื่อ Yahoo น่าจะล่ม (mock ทั้งหมด ไม่ต่อเครือข่าย) ----------------------------

dl = _load("dl_history", "tools/core/2_download_history.py")


def _last_bday() -> date:
    return pd.bdate_range(end=pd.Timestamp(date.today()), periods=1)[0].date()


def _frame(rows: int = 210, seed: int = 0, last: date = None) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    last = last or date.today()
    idx = pd.bdate_range(end=pd.Timestamp(last), periods=rows)
    close = 10 + np.cumsum(rng.normal(0, 0.05, rows))
    return pd.DataFrame({"Open": close, "High": close + 0.1, "Low": close - 0.1, "Close": close,
                         "Volume": rng.integers(1000, 5000, rows)}, index=idx)


def _env(tmp_path, monkeypatch, tickers, failing):
    hist = tmp_path / "history"
    hist.mkdir()
    syms = tmp_path / "active_symbols.csv"
    pd.DataFrame({"Ticker": tickers}).to_csv(syms, index=False)
    # ไฟล์ราคาเก่า (เมื่อวาน) ของทุกตัว — ทางสำรองเมื่อดึงสดไม่ได้
    yesterday = date.today() - timedelta(days=1)
    for i, t in enumerate(tickers):
        dl.add_indicator_columns(_frame(seed=i, last=yesterday)).to_csv(hist / f"{t}.csv")
    idx = pd.bdate_range(end=pd.Timestamp(date.today()), periods=30)
    pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 1}, index=idx).to_csv(hist / "SET_INDEX.csv")

    frames = {t: _frame(seed=100 + i) for i, t in enumerate(tickers) if t not in failing}

    def fake_download(yf_tickers, **kw):
        got = {f"{t}.BK": frames[t] for t in (y[:-3] for y in yf_tickers) if t in frames}
        if not got:
            return pd.DataFrame()          # Yahoo ล่ม: yfinance คืนตารางว่าง (ไม่ raise)
        return pd.concat(got, axis=1)

    retried = []
    monkeypatch.setattr(dl.yf, "download", fake_download)
    monkeypatch.setattr(dl, "retry_single_ticker_download", lambda t: retried.append(t))
    monkeypatch.setattr(dl.time, "sleep", lambda s: None)
    monkeypatch.setattr(dl, "download_set_index", lambda: None)
    monkeypatch.setattr(dl, "update_set_index_volume", lambda d: None)
    monkeypatch.setattr(dl, "load_symbol_sources", lambda: ({t: "tv" for t in tickers}, True))
    monkeypatch.setattr(dl, "fetch_fundamentals", lambda t: (t, {}))
    monkeypatch.setattr(dl, "apply_growth_cache_fallback", lambda d: None)
    monkeypatch.setattr(dl, "apply_growth_freshness", lambda rows, tv, today: None)
    monkeypatch.setattr(dl, "get_authenticated_session", lambda: (_ for _ in ()).throw(RuntimeError("no network")))
    monkeypatch.setattr("requests.post", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no network")))
    for name, rel in [("SYMBOLS_FILE", syms.name), ("DAILY_FILE", "daily.csv"), ("SHORT_HISTORY_FILE", "short.json"),
                      ("MISSING_DATA_FILE", "missing.json")]:
        monkeypatch.setattr(dl, name, tmp_path / rel)
    monkeypatch.setattr(dl, "HISTORY_DIR", hist)
    fresh_file = tmp_path / "meta" / "price_freshness.json"
    monkeypatch.setattr(dl, "PRICE_FRESHNESS_FILE", fresh_file)
    return fresh_file, retried


def test_download_total_outage_skips_individual_retry(tmp_path, monkeypatch):
    tickers = [f"T{i:03d}" for i in range(60)]
    fresh_file, retried = _env(tmp_path, monkeypatch, tickers, failing=set(tickers))   # Yahoo ล่มทุกตัว
    # ไฟล์ของรอบก่อนต้องถูกลบ ไม่ค้างมาหลอก
    pf.write_payload(fresh_file, payload())
    dl.download_history_and_build_snapshot()
    assert retried == [], "Yahoo ล่ม ต้องไม่ดึงซ้ำทีละตัว"
    data = pf.read_payload(fresh_file)
    assert data["yahoo_outage_suspected"] is True and data["retry_skipped"] is True
    assert data["bulk_failed_count"] == 60 and data["universe_size"] == 60
    assert data["fresh_count"] == 0 and data["fallback_count"] == 60 and data["dropped_count"] == 0
    assert data["reference_date"] == str(_last_bday())
    assert pf.judge(data)[0] == pf.FAIL


def test_download_few_failures_still_retries_and_is_not_outage(tmp_path, monkeypatch):
    tickers = [f"T{i:03d}" for i in range(60)]
    failing = {"T001", "T002", "T003", "T004", "T005"}                                   # พลาด 5 ตัวเหมือนทุกวัน
    fresh_file, retried = _env(tmp_path, monkeypatch, tickers, failing=failing)
    dl.download_history_and_build_snapshot()
    assert sorted(retried) == sorted(failing)
    data = pf.read_payload(fresh_file)
    assert data["yahoo_outage_suspected"] is False and data["retry_skipped"] is False
    assert data["bulk_failed_count"] == 5 and data["fresh_count"] == 55
    assert data["fallback_tickers"] == sorted(failing) and data["dropped_count"] == 0
    assert data["stock_data_date"] == str(_last_bday())


def test_download_exactly_at_threshold_is_not_outage(tmp_path, monkeypatch):
    tickers = [f"T{i:03d}" for i in range(80)]
    failing = set(tickers[:50])                                                          # 50 ตัวพอดี: "เกิน" 50 ถึงจะล่ม
    fresh_file, retried = _env(tmp_path, monkeypatch, tickers, failing=failing)
    dl.download_history_and_build_snapshot()
    assert len(retried) == 50 and pf.read_payload(fresh_file)["yahoo_outage_suspected"] is False
