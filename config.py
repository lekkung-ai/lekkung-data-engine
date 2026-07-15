"""
config.py — Single source of truth (ฉบับสมบูรณ์)
แผนที่ศูนย์กลางของ data_engine ครอบคลุมทั้ง Tools และ Data
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    # Load .env file if it exists in the ROOT or current directory
    load_dotenv()
except ImportError:
    pass

# ============================================================
# 📍 ROOT — จุดศูนย์กลาง (พิกัดปัจจุบันคือโฟลเดอร์ data_engine)
# ============================================================
ROOT = Path(__file__).resolve().parent

# ============================================================
# 🛠️ TOOLS PATHS (ระบุพิกัดโฟลเดอร์เก็บโค้ดต่างๆ)
# ============================================================
TOOLS_DIR = ROOT / "tools"
CORE_DIR = TOOLS_DIR / "core"
SCANNER_DIR = TOOLS_DIR / "scanner"
NEWS_TOOLS_DIR = TOOLS_DIR / "news"
AI_AGENTS_DIR = TOOLS_DIR / "ai_agents"

# ============================================================
# 📁 DATA PATHS (ระบุพิกัดโฟลเดอร์เก็บไฟล์ CSV)
# ============================================================
DATA_DIR = ROOT / "data"
HISTORY_DIR = DATA_DIR / "history"
RESULTS_DIR = DATA_DIR / "results"
NEWS_DIR = DATA_DIR / "news"
LOGS_DIR = DATA_DIR / "logs"
MANUAL_DIR = DATA_DIR / "manual_input"

# 📄 กำหนดชื่อไฟล์ผลลัพธ์ (Result Files)
SYMBOLS_FILE = RESULTS_DIR / "active_symbols.csv"
DAILY_FILE = RESULTS_DIR / "daily_prices.csv"
RS_FILE = RESULTS_DIR / "rs_ranking.csv"
SEPA_FILE = RESULTS_DIR / "sepa_result.csv"
COMBO_FILE = RESULTS_DIR / "sepa_stage2_combo.csv"
WYCKOFF_FILE = RESULTS_DIR / "wyckoff_stages.csv"
PINE_STAGES_FILE = RESULTS_DIR / "pine_stages.csv"
ACCUM_FILE = RESULTS_DIR / "accumulation_breakout.csv"
KELL_FILE = RESULTS_DIR / "oliver_kell_signals.csv"
VDU_FILE = RESULTS_DIR / "vdu_signals.csv"
WARRANT_FILE = RESULTS_DIR / "warrant_vs_parent.csv"
CANSLIM_FILE = RESULTS_DIR / "canslim_result.csv"
WARRANTS_MASTER = MANUAL_DIR / "warrants_master.csv"

# 🗞️ กำหนดชื่อไฟล์ข่าว (News Files)
NEWS_FILE = NEWS_DIR / "news_feed.csv"
SEPA_NEWS_FILE = NEWS_DIR / "sepa_news.csv"

# 📝 ไฟล์บันทึกการทำงาน (Log)
LOG_FILE = LOGS_DIR / "run_log.txt"

# ============================================================
# 🎛️ SCANNER THRESHOLDS — ปรับจูนค่าเกณฑ์การสแกนหุ้น
# ============================================================
ADTV_MIN_MB = 10.0
MIN_HISTORY_DAYS = 252
RS_MIN_THRESHOLD = 70
SEPA_SLOPE_MIN = 0.0
WEINSTEIN_SLOPE = 1.0

# 📉 PRICE DOWNLOAD FALLBACK (2_download_history.py) — when yf.download fails
# or returns <200 bars for a ticker even after a short retry, reuse its
# existing data/history/{ticker}.csv file instead of dropping it from
# daily_prices.csv entirely, as long as that file isn't too stale.
PRICE_FALLBACK_MAX_STALE_BUSINESS_DAYS = 5  # older than this -> drop (likely SP/delisted), not silently reused
PRICE_FALLBACK_WARN_PCT = 0.10  # fallback rate above this fraction of the universe -> print a loud warning (systemic Yahoo issue, not per-ticker)

BASE_WINDOW = 150
MAX_BASE_WIDTH = 45.0
FLAT_SLOPE_RANGE = 2.5
NEAR_BREAK_PCT = 5.0

# ============================================================
# 📉 MARKET BREADTH / FTD-DD THRESHOLDS (calculate_breadth.py)
# ============================================================
BREADTH_MA_PERIODS = [10, 20, 50, 200]
BREADTH_BACKFILL_DAYS = 120       # trading days of breadth/SET-index history to output
BREADTH_MIN_HISTORY_DAYS = MIN_HISTORY_DAYS  # universe filter, reuses the 252-day rule used elsewhere
NEW_HIGH_LOW_WINDOW = 252          # trailing window for "new 52W high/low" (calendar convention: 1 trading year)
SPARKLINE_DAYS = 30

SET_INDEX_YF_SYMBOL = "^SET.BK"
SET_INDEX_FETCH_PERIOD = "2y"      # enough lookback for MA200 + FTD/DD state machine warm-up

DD_MIN_DECLINE_PCT = 0.2           # Distribution Day: SET index down >= this %, on higher volume than prior day
FTD_MIN_GAIN_PCT = 1.5             # Follow-Through Day: SET index up >= this %, on higher volume than prior day
FTD_WINDOW_START_DAY = 4           # FTD only counts on day 4-10 of a rally attempt (O'Neil convention)
FTD_WINDOW_END_DAY = 10

MARKET_STAGE_DD_CORRECTION = 6     # dd_count in trailing 25 sessions >= this -> "Correction"
MARKET_STAGE_DD_PRESSURE = 4       # dd_count in trailing 25 sessions >= this -> "Under Pressure"
MARKET_STAGE_MA50_PRESSURE_PCT = 35  # pct_above_ma50 below this -> "Under Pressure" even if DD count is low

# ============================================================
# 🤖 EXTERNAL SERVICES
# ============================================================
LINE_TOKEN = os.getenv("LINE_TOKEN", "")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")  # ใช้ Qwen ตามที่ตกลงกัน
OLLAMA_URL = "http://localhost:11434/api/generate"

# ============================================================
# ⏳ DOWNLOAD SETTINGS
# ============================================================
CHUNK_SIZE = 100
CHUNK_SLEEP = 1.5
FUND_WORKERS = 10  # parallel workers สำหรับดึง fundamentals

# ============================================================
# 📰 NEWS SOURCES
# ============================================================
NEWS_SOURCES = {
    "SET Newsroom": {
        "url": "https://www.set.or.th/api/set/news/search?lang=th&keyword=&pageSize=50",
        "type": "set_json",
    },
    "Finnomena": {
        "url": "https://www.finnomena.com/feed/",
        "type": "rss",
    },
    "Investing.com TH": {
        "url": "https://th.investing.com/rss/news_14.rss",
        "type": "rss",
    },
}
NEWS_MAX_PER_TICKER = 5
NEWS_REQUEST_TIMEOUT = 12

# ============================================================
# 🚫 SYMBOL FILTER
# ============================================================
EXCLUDED_SUFFIXES = ("-R", ".R", "-F", ".F", "-W", "-P", "-IF", "-DR", "-U", "-T")
KNOWN_NON_STOCKS = {
    "WHART",
    "AMATAR",
    "TLGF",
    "DIF",
    "JASIF",
    "BTSGIF",
    "TFFIF",
    "EGATIF",
    "LHPF",
    "SHREIT",
    "POPF",
}


# ============================================================
# 🛠️ HELPERS
# ============================================================
def ensure_dirs():
    """สร้างโฟลเดอร์ data ย่อยๆ ให้ครบ ถ้ายังไม่มี"""
    for d in [HISTORY_DIR, RESULTS_DIR, NEWS_DIR, LOGS_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def p(path_obj) -> str:
    """แปลง Path Object ให้เป็นข้อความ (String)"""
    return str(path_obj)


ensure_dirs()
