import sys
import urllib.request
import json

def test_yahoo(sym):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=2mo"
    try:
        req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
        r = urllib.request.urlopen(req, timeout=15)
        d = json.load(r)
        closes = d['chart']['result'][0]['indicators']['quote'][0]['close']
        valid = [c for c in closes if c is not None]
        return f"OK {len(valid)} bars, last={valid[-1] if valid else 'none'}"
    except Exception as e:
        return f"FAIL: {type(e).__name__}: {str(e)[:100]}"

for sym in ['SRICHA.BK', 'PTT.BK']:
    print(f"Yahoo {sym}: {test_yahoo(sym)}")

# แหล่ง 2: SETTrade historical (เผื่อ Yahoo บล็อก CI)
def test_settrade(sym):
    url = f"https://www.settrade.com/api/set/stock/{sym}/historical-trading?period=6M"
    try:
        req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0','Accept':'application/json'})
        r = urllib.request.urlopen(req, timeout=15)
        d = json.load(r)
        return f"OK type={type(d).__name__} keys={list(d.keys())[:5] if isinstance(d,dict) else len(d)}"
    except Exception as e:
        return f"FAIL: {type(e).__name__}: {str(e)[:100]}"

for sym in ['SRICHA', 'PTT']:
    print(f"SETTrade {sym}: {test_settrade(sym)}")
