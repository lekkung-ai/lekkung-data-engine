"""ฤดูงบ: วันขอบทุกช่วง · ปีอธิกสุรทิน · เวลาไทยข้ามเที่ยงคืน · จำลองขั้นเช็คใน workflow จริง
(ไม่ต่อเครือข่าย · ไม่รัน pipeline)

    python -m pytest tools/core/test_earnings_season.py
"""
import os
import subprocess
import sys
import textwrap
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.core.earnings_season import (  # noqa: E402
    EARNINGS_SEASONS, bangkok_today, earnings_season_name, is_earnings_season, season_gate,
)

WF = ROOT / ".github/workflows"
MORNING = yaml.safe_load((WF / "iaa-research-morning.yml").read_text(encoding="utf-8"))
EVENING = yaml.safe_load((WF / "earnings-evening.yml").read_text(encoding="utf-8"))


# ---------- ขอบเขตของแต่ละช่วง (ปีธรรมดา 2026 และปีอธิกสุรทิน 2028) ----------
@pytest.mark.parametrize("year", [2026, 2027, 2028])
@pytest.mark.parametrize("d, expected", [
    # งบปี 1 ก.พ. – 31 มี.ค.
    ((1, 31), None), ((2, 1), "งบปี"), ((3, 31), "งบปี"), ((4, 1), None),
    # Q1 15 เม.ย. – 20 พ.ค.
    ((4, 14), None), ((4, 15), "Q1"), ((5, 20), "Q1"), ((5, 21), None),
    # Q2 15 ก.ค. – 20 ส.ค.
    ((7, 14), None), ((7, 15), "Q2"), ((8, 20), "Q2"), ((8, 21), None),
    # Q3 15 ต.ค. – 20 พ.ย.
    ((10, 14), None), ((10, 15), "Q3"), ((11, 20), "Q3"), ((11, 21), None),
])
def test_edges(year, d, expected):
    day = date(year, *d)
    assert earnings_season_name(day) == expected
    assert is_earnings_season(day) is (expected is not None)


@pytest.mark.parametrize("d", [(1, 1), (6, 15), (9, 30), (12, 1), (12, 31)])
def test_mid_off_season(d):
    assert not is_earnings_season(date(2026, *d))


@pytest.mark.parametrize("d", [(2, 15), (3, 15), (5, 1), (8, 1), (11, 11)])
def test_mid_in_season(d):
    assert is_earnings_season(date(2026, *d))


def test_leap_day_in_annual_season():
    assert is_earnings_season(date(2028, 2, 29))  # ปีอธิกสุรทิน
    assert is_earnings_season(date(2028, 2, 28)) and is_earnings_season(date(2028, 3, 1))
    assert not is_earnings_season(date(2028, 1, 31)) and is_earnings_season(date(2028, 2, 1))


def test_every_day_of_year_is_classified_consistently():
    """นับวันในฤดูทั้งปี 2026/2028 ให้ตรงกับช่วงที่ประกาศ (กันช่วงซ้อน/หลุด)"""
    for year in (2026, 2028):
        total = sum((date(year, *e) - date(year, *s)).days + 1 for _, s, e in EARNINGS_SEASONS)
        counted = sum(is_earnings_season(date(year, 1, 1) + timedelta(days=i))
                      for i in range((date(year + 1, 1, 1) - date(year, 1, 1)).days))
        assert counted == total
    # ช่วงไม่ซ้อนกัน เรียงตามเวลา
    ends = [e for _, _, e in EARNINGS_SEASONS]
    starts = [s for _, s, _ in EARNINGS_SEASONS]
    assert all(e < s for e, s in zip(ends, starts[1:]))


# ---------- เวลาไทย (UTC+7) ----------
UTC = timezone.utc


