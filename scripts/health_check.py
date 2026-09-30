"""
health_check.py — ตรวจสุขภาพข้อมูลท้าย pipeline (daily-scan.yml step "Data health check")

อ่านไฟล์ที่ push ขึ้น stockdesk แล้ว (git show <rev>:<path> ใน stockdesk repo — ไม่แตะ
working tree) + SET_INDEX.csv / ไฟล์หุ้นใน HISTORY_DIR ของ data_engine แล้ว print ตาราง
ผลทุกข้อ · exit 1 เมื่อมีข้อใดไม่ผ่าน (job แดง) · exit 0 เมื่อผ่านทุกข้อ

วันอ้างอิง = วันที่ล่าสุดที่มี Volume > 0 ที่มากที่สุดในไฟล์หุ้น (last_traded_date ตัวเดียวกับ
ตัวกรองไฟล์ค้าง find_stale_files) — ไม่ใช้วันนี้ เพราะ CI รันวันหยุดด้วย

  H1  SET_INDEX.csv: วันสุดท้าย = วันอ้างอิง
  H2  breadth.json: set_index วันสุดท้าย = วันอ้างอิง · volume แถวสุดท้าย > 0
  H3  sector_rs.json / sector_flow.json: generated_at เป็นของ run นี้ (>= --run-start =
      RUN_START จาก step "Record run start")
      · sector_flow.as_of = วันอ้างอิง · sector_flow.benchmark = "SET_INDEX"
      · ถ้า benchmark = "SET_INDEX" วันสุดท้ายของ SET_INDEX.csv ต้อง = sector_flow.as_of
  H4  combined.json: ไม่มี ticker ใน NON_STOCK_FILES · จำนวนแถวต่างจาก commit data ก่อนหน้า
      (commit ก่อนหน้าที่แก้ combined.json) ไม่เกิน ±5%
  H5  ทุก JSON ที่ copy ไป stockdesk รอบนี้: ไม่มี NaN / Infinity
  H6  JSON ที่ copy ไป stockdesk รอบนี้: generated_at ไม่เก่ากว่าวันอ้างอิงเกิน 2 วันทำการ

"JSON ที่ copy ไป stockdesk รอบนี้" = ชื่อไฟล์ *.json ใน --output-dir (data/results/output
ของ runner ซึ่งสดทุก run เพราะ gitignore) — ตรงกับ step "Copy JSON to stockdesk"

Usage:
    python scripts/health_check.py --run-start "$RUN_START"   # CI: path default ตาม workspace ของ runner
    python scripts/health_check.py --stockdesk-repo ../stockdesk --rev origin/main \\
        --history-dir data/history --output-dir <dir> --run-start 2026-09-29T21:00:00+07:00
"""

import argparse
import json
import math
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))
from config import HISTORY_DIR, RESULTS_DIR  # noqa: E402
from tools.scanner.utils import NON_STOCK_FILES, last_traded_date  # noqa: E402

BANGKOK_TZ = timezone(timedelta(hours=7))
SCANS = "data/scans"

# H4 — จำนวนแถว combined.json เทียบ commit data ก่อนหน้า (ตามสเปก) · ย้อนหลัง 40 commit
# (2026-08-21 → 2026-09-29) เปลี่ยนต่อรอบสูงสุด ~2% (875 → 858)
COMBINED_MAX_ROW_CHANGE = 0.05
# H6 — เกินกี่วันทำการ (จันทร์–ศุกร์) จากวันอ้างอิงถือว่าค้าง
STALE_MAX_BUSINESS_DAYS = 2
# H6 — ไฟล์ที่ตั้งใจให้อัปเดตไม่ทุกวัน → ไม่ตรวจความสด (ยังตรวจ H5)
# ว่างอยู่: universe_changes.json อัปเดตแค่วันจันทร์ (หรือ force) แต่ check_universe.py เขียนลง
# data/results/output เฉพาะรอบที่รันเท่านั้น → รอบที่มันถูก copy มันสดเสมอ ไม่ต้องยกเว้น
H6_ALLOWLIST: Dict[str, str] = {}
# H6 — ไฟล์ที่ไม่มี field generated_at → ใช้ field วันที่ของข้อมูลแทน
#   nvdr.json (convert_to_json.build_nvdr): มีแต่ latest_date = วันล่าสุดใน nvdr_history.csv
#   ปกติช้ากว่าวันอ้างอิง 1 วันทำการ (SET ออกข้อมูล NVDR ย้อนหลัง 1 วัน)
DATE_FIELD_FALLBACK = {"nvdr.json": "latest_date"}
# H6 — ไฟล์ scan ที่เป็น list เปล่า (lekkung.json ฯลฯ) ใช้เวลาใน manifest นี้ (key = ชื่อไฟล์ไม่รวม .json)
MANIFEST = "generated_at.json"


