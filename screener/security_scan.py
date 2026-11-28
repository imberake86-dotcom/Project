"""보안주 단테 기법 점검: 최근 N봉 안에 성립한 기법과 11/27 현재 실패 기준 유지 여부를 로그로."""
import json
import sys
from datetime import datetime, timedelta, timezone

import FinanceDataReader as fdr
import pandas as pd

from bowl import load
from dante_scan import signals

KST = timezone(timedelta(hours=9))
NAMES = sys.argv[2:]
N = int(sys.argv[1])
lst = fdr.StockListing("KRX")
today = datetime.now(KST).date()
out = []
for n in NAMES:
    r = lst[lst["Name"] == n]
    if r.empty:
        print("NOT FOUND", n)
        continue
    code = r["Code"].iloc[0]
    try:
        df = load(code, "pm", today)
        assert len(df) > 30, "short"
    except Exception as e:
        print("FAIL", n, repr(e))
        continue
    i = len(df) - 1
    t = df.iloc[i]
    rec = {"name": n, "code": code, "date": str(df.index[i].date()), "close": int(t["Close"]), "chg": round(float(t["chg"]), 1),
           "ret5": round(float(t["Close"] / df["Close"].iloc[i - 5] * 100 - 100), 1),
           "val20_eok": round(float((df["Close"] * df["Volume"]).iloc[-20:].mean()) / 1e8),
           "ma": {k: (None if pd.isna(t[f"MA{k}"]) else round(float(t[f"MA{k}"]))) for k in [5, 20, 60, 112, 224]},
           "low": int(t["Low"]), "sig": []}
    for j in range(max(230, i - N + 1), i + 1):
        try:
            s, vx = signals(df, j)
        except Exception as e:
            print("SIGFAIL", n, repr(e)); break
        for a, b, c in s:
            rec["sig"].append([str(df.index[j].date()), a, b, c, vx, int(df["Close"].iloc[j]), int(df["Low"].iloc[j])])
    out.append(rec)
    print(json.dumps(rec, ensure_ascii=False))