def test_bangkok_today_crosses_midnight():
    # 14 ต.ค. 17:00 UTC = 15 ต.ค. 00:00 ไทย → เข้าฤดู Q3 แล้วทั้งที่ UTC ยังเป็นวันที่ 14
    assert bangkok_today(datetime(2026, 10, 14, 16, 59, tzinfo=UTC)) == date(2026, 10, 14)
    assert bangkok_today(datetime(2026, 10, 14, 17, 0, tzinfo=UTC)) == date(2026, 10, 15)
    assert is_earnings_season(bangkok_today(datetime(2026, 10, 14, 17, 0, tzinfo=UTC)))
    assert not is_earnings_season(bangkok_today(datetime(2026, 10, 14, 16, 59, tzinfo=UTC)))
    # 20 พ.ย. 23:00 UTC = 21 พ.ย. 06:00 ไทย → พ้นฤดูแล้ว ทั้งที่ UTC ยังเป็นวันที่ 20
    assert not is_earnings_season(bangkok_today(datetime(2026, 11, 20, 23, 0, tzinfo=UTC)))
    assert is_earnings_season(bangkok_today(datetime(2026, 11, 20, 16, 59, tzinfo=UTC)))
    # 31 ม.ค. 17:00 UTC = 1 ก.พ. 00:00 ไทย ; 31 มี.ค. 17:00 UTC = 1 เม.ย. 00:00 ไทย (พ้นฤดู)
    assert is_earnings_season(bangkok_today(datetime(2026, 1, 31, 17, 0, tzinfo=UTC)))
    assert not is_earnings_season(bangkok_today(datetime(2026, 3, 31, 17, 0, tzinfo=UTC)))
    # 28 ก.พ. 17:00 UTC ปีอธิกสุรทิน = 29 ก.พ. ไทย
    assert bangkok_today(datetime(2028, 2, 28, 17, 0, tzinfo=UTC)) == date(2028, 2, 29)


def test_bangkok_today_other_tz_input_and_naive():
    assert bangkok_today(datetime(2026, 11, 11, 0, 30, tzinfo=timezone(timedelta(hours=-5)))) == date(2026, 11, 11)
    assert bangkok_today(datetime(2026, 11, 11, 20, 0, tzinfo=timezone(timedelta(hours=-5)))) == date(2026, 11, 12)
    with pytest.raises(ValueError):
        bangkok_today(datetime(2026, 11, 11, 12, 0))


# ---------- season_gate ----------
def test_season_gate():
    skip, msg = season_gate(date(2026, 11, 11))
    assert not skip and "Q3" in msg
    skip, msg = season_gate(date(2026, 12, 1))
    assert skip and "ignore_season" in msg
    skip, msg = season_gate(date(2026, 12, 1), ignore_season=True)
    assert not skip and "ignore_season" in msg
    assert season_gate(date(2026, 11, 11), ignore_season=True)[0] is False


# ---------- โครง workflow ----------
def _steps(wf):
    return next(iter(wf["jobs"].values()))["steps"]


def _step(wf, step_id):
    return next(s for s in _steps(wf) if s.get("id") == step_id)


def test_dispatch_input_is_optional_boolean_default_false():
    for wf in (MORNING, EVENING):
        inp = wf[True]["workflow_dispatch"]["inputs"]["ignore_season"]  # YAML 1.1: 'on' → True
        assert inp["type"] == "boolean" and inp["default"] is False and inp["required"] is False
        assert "schedule" not in wf[True]  # ยังเป็น cron-job.org อย่างเดียว


def test_holiday_step_does_not_read_ignore_season():
    holiday = _step(EVENING, "holiday")
    assert "ignore_season" not in str(holiday).lower()
    # ขั้นเช็คฤดูมาหลังขั้นเช็ควันหยุด
    ids = [s.get("id") for s in _steps(EVENING)]
    assert ids.index("holiday") < ids.index("season")
    # ถ้าวันหยุด/เสาร์อาทิตย์ → ขั้นเช็คฤดูไม่รัน แล้วขั้นดึงงบก็ยังถูกข้ามด้วยเงื่อนไขวันหยุดเดิม
    assert "holiday.outputs.skip != 'true'" in _step(EVENING, "season")["if"]


def test_every_evening_earnings_step_gated_by_both():
    gated = [s for s in _steps(EVENING) if s.get("id") not in ("holiday", "season")
             and s["name"] != "Setup Python" and not s["name"].startswith("Checkout data_engine")]
    assert len(gated) == 6
    for s in gated:
        assert "steps.holiday.outputs.skip != 'true'" in s["if"], s["name"]
        assert "steps.season.outputs.skip != 'true'" in s["if"], s["name"]


