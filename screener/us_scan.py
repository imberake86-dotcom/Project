"""미국 감시 종목(us_watchlist.txt)에 단테 기법이 새로 성립했는지 판정. 판정식은 dante_scan.signals 그대로.

시세는 야후 일봉. 정규장 마감(16:00 ET) 전에 돌면 미완성 당일 봉은 빼고 직전 확정 봉으로 판정한다.
출력: reports/us/latest.json ({"asof", "generated", "message", "hits"}); 새 기법이 없으면 message 는 빈 문자열.
사용: python screener/us_scan.py
     python screener/us_scan.py backtest 일수 티커...
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from bowl import prepare
from dante_scan import fmt_hit, signals

ROOT = Path(__file__).resolve().parent.parent
ET = ZoneInfo("America/New_York")
KST = timezone(timedelta(hours=9))


def px(v):
    return f"${v:,.4f}".rstrip("0").rstrip(".") if v < 10 else f"${v:,.2f}"


def watchlist():
    lines = (ROOT / "screener" / "us_watchlist.txt").read_text(encoding="utf-8").splitlines()
    return [x.split("#")[0].strip() for x in lines if x.split("#")[0].strip()]


def load(sym):
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                     params={"range": "3y", "interval": "1d"}, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    idx = pd.to_datetime(res["timestamp"], unit="s", utc=True).tz_convert(ET).normalize().tz_localize(None)
    df = pd.DataFrame({"Open": q["open"], "High": q["high"], "Low": q["low"], "Close": q["close"], "Volume": q["volume"]},
                      index=idx).dropna()
    now = datetime.now(ET)
    if df.index[-1].date() == now.date() and (now.hour, now.minute) < (16, 15):
        df = df.iloc[:-1]
    return prepare(df)


def main():
    hits, asof = [], None
    for sym in watchlist():
        try:
            df = load(sym)
        except Exception as e:
            print(f"{sym} failed: {e!r}", file=sys.stderr)
            continue
        i = len(df) - 1
        asof = max(asof or "", str(df.index[i].date()))
        sig, vol_x = signals(df, i, px)
        print(sym, df.index[i].date(), round(df["Close"].iloc[i], 4), sig or "-")
        if sig:
            hits.append({"name": sym, "close": float(df["Close"].iloc[i]), "chg": round(float(df["chg"].iloc[i]), 1),
                         "vol_x": vol_x, "sig": sig})
    msg = ""
    if hits:
        lines = [f"[미국] 단테 기법 새로 성립 ({pd.Timestamp(asof):%m/%d} 정규장 종가 기준)"]
        lines += [fmt_hit(h).replace(f"{h['close']:,}", px(h["close"]), 1) for h in hits]
        msg = "\n".join(lines)
    out = ROOT / "reports" / "us"
    out.mkdir(parents=True, exist_ok=True)
    data = {"asof": asof, "generated": datetime.now(KST).isoformat(timespec="minutes"), "message": msg, "hits": hits}
    (out / "latest.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(msg or "새로 성립한 기법 없음")


def backtest(days, syms):
    for sym in syms:
        df = load(sym)
        for i in range(len(df) - days, len(df)):
            sig, vol_x = signals(df, i, px)
            if sig:
                print(f"{sym} {df.index[i].date()} {px(df['Close'].iloc[i])} ({df['chg'].iloc[i]:+.1f}%, {vol_x}배): "
                      + " / ".join(f"{a} - {b} (실패: {c})" for a, b, c in sig))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "backtest":
        backtest(int(sys.argv[2]), sys.argv[3:])
    else:
        main()
