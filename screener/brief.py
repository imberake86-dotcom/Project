"""관심종목 아침·장마감 타점 브리핑.

screener/watchlist.txt 의 종목을 확정 일봉으로 판정해 단테 타점(진입/손절/1차 목표)만 뽑는다.
- am (장 시작 전): 오늘 날짜 봉(넥스트레이드 프리마켓)은 버리고 어제 정규장 종가까지로 판정
- pm (15:30 장 마감 뒤): 오늘 봉을 KRX 정규장 OHLCV 로 덮어쓰고 판정
판정식:
  기준봉 지지 (R04)  최근 10봉 안의 기준봉(+7%↑, 거래량 20일평균 3배↑) 뒤 종가가 시가를 지키며 시가 근처로 눌림
  224 회복 눌림      10봉 안에 종가가 224선을 회복했고 지분(224선 위 면적) 60%↑, 224선 +6% 안쪽
  B / B'             아침 스캔과 같은 5·20·60, 5·112·224 전환 (교차 3봉 이내)
  224 돌파 대기      112선 위 지지(20일 중 10일↑), 224선 5% 아래까지 접근
목표는 가까운 저항(전고점·이평선·기준봉 뒤 봉들의 고가, 2% 안쪽은 한 구간으로 묶음) 바로 아래.
최근 10봉 종가가 이미 넘어선 가격은 소화된 매물로 보고 저항에서 뺀다.
손익비 1.5 미만이면 저항 구간 돌파를 진입 조건으로 바꿔 다시 계산하고, 그래도 안 되면 버린다.

출력: reports/brief/latest.json  ({"mode", "asof", "message", "picks", "skipped"})
사용: python screener/brief.py am|pm
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import FinanceDataReader as fdr
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
KST = timezone(timedelta(hours=9))
MIN_VALUE_20D = 3e8   # 20일 평균 거래대금 하한 (원일특강처럼 하루 수천 주면 타점이 의미 없음)
MIN_RR = 1.5


def tick(p):
    for lim, t in [(2000, 1), (5000, 5), (20000, 10), (50000, 50), (200000, 100), (500000, 500)]:
        if p < lim:
            return t
    return 1000


def floor_t(p):
    t = tick(p)
    return int(p // t * t)


def ceil_t(p):
    t = tick(p)
    return int(-(-p // t) * t)


def watchlist():
    lines = (ROOT / "screener" / "watchlist.txt").read_text(encoding="utf-8").splitlines()
    return [x.split("#")[0].strip() for x in lines if x.split("#")[0].strip()]


def load(code, row, mode, today):
    df = fdr.DataReader(code, (today - timedelta(days=760)).isoformat())
    df = df[df["Volume"] > 0].copy()
    if mode == "am":
        df = df[df.index.date < today]
    elif df.index[-1].date() == today and row is not None:
        for k in ["Open", "High", "Low", "Close", "Volume"]:  # 넥스트레이드 체결이 섞이지 않게 정규장 값으로
            v = pd.to_numeric(str(row.get(k, "")).replace(",", ""), errors="coerce")
            if pd.notna(v) and v > 0:
                df.iloc[-1, df.columns.get_loc(k)] = v
    for n in [5, 20, 60, 112, 224]:
        df[f"MA{n}"] = df["Close"].rolling(n).mean()
    df["VMA20"] = df["Volume"].rolling(20).mean()
    df["chg"] = df["Close"].pct_change() * 100
    return df


def zones(levels, px):
    """현재가 위 저항 가격들을 2% 안쪽끼리 묶어 [(하단, 상단)] 으로."""
    out = []
    for v in sorted(x for x in levels if x > px):
        if out and v <= out[-1][1] * 1.02:
            out[-1][1] = v
        else:
            out.append([v, v])
    return out


def resistances(df, since=None):
    t = df.iloc[-1]
    w = df.iloc[-240:-1]
    hi = w["High"]
    piv = hi[hi == hi.rolling(11, center=True, min_periods=1).max()].tolist()
    lv = piv + [t[f"MA{n}"] for n in [20, 60, 112, 224] if pd.notna(t[f"MA{n}"])]
    if since is not None:
        lv += df["High"].iloc[since:].tolist()
    consumed = df["Close"].iloc[-10:].max()
    return [float(x) for x in lv if x > consumed]


def plan(kind, why, entry, stop, res):
    """진입가·손절가와 저항 목록으로 손익비 맞는 계획을 만든다. 안 되면 None."""
    if stop >= entry:
        return None
    zs = zones(res, entry * 1.01)
    if not zs:
        return None
    target = floor_t(zs[0][0] * 0.995)
    if target > entry and (target - entry) / (entry - stop) >= MIN_RR:
        return {"type": kind, "why": why, "entry": int(entry), "stop": int(stop), "target": int(target),
                "rr": round((target - entry) / (entry - stop), 1), "cond": None}
    if len(zs) > 1:
        trig = floor_t(zs[0][1]) + tick(zs[0][1])
        target = floor_t(zs[1][0] * 0.995)
        if target > trig and (target - trig) / (trig - stop) >= MIN_RR:
            return {"type": kind, "why": why, "entry": int(trig), "stop": int(stop), "target": int(target),
                    "rr": round((target - trig) / (trig - stop), 1), "cond": "돌파"}
    return None


def analyze(df):
    t, n = df.iloc[-1], len(df)
    px = t["Close"]
    plans = []
    has224 = pd.notna(t["MA224"])

    # 기준봉 지지 (R04). 오늘 막 생긴 기준봉이면 시가 근처까지 눌릴 때를 기다린다
    for i in range(n - 1, n - 11, -1):
        b = df.iloc[i]
        if b["chg"] >= 7 and b["Volume"] >= 3 * df["VMA20"].iloc[i - 1]:
            s = b["Open"]
            if i == n - 1:
                entry = floor_t(s * 1.02)
                p = plan("기준봉 눌림 대기", f"{df.index[i]:%m/%d} 기준봉(거래량 {b['Volume'] / df['VMA20'].iloc[i - 1]:.0f}배) 시가 {s:,.0f}",
                         entry, floor_t(s) - tick(s), resistances(df, since=i) + [float(b["High"])])
                if p and entry < px:
                    p["cond"] = "눌림"
                    plans.append(p)
                break
            after = df.iloc[i + 1:]
            if (after["Close"] >= s).all() and px <= s * 1.06:
                entry = floor_t(min(px, s * 1.015))
                stop = floor_t(min(s, after["Low"].min())) - tick(s)
                why = f"{df.index[i]:%m/%d} 기준봉 시가 {s:,.0f} 지지"
                plans.append(plan("기준봉 지지", why, entry, stop, resistances(df, since=i)))
            break

    # 224 회복 눌림 (지분)
    if has224:
        w = df.iloc[-224:]
        diff = (w["Close"] - w["MA224"]) / w["MA224"]
        buy, sell = diff.clip(lower=0).sum(), (-diff).clip(lower=0).sum()
        share = buy / (buy + sell) * 100 if buy + sell else 50
        above = df["Close"] > df["MA224"]
        rec = bool((above & ~above.shift(1, fill_value=True)).iloc[-10:].any())
        if rec and share >= 60 and 0 < px / t["MA224"] - 1 <= 0.06:
            entry = floor_t(min(px, t["MA224"] * 1.025))
            stop = floor_t(t["MA224"]) - tick(t["MA224"])
            plans.append(plan("224 회복 눌림", f"224선 {t['MA224']:,.0f} 위, 지분 {share:.0f}%", entry, stop, resistances(df)))

    # B / B' (5·20·60, 5·112·224)
    def crossed(a, b):
        up = (df[a] > df[b]) & (df[a].shift(1) <= df[b].shift(1))
        return bool(up.iloc[-3:].any())
    if crossed("MA5", "MA20") and px > t["MA5"] and t["MA20"] < px < t["MA60"] and t["MA20"] < t["MA60"]:
        stop = floor_t(t["MA20"]) - tick(t["MA20"])
        plans.append(plan("5·20·60", "5/20 골든크로스, 20선 위·60선 아래", floor_t(px), stop, resistances(df)))
    if has224 and crossed("MA5", "MA112") and px > t["MA5"] and t["MA112"] < px < t["MA224"] and t["MA112"] < t["MA224"]:
        stop = floor_t(t["MA112"]) - tick(t["MA112"])
        plans.append(plan("5·112·224", "5/112 골든크로스, 112선 위·224선 아래", floor_t(px), stop, resistances(df)))

    # 224 돌파 대기
    if has224:
        on112 = int((df["Close"].iloc[-20:] > df["MA112"].iloc[-20:]).sum())
        if on112 >= 10 and t["MA112"] < px < t["MA224"] and px >= t["MA224"] * 0.95:
            trig = ceil_t(t["MA224"])
            if trig == t["MA224"]:
                trig += tick(trig)
            stop = floor_t(max(df["Low"].iloc[-3:].min(), t["MA224"] * 0.98)) - tick(t["MA224"])
            p = plan("224 돌파 대기", f"112선 위 {on112}/20일, 224선 {t['MA224']:,.0f} 바로 아래", trig, stop,
                     [x for x in resistances(df) if x > trig * 1.01])
            if p:
                p["cond"] = "돌파"
            plans.append(p)

    plans = [p for p in plans if p]
    if not plans:
        return None
    # 바로 진입 가능한 것, 그다음 손익비 순
    plans.sort(key=lambda p: (p["cond"] is not None, -p["rr"]))
    return plans[0]


def market_picks(asof):
    f = ROOT / "reports" / "morning" / f"{asof}.json"
    if not f.exists():
        return []
    hits = json.loads(f.read_text(encoding="utf-8"))["hits"]
    rows = [(h, max(h["plans"], key=lambda p: p["rr"])) for h in hits if h.get("val20_eok", 0) >= 10]
    rows = [(h, p) for h, p in rows if p["rr"] >= MIN_RR]
    rows.sort(key=lambda x: -x[1]["rr"])
    return [{"name": h["name"], "close": h["close"], "type": p["type"], "entry": p["entry"], "stop": p["stop"],
             "target": p["target"], "rr": p["rr"]} for h, p in rows[:5]]


def fmt(n):
    return f"{int(n):,}"


def main(mode):
    now = datetime.now(KST)
    today = now.date()
    listing = fdr.StockListing("KRX").set_index("Name")
    picks, skipped, asof = [], [], None
    for name in watchlist():
        if name not in listing.index:
            skipped.append(f"{name}: 종목명 못 찾음")
            continue
        row = listing.loc[name]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        try:
            df = load(row["Code"], row, mode, today)
        except Exception as e:
            skipped.append(f"{name}: 시세 오류 {e!r}")
            continue
        if len(df) < 120:
            continue
        asof = max(asof or "", str(df.index[-1].date()))
        val20 = float((df["Close"] * df["Volume"]).iloc[-20:].mean())
        if val20 < MIN_VALUE_20D:
            skipped.append(f"{name}: 20일 평균 거래대금 {val20 / 1e8:.1f}억")
            continue
        p = analyze(df)
        if p:
            p.update({"name": name, "close": int(df["Close"].iloc[-1]),
                      "chg": round(float(df["chg"].iloc[-1]), 1)})
            picks.append(p)

    label = {"am": "아침", "pm": "장마감"}[mode]
    d = pd.Timestamp(asof).strftime("%m/%d") if asof else ""
    lines = [f"[{label}] 관심종목 타점 ({d} 종가 기준)"]
    how = {None: "진입 {}", "눌림": "{} 이하 눌림 시", "돌파": "{} 돌파 시"}
    for title, conds in [(None, [None]), ("조건 충족 시", ["눌림", "돌파"])]:
        group = [p for p in picks if p["cond"] in conds]
        if group and title:
            lines.append(title)
        for p in group:
            lines.append(f"• {p['name']} {fmt(p['close'])}: {p['why']}. {how[p['cond']].format(fmt(p['entry']))}"
                         f" / 손절 {fmt(p['stop'])} / 목표 {fmt(p['target'])}")
    if not picks:
        lines.append("오늘은 관심종목에 타점이 없습니다.")
    mk = market_picks(asof) if asof else []
    if mk:
        lines.append("관심종목 밖에서 찾은 종목")
        for m in mk:
            lines.append(f"• {m['name']} {fmt(m['close'])}: {m['type']}. 진입 {fmt(m['entry'])} / 손절 {fmt(m['stop'])} / 목표 {fmt(m['target'])}")

    out = ROOT / "reports" / "brief"
    out.mkdir(parents=True, exist_ok=True)
    res = {"mode": mode, "asof": asof, "generated": now.isoformat(timespec="minutes"), "message": "\n".join(lines),
           "picks": picks, "market": mk, "skipped": skipped}
    (out / "latest.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print(res["message"])
    if skipped:
        print("skipped:", skipped, file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "pm")