def test_morning_gates_only_earnings_steps():
    """ทุกขั้นหลังเช็ควันหยุด gate ด้วยวันหยุด · ขั้นดึงงบ gate ด้วยวันหยุด + ฤดูงบ · ขั้น research ไม่ดูฤดูงบ"""
    always = {"Checkout data_engine repository", "Setup Python", "Skip SET holidays and weekends"}
    research = {"Check earnings season",
                "Install Playwright (only dependency this script needs)", "Checkout stockdesk repository",
                "Fetch IAA research (merges with existing file - no gaps)",
                "Commit and push research_iaa.json only"}
    seen = set()
    for s in _steps(MORNING):
        seen.add(s["name"])
        if s["name"] in always:
            assert "if" not in s, s["name"]
        elif s["name"] in research:
            assert s["if"] == "steps.holiday.outputs.skip != 'true'", s["name"]
        else:
            assert s["if"] == "steps.holiday.outputs.skip != 'true' && steps.season.outputs.skip != 'true'", s["name"]
    assert always | research <= seen
    assert len([s for s in _steps(MORNING) if "steps.season.outputs.skip" in s.get("if", "")]) == 5
    ids = [s.get("id") for s in _steps(MORNING)]
    assert ids.index("holiday") < ids.index("season")
    assert "ignore_season" not in str(_step(MORNING, "holiday")).lower()


# ---------- จำลองขั้นเช็คฤดูใน workflow จริง ----------
SHIM = textwrap.dedent('''
    import datetime as _real, sys, types
    FIXED = {fixed}
    class _DT(_real.datetime):
        @classmethod
        def now(cls, tz=None):
            return FIXED.astimezone(tz) if tz else FIXED.replace(tzinfo=None)
    _m = types.ModuleType("datetime"); _m.__dict__.update(_real.__dict__); _m.datetime = _DT
    sys.modules["datetime"] = _m
''')


