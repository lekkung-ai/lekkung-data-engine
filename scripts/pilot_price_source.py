import sys
import urllib.request
import json
import time

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

# แหล่ง 3: Yahoo spark API (หลาย symbol ใน 1 req — เทสว่าได้ close ย้อนหลังไหม)
def test_spark_batch(syms):
    joined = ','.join(syms)
    url = f"https://query1.finance.yahoo.com/v7/finance/spark?symbols={joined}&range=3mo&interval=1d"
    try:
        req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
        r = urllib.request.urlopen(req, timeout=20)
        d = json.load(r)
        # spark คืน dict/list ต่อ symbol
        n = len(d.get('spark',{}).get('result',[])) if isinstance(d,dict) and 'spark' in d else (len(d) if isinstance(d,dict) else 'unknown')
        # ดูโครงสร้างตัวแรก
        sample = json.dumps(d, ensure_ascii=False)[:600]
        return f"OK symbols_returned={n} sample={sample}"
    except Exception as e:
        return f"FAIL: {type(e).__name__}: {str(e)[:120]}"

batch = ['SRICHA.BK','PTT.BK','KBANK.BK','DELTA.BK','SCC.BK']
print("Spark batch(5):", test_spark_batch(batch))

# เทส timing: ดึง 20 ตัว sequential ดูใช้เวลาจริงเท่าไหร่ (ประเมิน 689)
syms20 = ['SRICHA','PTT','KBANK','DELTA','SCC','CPALL','AOT','ADVANC','GULF','BDMS','BBL','SCB','KTB','TRUE','MINT','CRC','OSP','CBG','IVL','PTTEP']
t0 = time.time()
ok = 0
for s in syms20:
    if 'OK' in test_yahoo(s + '.BK'): ok += 1
el = time.time() - t0
print(f"Sequential 20 tickers: {ok}/20 OK in {el:.1f}s -> est 689 = {el/20*689/60:.1f} min")
