"""아침 종목 추천용 전 종목 스캔 (코스피·코스닥).

카이로스 검색식 A(5일선 회복)·B(5·20·60 전환)·B'(5·112·224 전환)을 일봉으로 재현하고,
손절선·가까운 저항·손익비로 거른다. 규칙 근거: 주식단테_최근300쇼츠_규칙정리.md, reports 의 10/7 타점 보고서.
마지막 봉은 KRX 상장목록(정규장 종가)의 OHLCV로 덮어써 넥스트레이드 체결이 섞이지 않게 한다.
출력: reports/morning/<날짜>.json, .md
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import FinanceDataReader as fdr
import pandas as pd

from brief import naver_regular

ROOT = Path(__file__).resolve().parent.parent
MIN_VALUE_20D = 10e8   # F1 20일 평균 거래대금 하한 (예시값, 단테 고정값 없음)
MAX_CHG = 15.0         # F2 당일 등락률 상한 (추격 회피 R11)
CROSS_WITHIN = 3       # 교차 유효기간(거래일)
_NOW = datetime.now(timezone(timedelta(hours=9)))
TODAY = _NOW.date()
PREOPEN = _NOW.hour < 9


def tick(p):
    for lim, t in [(2000, 1), (5000, 5), (20000, 10), (50000, 50), (200000, 100), (500000, 500)]:
        if p < lim:
            return t
    return 1000


def floor_t(p):
    t = tick(p)
    return int(p // t * t)


def universe():
    df = fdr.StockListing("KRX")
    df = df[df["Market"].isin(["KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"])]
    df = df[df["Code"].str.endswith("0")]
    df = df[~df["Name"].str.contains("스팩|리츠")]
    if "Dept" in df:
        df = df[~df["Dept"].fillna("").str.contains("관리|투자주의환기")]
    df = df[df["Amount"] >= 3e8]
    return df


def swing_highs_above(df, px, lookback=240, k=5):
    w = df.iloc[-lookback:-1]
    hi = w["High"]
    piv = hi[hi == hi.rolling(2 * k + 1, center=True, min_periods=1).max()]
    return sorted(v for v in piv if v > px * 1.01)


def crossed(a, b, within):
    up = (a > b) & (a.shift(1) <= b.shift(1))
    idx = [i for i in range(len(a) - within, len(a)) if up.iloc[i]]
    return idx[-1] if idx else None


def analyze(row, start):
    code, name = row["Code"], row["Name"]
    try:
        df = fdr.DataReader(code, start)
    except Exception:
        return None
    df = df[df["Volume"] > 0]
    if len(df) < 240:
        return None
    if PREOPEN:  # 장 시작 전에는 넥스트레이드 프리마켓으로 생긴 오늘 봉을 버리고 어제 봉 그대로 쓴다
        df = df[df.index.date < TODAY]
    asof = df.index[-1].date()
    # 정규장 값으로 마지막 봉 교정 (KRX 상장목록은 장 마감 직후 갱신이 늦어 네이버 실시간 정규장 시세를 쓴다)
    day, q = naver_regular(code)
    if q and q["Close"] > 0 and day == asof:
        for k, v in q.items():
            if v > 0:
                df.iloc[-1, df.columns.get_loc(k)] = v
    c = df["Close"]
    for n in [5, 20, 60, 112, 224]:
        df[f"MA{n}"] = c.rolling(n).mean()
    t, p = df.iloc[-1], df.iloc[-2]
    px = t["Close"]
    chg = (px / p["Close"] - 1) * 100
    val20 = (df["Close"] * df["Volume"]).iloc[-21:-1].mean()
    if val20 < MIN_VALUE_20D or chg > MAX_CHG:
        return None
    vmean = df["Volume"].iloc[-21:-1].mean()
    hits = []
    # A. 5일선 회복형
    below5 = (df["Close"].iloc[-6:-1] < df["MA5"].iloc[-6:-1]).all()
    a1 = below5 and px > t["MA5"] and t["Volume"] > p["Volume"] and px > t["Open"]
    a2 = p["Close"] < p["MA5"] and t["Open"] > t["MA5"] and t["Open"] > p["High"]
    a3 = t["MA5"] > p["MA5"]
    if (a1 or a2) and a3:
        hits.append("A")
    # B. 5·20·60
    i = crossed(df["MA5"], df["MA20"], CROSS_WITHIN)
    if i is not None and px > t["MA5"] and px > t["MA20"] and px < t["MA60"] and t["MA20"] < t["MA60"]:
        hits.append("B")
    # B'. 5·112·224
    j = crossed(df["MA5"], df["MA112"], CROSS_WITHIN)
    if j is not None and pd.notna(t["MA224"]) and px > t["MA5"] and px > t["MA112"] and px < t["MA224"] \
            and t["MA112"] < t["MA224"]:
        hits.append("B2")
    if not hits:
        return None

    entry = px
    plans = []
    highs = swing_highs_above(df, entry)
    for h in hits:
        if h == "A":
            stop_ref, stop_why = min(t["Low"], t["Open"]), "당일 시가·저가 이탈(R02 실패 기준)"
            ma_res = [t[f"MA{n}"] for n in [20, 60, 112, 224] if t[f"MA{n}"] > entry * 1.01]
        elif h == "B":
            stop_ref, stop_why = t["MA20"], "20일선 이탈(R03)"
            ma_res = [t["MA60"]] + [t[f"MA{n}"] for n in [112, 224] if t[f"MA{n}"] > entry * 1.01]
        else:
            stop_ref, stop_why = t["MA112"], "112일선 이탈(R03 변형)"
            ma_res = [t["MA224"]]
        stop = floor_t(stop_ref) - tick(stop_ref)
        res = sorted([x for x in ma_res + highs if x > entry * 1.01])
        if not res or stop >= entry:
            continue
        target = floor_t(res[0] * 0.995)  # 가까운 저항 바로 아래 (#113)
        risk = entry - stop
        rr = (target - entry) / risk
        plans.append({"type": h, "entry": int(entry), "stop": int(stop), "stop_why": stop_why,
                      "target": int(target), "resistance": round(float(res[0])), "rr": round(rr, 2),
                      "stop_pct": round(risk / entry * 100, 2), "target_pct": round((target / entry - 1) * 100, 2)})
    if not plans:
        return None
    rng = ((df["High"] - df["Low"]).iloc[-21:-1]).mean()
    return {"code": code, "name": name, "market": row["Market"], "asof": str(asof), "close": int(px),
            "chg": round(chg, 2), "body": round((px / t["Open"] - 1) * 100, 2),
            "upper_wick_pct": round((t["High"] / max(px, t["Open"]) - 1) * 100, 2),
            "vol_x": round(t["Volume"] / vmean, 2) if vmean else None,
            "val20_eok": round(val20 / 1e8, 1), "atr20": round(float(rng)), "gap": round((t["Open"] / p["Close"] - 1) * 100, 2),
            "ma": {n: round(float(t[f"MA{n}"])) for n in [5, 20, 60, 112, 224]},
            "plans": plans, "last5": [[str(d.date())[5:], int(b.Open), int(b.High), int(b.Low), int(b.Close), int(b.Volume)]
                                      for d, b in df.iloc[-5:].iterrows()]}


def main():
    uni = universe()
    start = (date.today() - timedelta(days=560)).isoformat()
    print(f"universe {len(uni)}", file=sys.stderr)
    with ThreadPoolExecutor(12) as ex:
        out = [r for r in ex.map(lambda rw: analyze(rw, start), [r for _, r in uni.iterrows()]) if r]
    asof = max((r["asof"] for r in out), default=str(date.today()))
    out.sort(key=lambda r: -max(p["rr"] for p in r["plans"]))
    d = ROOT / "reports" / "morning"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{asof}.json").write_text(json.dumps({"asof": asof, "universe": len(uni), "hits": out},
                                               ensure_ascii=False, indent=1))
    lines = [f"# 단테 아침 스캔 ({asof} 종가, 대상 {len(uni)}종목, 후보 {len(out)})", "",
             "| 종목 | 식 | 종가 | 등락 | 진입 | 손절 | 목표 | 손절% | 손익비 | 거래대금20(억) |", "|---|---|---|---|---|---|---|---|---|---|"]
    for r in out:
        for p in r["plans"]:
            lines.append(f"| {r['name']}({r['code']}) | {p['type']} | {r['close']:,} | {r['chg']}% | {p['entry']:,} | "
                         f"{p['stop']:,} | {p['target']:,} | {p['stop_pct']}% | {p['rr']} | {r['val20_eok']} |")
    (d / f"{asof}.md").write_text("\n".join(lines) + "\n")
    print(len(out))


if __name__ == "__main__":
    main()
