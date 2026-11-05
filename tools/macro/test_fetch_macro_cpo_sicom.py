"""Offline tests: CPO (ringgit, TradingView only) and SICOM (pick the live 1!/2! contract) in fetch_macro.

python -m pytest tools/macro/test_fetch_macro_cpo_sicom.py
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch_macro as fm  # noqa: E402
from macro_mapping import COMMODITIES  # noqa: E402


def _ts(day: str, hhmm: str) -> int:
    return int(datetime.strptime(f"{day} {hhmm}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp())


def _points(n: int, start: str = "2026-06-01", base: float = 4000.0):
    d0 = datetime.strptime(start, "%Y-%m-%d")
    return [((d0 + timedelta(days=i)).strftime("%Y-%m-%d"), base + i) for i in range(n)]


def _data(date: str, close: float = 250.0):
    return {"latest": {"date": date, "close": close}, "pct_1d": 0.1, "pct_1m": None,
            "series": [{"date": date, "close": close}]}


# ─── CPO config / unit / range ────────────────────────────────────────────────

def test_cpo_unit_is_ringgit():
    assert COMMODITIES["CPO=F"]["unit"].startswith("MYR/")


def test_cpo_config():
    assert fm.TV_PRIMARY["CPO=F"] == "MYX:FCPO1!"
    assert "CPO=F" in fm.TV_ONLY
    assert fm.VALID_CLOSE_RANGE["CPO=F"] == (2000.0, 8000.0)
    assert fm.TV_SESSION_SHIFT_OVERRIDE["CPO=F"] == timedelta(hours=12)


def test_valid_range_rejects_usd_points_in_latest_or_series():
    good = {"latest": {"close": 4500.0}, "series": [{"close": 4400.0}, {"close": 4600.0}]}
    usd_latest = {"latest": {"close": 1151.5}, "series": [{"close": 4400.0}]}
    usd_in_series = {"latest": {"close": 4500.0}, "series": [{"close": 4400.0}, {"close": 1150.0}]}
    empty = {"latest": {"close": None}, "series": []}
    assert fm.data_in_valid_range("CPO=F", good)
    assert not fm.data_in_valid_range("CPO=F", usd_latest)
    assert not fm.data_in_valid_range("CPO=F", usd_in_series)
    assert not fm.data_in_valid_range("CPO=F", empty)
    assert fm.data_in_valid_range("ZC=F", usd_latest)  # symbols without a range are untouched


def test_myx_night_session_belongs_to_the_next_trading_day_and_unfinished_one_is_dropped():
    shift = fm.TV_SESSION_SHIFT_OVERRIDE["CPO=F"]
    bars = [
        [_ts("2026-11-02", "02:00"), 0, 0, 0, 4649.0, 1],   # day-session open -> 2 Nov
        [_ts("2026-11-02", "13:00"), 0, 0, 0, 4551.0, 1],   # night session of 2 Nov -> trading day 3 Nov
        [_ts("2026-11-04", "13:00"), 0, 0, 0, 4377.0, 1],   # -> 5 Nov
        [_ts("2026-11-05", "13:00"), 0, 0, 0, 4400.0, 1],   # -> 6 Nov (still running)
    ]
    assert fm.bars_to_points(bars, shift, today="2026-11-05") == [
        ("2026-11-02", 4649.0), ("2026-11-03", 4551.0), ("2026-11-05", 4377.0)]
    assert fm.bars_to_points(bars, shift, today="2026-11-06")[-1] == ("2026-11-06", 4400.0)


def test_other_symbols_keep_the_6h_shift():
    assert fm.tv_session_date(_ts("2026-11-04", "23:00")) == "2026-11-05"  # COMEX, default shift


def _tv_rows(points):
    """[(date, close)] → TradingView rows stamped 13:00Z on the evening before (MYX night-session stamp)."""
    out = []
    for d, c in points:
        prev = (datetime.strptime(d, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        out.append({"v": [_ts(prev, "13:00"), c, c, c, c, 1]})
    return out


def test_fetch_tv_websocket_rejects_a_usd_priced_cpo(monkeypatch):
    async def fake(sym, n):
        return _tv_rows(_points(30, "2026-09-01", base=1100.0))   # looks like Yahoo's USD price
    monkeypatch.setattr(fm, "_tv_fetch_async", fake)
    monkeypatch.setattr(fm, "TV_WS_ATTEMPTS", 1)
    assert fm.fetch_tv_websocket("CPO=F") is None


def test_fetch_tv_websocket_accepts_ringgit_cpo(monkeypatch):
    async def fake(sym, n):
        return _tv_rows(_points(30, "2026-09-01", base=4400.0))
    monkeypatch.setattr(fm, "_tv_fetch_async", fake)
    data = fm.fetch_tv_websocket("CPO=F")
    assert data and data["latest"]["close"] == 4429.0 and len(data["series"]) == 30


def test_cpo_never_falls_back_to_yahoo_or_tradingview_ta(monkeypatch):
    monkeypatch.setattr(fm, "fetch_tv_websocket", lambda s, t=None: None)

    def boom(*a, **k):
        raise AssertionError("CPO must not use Yahoo / tradingview_ta")
    monkeypatch.setattr(fm, "fetch_yahoo", boom)
    monkeypatch.setattr(fm, "fetch_tradingview", boom)
    assert fm.fetch_symbol("CPO=F") == (None, None, None)


def test_other_tv_primary_symbols_still_fall_back_to_yahoo(monkeypatch):
    monkeypatch.setattr(fm, "fetch_tv_websocket", lambda s, t=None: None)
    monkeypatch.setattr(fm, "fetch_yahoo", lambda s: _data("2026-11-04", 6.3))
    assert fm.fetch_symbol("HG=F") == (_data("2026-11-04", 6.3), fm.SRC_YAHOO, "HG=F")


# ─── SICOM: 1! vs 2! ──────────────────────────────────────────────────────────

def test_pick_fresher_contract():
    assert fm.pick_fresher_contract([("A1", _data("2026-10-29")), ("A2", _data("2026-11-05"))])[0] == "A2"   # 2! newer
    assert fm.pick_fresher_contract([("A1", _data("2026-11-05")), ("A2", _data("2026-11-05"))])[0] == "A1"   # same day -> 1!
    assert fm.pick_fresher_contract([("A1", _data("2026-11-05")), ("A2", _data("2026-11-04"))])[0] == "A1"   # 1! newer
    assert fm.pick_fresher_contract([("A1", None), ("A2", _data("2026-11-04"))])[0] == "A2"                  # 1! failed
    assert fm.pick_fresher_contract([("A1", _data("2026-11-04")), ("A2", None)])[0] == "A1"                  # 2! failed
    assert fm.pick_fresher_contract([("A1", None), ("A2", None)]) is None                                    # both failed


def test_sicom_picks_the_live_contract_and_records_it(monkeypatch, capsys):
    calls = []

    def fake(symbol, tv_sym=None):
        calls.append(tv_sym)
        return _data("2026-10-29") if tv_sym.endswith("1!") else _data("2026-11-05", 239.5)
    monkeypatch.setattr(fm, "fetch_tv_websocket", fake)
    data, source, source_symbol = fm.fetch_symbol("SICOM_TSR20")
    assert calls == ["SGX:TF1!", "SGX:TF2!"]
    assert (source, source_symbol, data["latest"]["date"]) == (fm.SRC_TV_WS, "SGX:TF2!", "2026-11-05")
    assert "using SGX:TF2!" in capsys.readouterr().out          # the switch is logged


def test_sicom_same_day_prefers_front_contract(monkeypatch):
    monkeypatch.setattr(fm, "fetch_tv_websocket", lambda s, t=None: _data("2026-11-05"))
    assert fm.fetch_symbol("SICOM_RSS3")[2] == "SGX:RT1!"


def test_sicom_websocket_failure_falls_back_to_the_scanner(monkeypatch):
    monkeypatch.setattr(fm, "fetch_tv_websocket", lambda s, t=None: None)
    monkeypatch.setattr(fm, "fetch_direct_tradingview", lambda s: _data("2026-10-30"))
    data, source, source_symbol = fm.fetch_symbol("SICOM_RSS3")
    assert (source, source_symbol) == (fm.SRC_TV_SCANNER, "SGX:RT1!")


def test_shfe_still_uses_the_scanner(monkeypatch):
    monkeypatch.setattr(fm, "fetch_tv_websocket", lambda s, t=None: (_ for _ in ()).throw(AssertionError("no WS for SHFE")))
    monkeypatch.setattr(fm, "fetch_direct_tradingview", lambda s: _data("2026-11-05", 18940.0))
    assert fm.fetch_symbol("SHFE_RU")[1:] == (fm.SRC_TV_SCANNER, "SHFE:RU1!")


# ─── main(): merge-on-write must neither mix USD points into a ringgit series nor resurrect them ───

USD_LAST_GOOD = {"commodities": {"CPO=F": {
    "latest": {"date": "2026-10-23", "close": 1151.5}, "pct_1d": 0.1, "pct_1m": 1.0,
    "series": [{"date": f"2026-10-{d:02d}", "close": 1150.0 + d} for d in range(1, 24)],
    "last_success_date": "2026-10-23", "never_fetched": False, "source": "Yahoo Finance", "source_symbol": "CPO=F"}}}


def _run_main(monkeypatch, tmp_path, cpo_fetch, last_good):
    meta = {"name_th": "x", "name_en": "x", "unit": "u", "zone": "agri", "tickers": [], "ticker_impacts": {}}
    monkeypatch.setattr(fm, "COMMODITIES", {"CPO=F": dict(meta), "ZC=F": dict(meta)})
    monkeypatch.setattr(fm, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.setattr(fm, "FETCH_SLEEP_SEC", 0)
    zc = fm.build_result(_points(40, "2026-09-01", base=440.0))

    def fake(symbol):
        return cpo_fetch() if symbol == "CPO=F" else (dict(zc), fm.SRC_TV_WS, "CBOT:ZC1!")
    monkeypatch.setattr(fm, "fetch_symbol", fake)
    sd = tmp_path / "stockdesk_macro.json"
    sd.write_text(json.dumps(last_good), encoding="utf-8")
    monkeypatch.setenv("STOCKDESK_MACRO_JSON", str(sd))
    fm.main()
    return json.loads((tmp_path / "out" / "macro_commodities.json").read_text(encoding="utf-8"))["commodities"]["CPO=F"]


def test_main_replaces_the_whole_cpo_series_and_keeps_no_usd_point(monkeypatch, tmp_path):
    ringgit = fm.build_result(_points(180, "2026-05-01", base=4000.0))
    out = _run_main(monkeypatch, tmp_path, lambda: (dict(ringgit), fm.SRC_TV_WS, "MYX:FCPO1!"), USD_LAST_GOOD)
    assert out["series"] == ringgit["series"] and len(out["series"]) == 180
    assert all(2000 <= p["close"] <= 8000 for p in out["series"])
    assert (out["source"], out["source_symbol"]) == (fm.SRC_TV_WS, "MYX:FCPO1!")
    assert "is_stale_fallback" not in out


def test_main_failed_cpo_fetch_never_resurrects_the_usd_last_good(monkeypatch, tmp_path):
    out = _run_main(monkeypatch, tmp_path, lambda: (None, None, None), USD_LAST_GOOD)
    assert out["never_fetched"] is True and out["latest"]["close"] is None and out["series"] == []


def test_main_failed_cpo_fetch_keeps_a_ringgit_last_good(monkeypatch, tmp_path):
    good = {"commodities": {"CPO=F": {
        "latest": {"date": "2026-11-04", "close": 4377.0}, "pct_1d": 0.0, "pct_1m": 0.0,
        "series": [{"date": "2026-11-03", "close": 4496.0}, {"date": "2026-11-04", "close": 4377.0}],
        "last_success_date": "2026-11-04", "never_fetched": False, "source": fm.SRC_TV_WS, "source_symbol": "MYX:FCPO1!"}}}
    out = _run_main(monkeypatch, tmp_path, lambda: (None, None, None), good)
    assert out["is_stale_fallback"] is True and out["latest"]["close"] == 4377.0
    assert [p["close"] for p in out["series"]] == [4496.0, 4377.0]
