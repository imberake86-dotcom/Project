"""미국 종목 즉석 확인: 야후 일봉(2년)·이평과 당일 5분봉(프리/애프터 포함)을 로그로 찍는다."""
import sys
from datetime import datetime, timezone, timedelta

import pandas as pd
import requests

H = {"User-Agent": "Mozilla/5.0"}
ET = timezone(timedelta(hours=-4))


def chart(sym, **params):
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}", params=params, headers=H, timeout=20)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame(q, index=pd.to_datetime(res["timestamp"], unit="s", utc=True).tz_convert(ET))
    return res["meta"], df.dropna(subset=["close"])


for sym in sys.argv[1:]:
    meta, d = chart(sym, range="2y", interval="1d")
    for n in (5, 20, 60, 112, 224, 256):
        d[f"ma{n}"] = d["close"].rolling(n).mean()
    print("=" * 20, sym, {k: meta.get(k) for k in ("regularMarketPrice", "regularMarketTime", "chartPreviousClose", "regularMarketDayHigh", "regularMarketDayLow", "regularMarketVolume")})
    print("regularMarketTime ET:", datetime.fromtimestamp(meta["regularMarketTime"], ET))
    pd.set_option("display.width", 250)
    print(d.tail(30).round(4).to_string())
    print("52w high/low:", d["high"].tail(252).max(), d["low"].tail(252).min(), "avg vol 20:", d["volume"].tail(21).iloc[:-1].mean())
    _, m = chart(sym, range="1d", interval="5m", includePrePost="true")
    print(m.round(4).to_string())
