"""Offline checks for fetch_macro.TV_PRIMARY (no network).

python -m pytest tools/macro/test_fetch_macro.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch_macro as fm  # noqa: E402
from macro_mapping import COMMODITIES  # noqa: E402


def test_tv_primary_symbols_are_known_commodities():
    assert set(fm.TV_PRIMARY) <= set(COMMODITIES)


def test_tv_primary_is_never_also_a_direct_scanner_symbol():
    # fetch_symbol() sends DIRECT_TV_MAPPING symbols to the scanner first, so TV_PRIMARY would be dead code
    assert not set(fm.TV_PRIMARY) & set(fm.DIRECT_TV_MAPPING)


def test_tv_symbols_look_like_exchange_colon_symbol_and_are_unique():
    for yahoo, tv in fm.TV_PRIMARY.items():
        assert re.fullmatch(r"[A-Z_]+:[A-Z0-9.!]+", tv), (yahoo, tv)
    assert len(set(fm.TV_PRIMARY.values())) == len(fm.TV_PRIMARY)


def test_gold_uses_comex_futures_not_spot():
    # GC=F is the COMEX contract; TVC:GOLD is spot and differs from it
    assert fm.TV_PRIMARY["GC=F"] == "COMEX:GC1!"


def test_moved_symbols_stay_in_tv_primary():
    moved = ("HRC=F HG=F ALI=F SB=F ZC=F ZS=F ZM=F ZW=F ZL=F HE=F KC=F GC=F THB=X DX-Y.NYB ^TNX BDRY "
             "BZ=F CL=F NG=F HO=F RB=F").split()
    assert all(s in fm.TV_PRIMARY for s in moved)


def test_session_date_shift_matches_yahoo_dates():
    # raw stamps seen 2026-11-05: COMEX Globex open 23:00Z the evening before, CBOT 01:00Z, FX 22:00Z
    assert fm.tv_session_date(1793833200) == "2026-11-05"   # 2026-11-04 23:00Z (COMEX)
    assert fm.tv_session_date(1793840400) == "2026-11-05"   # 2026-11-05 01:00Z (CBOT)
    assert fm.tv_session_date(1793829600) == "2026-11-05"   # 2026-11-04 22:00Z (FX_IDC)