# ------------------------------------------------------------------ helpers
class Git:
    def __init__(self, repo: Path, rev: str):
        self.repo, self.rev = repo, rev

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True)

    def read(self, path: str, rev: Optional[str] = None) -> Optional[bytes]:
        r = self._run("show", f"{rev or self.rev}:{path}")
        return r.stdout if r.returncode == 0 else None

    def commits_touching(self, path: str, n: int) -> List[str]:
        r = self._run("log", "-n", str(n), "--format=%H", self.rev, "--", path)
        return r.stdout.decode().split() if r.returncode == 0 else []


def load_json(raw: bytes) -> Tuple[Any, List[str]]:
    """(ข้อมูล, รายการ NaN/Infinity ที่เจอ) — json ของ Python รับ NaN/Infinity/-Infinity โดย default
    ดักด้วย parse_constant แทนการ grep ข้อความ (กันเจอคำว่า NaN ใน string)"""
    found: List[str] = []

    def _const(tok: str) -> float:
        found.append(tok)
        return float("nan")

    return json.loads(raw.decode("utf-8"), parse_constant=_const), found


def to_bkk_date(ts: str) -> date:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BANGKOK_TZ)
    return dt.astimezone(BANGKOK_TZ).date()


def busdays(d: date, ref: date) -> int:
    return int(np.busday_count(d, ref))


def reference_date(history_dir: Path) -> Optional[date]:
    """วันอ้างอิง = max(last_traded_date) ของไฟล์หุ้น (ไม่รวม NON_STOCK_FILES) — เหมือน find_stale_files"""
    days = [
        d
        for p in history_dir.glob("*.csv")
        if p.name not in NON_STOCK_FILES and (d := last_traded_date(p)) is not None
    ]
    return max(days) if days else None


