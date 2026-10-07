"""장중 1분 간격 실시간 감시.

alerts.py 의 RULES 를 현재가로 매분 판정한다. 이평선은 장 시작 전 일봉으로
어제까지의 합을 구해 두고, 오늘 값은 (어제까지 n-1개 종가 + 현재가) / n 으로 계산한다.
새 조건이 충족되면 즉시 GitHub 이슈를 만들고 reports/alerts.json 을 커밋한다.

사용: python screener/watch.py   (WATCH_UNTIL=HH:MM KST 까지, 기본 15:31)
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

from alerts import RULES
from dante import MAS, ROOT, find_code, load_prices

KST = timezone(timedelta(hours=9))
OUT = ROOT / "reports" / "alerts.json"
DEBUG = os.environ.get("WATCH_DEBUG") == "1"  # 시간 제한 없이 한 바퀴 돌며 원시 시세를 출력, 알림·커밋 안 함


def now():
    return datetime.now(KST)


def prepare(name):
    """어제까지 일봉에서 이평선 계산에 필요한 값을 미리 구한다."""
    code = find_code(name)
    df = load_prices(code)
    today = now().date()
    if df.index[-1].date() == today:  # 장중이면 오늘 미완성 봉이 들어 있으므로 뺀다
        df = df.iloc[:-1]
    closes = df["Close"].tolist()
    return {
        "code": code,
        "sums": {n: sum(closes[-(n - 1):]) for n in MAS if len(closes) >= n - 1},
        "vma20": float(df["Volume"].iloc[-20:].mean()),
    }


def quote(code):
    """네이버 실시간 시세, 실패하면 오늘 일봉(FinanceDataReader). 둘 다 실패하면 None."""
    try:
        r = requests.get(f"https://polling.finance.naver.com/api/realtime/domestic/stock/{code}", timeout=5)
        d = r.json()["datas"][0]
        if DEBUG:
            print(json.dumps(d, ensure_ascii=False))
        num = lambda k: float(str(d[k]).replace(",", ""))
        return {"price": num("closePrice"), "low": num("lowPrice"), "volume": num("accumulatedTradingVolume"),
                "status": d.get("marketStatus"), "src": "naver"}
    except Exception as e:
        print(f"{now():%H:%M:%S} naver quote {code} failed: {e!r}", file=sys.stderr)
    try:
        import FinanceDataReader as fdr
        b = fdr.DataReader(code, now().date().isoformat()).iloc[-1]
        return {"price": float(b["Close"]), "low": float(b["Low"]), "volume": float(b["Volume"]),
                "status": None, "src": "fdr"}
    except Exception as e:
        print(f"{now():%H:%M:%S} fdr quote {code} failed: {e!r}", file=sys.stderr)
        return None


def row(base, q):
    t = {"Close": q["price"], "Low": q["low"], "Volume": q["volume"], "VMA20": base["vma20"]}
    for n, s in base["sums"].items():
        t[f"MA{n}"] = (s + q["price"]) / n
    return t


def load_seen(date):
    if OUT.exists():
        old = json.loads(OUT.read_text())
        if old.get("date") == date:
            return old["triggers"]
    return []


def notify(hit):
    title = f"[타점] {hit['date']} {hit['name']} {hit['rule']}"
    body = (f"{hit['time']} 현재가 {hit['price']:,.0f} / 기준선 {hit['level']:,} / 거래량 20일평균 {hit['vol_x']}배\n"
            f"{hit['note']}\n\n장중 신호는 종가 확정 전입니다. 종가까지 기준선 위에서 버티는지 확인하세요.")
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER")
    cmd = ["gh", "issue", "create", "--title", title, "--body", body] + (["--assignee", owner] if owner else [])
    subprocess.run(cmd, check=False)


def commit(date, triggers):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"date": date, "triggers": triggers}, ensure_ascii=False, indent=1))
    if os.environ.get("GITHUB_ACTIONS"):
        sh = lambda *a: subprocess.run(a, check=False)
        sh("git", "add", str(OUT))
        sh("git", "commit", "-m", f"alerts: {now():%F %H:%M}")
        sh("git", "pull", "--rebase")
        sh("git", "push")


def main():
    hh, mm = map(int, os.environ.get("WATCH_UNTIL", "15:31").split(":"))
    until = now().replace(hour=hh, minute=mm, second=0, microsecond=0)
    date = now().date().isoformat()
    bases = {name: prepare(name) for name in RULES}
    triggers = load_seen(date)
    seen = {(x["name"], x["rule"]) for x in triggers}
    print(f"watching {list(RULES)} until {until:%H:%M}, already alerted today: {sorted(seen)}")

    while DEBUG or now() < until:
        for name, rules in RULES.items():
            base = bases[name]
            q = quote(base["code"])
            if not q:
                continue
            t = row(base, q)
            if DEBUG:
                print(name, q, {k: round(v) for k, v in t.items() if k.startswith("MA")},
                      [r for r, fn, *_ in rules if fn(t)])
                continue
            for rule, fn, col, note in rules:
                if (name, rule) in seen or not fn(t):
                    continue
                hit = {"date": date, "time": f"{now():%H:%M}", "name": name, "code": base["code"], "rule": rule,
                       "price": q["price"], "level": round(t[col]), "vol_x": round(q["volume"] / base["vma20"], 1),
                       "note": note}
                print("HIT", json.dumps(hit, ensure_ascii=False))
                seen.add((name, rule))
                triggers.append(hit)
                notify(hit)
                commit(date, triggers)
        if DEBUG:
            return
        time.sleep(max(0, 60 - now().second))
    print(f"done at {now():%H:%M}, alerts today: {len(triggers)}")


if __name__ == "__main__":
    main()