def _run_step(wf, step_id, now_utc, ignore, tmp_path):
    """รัน script ของขั้น step_id (จาก YAML จริง) โดยล็อกนาฬิกา → คืน (GITHUB_OUTPUT, summary, stdout)"""
    script = _step(wf, step_id)["run"]
    assert script.lstrip().startswith("python - <<'PYEOF'")
    code = script.split("<<'PYEOF'\n", 1)[1].rsplit("PYEOF", 1)[0]
    fixed = f"_real.datetime({now_utc.year},{now_utc.month},{now_utc.day},{now_utc.hour},{now_utc.minute},tzinfo=_real.timezone.utc)"
    out, summ = tmp_path / "out.txt", tmp_path / "summary.md"
    out.write_text("", encoding="utf-8"); summ.write_text("", encoding="utf-8")
    env = {**os.environ, "GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(summ),
           "IGNORE_SEASON": "true" if ignore else "false", "PYTHONUTF8": "1"}
    r = subprocess.run([sys.executable, "-c", SHIM.format(fixed=fixed) + code], cwd=ROOT, env=env,
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return out.read_text(encoding="utf-8"), summ.read_text(encoding="utf-8"), r.stdout


@pytest.mark.parametrize("wf, name", [(MORNING, "morning"), (EVENING, "evening")])
@pytest.mark.parametrize("now, ignore, expect_skip", [
    (datetime(2026, 11, 11, 3, 0, tzinfo=UTC), False, "false"),    # 11 พ.ย. → อยู่ในฤดู
    (datetime(2026, 12, 1, 3, 0, tzinfo=UTC), False, "true"),      # 1 ธ.ค. → ข้าม
    (datetime(2026, 12, 1, 3, 0, tzinfo=UTC), True, "false"),      # 1 ธ.ค. + ignore_season → รัน
    (datetime(2026, 11, 20, 17, 30, tzinfo=UTC), False, "true"),   # 21 พ.ย. 00:30 ไทย (UTC ยังเป็น 20)
    (datetime(2026, 10, 14, 17, 30, tzinfo=UTC), False, "false"),  # 15 ต.ค. 00:30 ไทย (UTC ยังเป็น 14)
])
def test_workflow_step_simulation(wf, name, now, ignore, expect_skip, tmp_path):
    out, summary, stdout = _run_step(wf, "season", now, ignore, tmp_path)
    assert out.strip() == f"skip={expect_skip}"
    assert "นอกฤดูงบ" in summary or "ฤดูงบ" in summary
    assert bangkok_today(now).isoformat() in stdout


def test_ignore_season_does_not_bypass_holiday_step(tmp_path):
    """ขั้นเช็ควันหยุด ไม่อ่าน ignore_season: 13 ต.ค. 2026 (วันหยุด) + ignore → ยัง skip=true"""
    out, _, _ = _run_step(EVENING, "holiday", datetime(2026, 10, 13, 3, 0, tzinfo=UTC), True, tmp_path)
    assert out.strip() == "skip=true"
    # 11 พ.ย. 2026 (พุธ ไม่ใช่วันหยุด) → รัน
    out, _, _ = _run_step(EVENING, "holiday", datetime(2026, 11, 11, 3, 0, tzinfo=UTC), False, tmp_path)
    assert out.strip() == "skip=false"


# ---------- รอบเช้า: เช็ควันหยุด SET + เสาร์–อาทิตย์ (holiday_gate) ----------
@pytest.mark.parametrize("ignore", [False, True])
@pytest.mark.parametrize("now, expect_skip, word", [
    (datetime(2026, 10, 13, 2, 45, tzinfo=UTC), "true", "วันหยุด SET"),   # อ. 13 ต.ค. 2026 (ในตาราง) 09:45 ไทย
    (datetime(2026, 10, 16, 2, 45, tzinfo=UTC), "true", "วันหยุด SET"),   # ศ. 16 ต.ค. 2026 (SPECIAL)
    (datetime(2026, 11, 14, 2, 45, tzinfo=UTC), "true", "วันเสาร์"),       # ส. 14 พ.ย. 2026
    (datetime(2026, 11, 15, 2, 45, tzinfo=UTC), "true", "วันอาทิตย์"),     # อา. 15 พ.ย. 2026
    (datetime(2026, 11, 18, 2, 45, tzinfo=UTC), "false", "วันทำการ SET"),  # พ. 18 พ.ย. 2026
    (datetime(2026, 11, 13, 17, 30, tzinfo=UTC), "true", "วันเสาร์"),      # ส. 14 พ.ย. 00:30 ไทย (UTC ยังเป็นศุกร์)
])
def test_morning_holiday_step(now, expect_skip, word, ignore, tmp_path):
    out, _, stdout = _run_step(MORNING, "holiday", now, ignore, tmp_path)
    assert out.strip() == f"skip={expect_skip}"
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    assert len(lines) == 1 and word in lines[0] and bangkok_today(now).isoformat() in lines[0]
    assert lines[0].startswith("::notice::") == (expect_skip == "true")


@pytest.mark.parametrize("ignore", [False, True])
@pytest.mark.parametrize("now, holiday_skip, season_skip_no_ignore", [
    (datetime(2026, 10, 13, 2, 45, tzinfo=UTC), True, False),   # วันหยุดในฤดูงบ
    (datetime(2026, 12, 7, 2, 45, tzinfo=UTC), True, True),     # วันหยุดนอกฤดูงบ
    (datetime(2026, 11, 18, 2, 45, tzinfo=UTC), False, False),  # วันทำการในฤดูงบ
    (datetime(2026, 12, 1, 2, 45, tzinfo=UTC), False, True),    # วันทำการนอกฤดูงบ
])
def test_morning_which_steps_run(now, holiday_skip, season_skip_no_ignore, ignore, tmp_path):
    """ผลรวมของสองขั้น: วันหยุด → ไม่มีอะไรรันเลย (ignore_season ไม่ช่วย) · วันทำการ → research รัน
    และขั้นดึงงบรันตามฤดูงบ / ignore_season เหมือนเดิม"""
    out, _, _ = _run_step(MORNING, "holiday", now, ignore, tmp_path)
    assert out.strip() == f"skip={'true' if holiday_skip else 'false'}"
    if holiday_skip:
        return  # ขั้น season และทุกขั้นถัดไปมี if: steps.holiday.outputs.skip != 'true' (test ข้างบน)
    out, _, _ = _run_step(MORNING, "season", now, ignore, tmp_path)
    assert out.strip() == f"skip={'true' if (season_skip_no_ignore and not ignore) else 'false'}"


def test_holiday_gate_unit():
    from tools.core.set_holidays import holiday_gate
    assert holiday_gate(date(2026, 12, 31))[0] is True
    assert holiday_gate(date(2026, 12, 30))[0] is False
    skip, msg = holiday_gate(date(2027, 1, 4))  # จ. ปีที่ยังไม่มีตาราง → ไม่ข้าม แต่บอกไว้
    assert skip is False and "2027" in msg
    assert holiday_gate(date(2027, 1, 2))[0] is True  # เสาร์ ยังข้ามได้แม้ไม่มีตาราง