def last_csv_date(path: Path) -> Optional[date]:
    """วันที่ของแถวข้อมูลแถวสุดท้าย (คอลัมน์แรก) ของ CSV"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            return date.fromisoformat(line.split(",", 1)[0].strip()[:10])
        except ValueError:
            continue
    return None


# ------------------------------------------------------------------ checks
Result = Tuple[str, str, bool, str]  # (id, หัวข้อ, ผ่าน, ค่าที่เจอ)


def check_h1(history_dir: Path, ref: date) -> List[Result]:
    last = last_csv_date(history_dir / "SET_INDEX.csv")
    return [("H1", "SET_INDEX.csv last date = ref", last == ref, f"last={last} ref={ref}")]


def check_h2(git: Git, ref: date) -> List[Result]:
    raw = git.read(f"{SCANS}/breadth.json")
    if raw is None:
        return [("H2", "breadth.json readable", False, "file not found")]
    rows = (load_json(raw)[0] or {}).get("set_index") or []
    if not rows:
        return [("H2", "breadth.json set_index non-empty", False, "set_index empty/missing")]
    last = rows[-1]
    return [
        ("H2", "breadth.set_index last date = ref", last.get("date") == ref.isoformat(),
         f"last={last.get('date')} ref={ref}"),
        ("H2", "breadth.set_index last volume > 0", (last.get("volume") or 0) > 0,
         f"volume={last.get('volume')}"),
    ]


def check_h3(git: Git, history_dir: Path, ref: date, run_start: datetime) -> List[Result]:
    out: List[Result] = []
    data: Dict[str, dict] = {}
    for name in ("sector_rs.json", "sector_flow.json"):
        raw = git.read(f"{SCANS}/{name}")
        data[name] = load_json(raw)[0] if raw is not None else {}
        ga = data[name].get("generated_at")
        try:
            ok = ga is not None and datetime.fromisoformat(ga) >= run_start
        except ValueError:
            ok = False
        out.append(("H3", f"{name} generated_at from this run", ok,
                    f"generated_at={ga} run_start={run_start.isoformat(timespec='seconds')}"))
    flow = data["sector_flow.json"]
    out.append(("H3", "sector_flow.as_of = ref", flow.get("as_of") == ref.isoformat(),
                f"as_of={flow.get('as_of')} ref={ref}"))
    out.append(("H3", "sector_flow.benchmark = SET_INDEX", flow.get("benchmark") == "SET_INDEX",
                f"benchmark={flow.get('benchmark')}"))
    # เทียบ SET_INDEX ได้เฉพาะเมื่อจบวันเดียวกับข้อมูลหุ้น (calculate_sector_flow.py ต้อง fallback ถ้าไม่ตรง)
    if flow.get("benchmark") == "SET_INDEX":
        idx_last = last_csv_date(history_dir / "SET_INDEX.csv")
        out.append(("H3", "SET_INDEX benchmark: SET_INDEX.csv last date = as_of",
                    idx_last is not None and idx_last.isoformat() == flow.get("as_of"),
                    f"SET_INDEX.csv last={idx_last} as_of={flow.get('as_of')}"))
    return out


def _combined_rows(raw: bytes) -> List[dict]:
    d = load_json(raw)[0]
    return d.get("data") or [] if isinstance(d, dict) else d


def check_h4(git: Git) -> List[Result]:
    path = f"{SCANS}/combined.json"
    raw = git.read(path)
    if raw is None:
        return [("H4", "combined.json readable", False, "file not found")]
    rows = _combined_rows(raw)
    banned = {Path(n).stem.upper() for n in NON_STOCK_FILES}
    hit = sorted({str(r.get("ticker")) for r in rows if str(r.get("ticker", "")).upper() in banned})
    out: List[Result] = [("H4", "combined.json has no NON_STOCK ticker", not hit,
                          f"found={hit or 'none'} (banned={sorted(banned)})")]

    # commit ล่าสุดที่แก้ combined.json = รอบนี้ · ตัวก่อนหน้า = commit data ก่อนหน้า
    # (CI checkout เป็น shallow: commit ขอบ shallow นับเป็น root จึงยังโผล่ใน log ถ้ามีไฟล์)
    commits = git.commits_touching(path, 2)
    if len(commits) < 2:
        out.append(("H4", "combined.json rows vs previous data commit", False,
                    f"previous version not found (commits touching file: {len(commits)})"))
        return out
    prev_raw = git.read(path, commits[1])
    prev_n = len(_combined_rows(prev_raw)) if prev_raw is not None else 0
    n = len(rows)
    change = (n - prev_n) / prev_n if prev_n else math.inf
    out.append(("H4", f"combined.json rows within ±{COMBINED_MAX_ROW_CHANGE:.0%} of previous",
                abs(change) <= COMBINED_MAX_ROW_CHANGE,
                f"now={n} prev={prev_n} ({commits[1][:7]}) change={change:+.2%}"))
    return out


def _file_date(name: str, d: Any, manifest: Optional[dict]) -> Tuple[Optional[date], str]:
    """(วันที่ของไฟล์ตามเวลา Bangkok, field ที่ใช้)"""
    if isinstance(d, dict) and d.get("generated_at"):
        return to_bkk_date(d["generated_at"]), "generated_at"
    if isinstance(d, dict) and name in DATE_FIELD_FALLBACK and d.get(DATE_FIELD_FALLBACK[name]):
        field = DATE_FIELD_FALLBACK[name]
        return date.fromisoformat(str(d[field])[:10]), field
    if name == MANIFEST and isinstance(d, dict) and d:
        return min(to_bkk_date(v) for v in d.values()), "min(values)"
    stem = name[: -len(".json")]
    if manifest and manifest.get(stem):
        return to_bkk_date(manifest[stem]), f"{MANIFEST}[{stem}]"
    return None, "no timestamp field"


def check_h5_h6(git: Git, names: List[str], ref: date) -> List[Result]:
    if not names:
        return [("H5", "JSON copied this run", False, "no *.json in --output-dir"),
                ("H6", "JSON copied this run", False, "no *.json in --output-dir")]
    parsed: Dict[str, Any] = {}
    bad_nan, unreadable = [], []
    for name in names:
        raw = git.read(f"{SCANS}/{name}")
        if raw is None:
            unreadable.append(name)
            continue
        try:
            parsed[name], consts = load_json(raw)
        except (ValueError, UnicodeDecodeError) as e:
            unreadable.append(f"{name}({e.__class__.__name__})")
            continue
        if consts:
            bad_nan.append(f"{name}({len(consts)}x {sorted(set(consts))})")
    out: List[Result] = [
        ("H5", f"no NaN/Infinity in {len(names)} copied JSON", not bad_nan and not unreadable,
         f"nan/inf={bad_nan or 'none'} unreadable={unreadable or 'none'}")
    ]

    manifest = parsed.get(MANIFEST)
    stale, notes = [], []
    for name in names:
        if name not in parsed:
            continue
        if name in H6_ALLOWLIST:
            notes.append(f"{name}(allowlist)")
            continue
        d, field = _file_date(name, parsed[name], manifest)
        if d is None:
            stale.append(f"{name}({field})")
        elif busdays(d, ref) > STALE_MAX_BUSINESS_DAYS:
            stale.append(f"{name}({field}={d}, {busdays(d, ref)} bdays)")
        elif field != "generated_at":
            notes.append(f"{name}({field}={d})")
    out.append(("H6", f"copied JSON not older than {STALE_MAX_BUSINESS_DAYS} business days vs ref",
                not stale and not unreadable,
                f"stale={stale or 'none'} · checked={len(parsed)} · non-generated_at={notes or 'none'}"))
    return out


# ------------------------------------------------------------------ main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stockdesk-repo", type=Path, default=ROOT / "stockdesk_repo")
    ap.add_argument("--rev", default="origin/main",
                    help="stockdesk revision to read (default origin/main = what the pushes above landed)")
    ap.add_argument("--history-dir", type=Path, default=HISTORY_DIR)
    ap.add_argument("--output-dir", type=Path, default=RESULTS_DIR / "output",
                    help="dir whose *.json names = files copied to stockdesk this run")
    ap.add_argument("--run-start",
                    help="ISO time this run started - daily-scan.yml passes $RUN_START "
                         "(missing/empty: fall back to mtime of run_all.py + WARNING)")
    args = ap.parse_args()

    if args.run_start:
        run_start = datetime.fromisoformat(args.run_start)
        run_start_src = "--run-start"
    else:
        # ตัวสำรอง: mtime ของ run_all.py = ตอน actions/checkout (ใกล้เวลาเริ่ม job แต่ไม่ใช่ตัวจริง)
        run_start = datetime.fromtimestamp((ROOT / "run_all.py").stat().st_mtime, BANGKOK_TZ)
        run_start_src = "mtime of run_all.py"
        print("WARNING: --run-start not given (RUN_START empty?) - falling back to mtime of run_all.py")
    if run_start.tzinfo is None:
        run_start = run_start.replace(tzinfo=BANGKOK_TZ)
    git = Git(args.stockdesk_repo, args.rev)
    names = sorted(p.name for p in args.output_dir.glob("*.json")) if args.output_dir.is_dir() else []

    ref = reference_date(args.history_dir)
    print(f"stockdesk: {args.stockdesk_repo} @ {args.rev} · history: {args.history_dir}")
    print(f"reference date (latest Volume>0 bar across stock files): {ref}")
    print(f"run start: {run_start.isoformat(timespec='seconds')} (from {run_start_src})")
    print(f"copied JSON this run ({len(names)}): {', '.join(names) or '-'}")

    results: List[Result] = []
    if ref is None:
        results.append(("REF", "reference date", False, f"no stock file with Volume>0 in {args.history_dir}"))
    else:
        results += check_h1(args.history_dir, ref)
        results += check_h2(git, ref)
        results += check_h3(git, args.history_dir, ref, run_start)
    results += check_h4(git)
    if ref is not None:
        results += check_h5_h6(git, names, ref)

    print()
    w = max(len(r[1]) for r in results)
    print(f"{'ID':<4} {'CHECK':<{w}}  {'RESULT':<6}  VALUE")
    print(f"{'-' * 4} {'-' * w}  {'-' * 6}  {'-' * 40}")
    for hid, title, ok, value in results:
        print(f"{hid:<4} {title:<{w}}  {'PASS' if ok else 'FAIL':<6}  {value}")

    failed = [r for r in results if not r[2]]
    print()
    if failed:
        for hid, title, _, value in failed:
            print(f"::error::{hid} {title}: {value}")
        print(f"❌ {len(failed)}/{len(results)} checks failed: {', '.join(sorted({r[0] for r in failed}))}")
        return 1
    print(f"✅ all {len(results)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
