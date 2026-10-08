"""단테 주요 기법이 '오늘 형성된' 종목 발굴 (관심종목 + 코스피·코스닥 전 종목).

docs/dante_technique.md 의 5대 기법을 마지막 확정 봉에서 새로 성립한 사건으로 판정한다.
  밥그릇 3번   bowl.py: 224선 강한 돌파 + 1번 낙폭·2번 바닥 횡보 역추적
  112·224      20봉 중 10봉 이상 112선 위에서 지지받다가 224선 돌파 (+2%, 거래량 1.5배)
  지분         224선 회복 + 최근 224봉의 224선 위·아래 면적이 35~65% (1:1 근접)
  256          5/20 골든크로스 뒤 20선 지지·60선 저항(256 자리)에 머물다 60선 돌파 (+2%, 거래량 1.5배)
  역매공파     60봉 전 역배열(20<60<112<224), 40봉 안 거래량 급증(3배), 오늘 60선 위 안착(2일째)
  오돌이       2일 이상 5선 아래 있던 주가가 5선과 전일 시가를 잡아먹는 양봉(거래량 전일↑, 20일 평균↑). 256 자리이거나
               60·112·224선 위 3% 안쪽 지지 자리에서만 인정
수치는 영상에서 확정된 값이 아니라 판정용 근사다. 마지막 봉은 네이버 실시간 KRX 정규장 OHLCV 로 교정한다.

출력: reports/dante/latest.json  ({"mode", "asof", "message", "hits"})
사용: python screener/dante_scan.py am|pm
     python screener/dante_scan.py backtest 일수 종목명...
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import FinanceDataReader as fdr
import pandas as pd

from bowl import bowl_at, load, prepare, universe
from brief import watchlist

ROOT = Path(__file__).resolve().parent.parent
KST = timezone(timedelta(hours=9))
MIN_VALUE_MINE = 3e8    # 관심종목 20일 평균 거래대금 하한
MIN_VALUE_MARKET = 10e8  # 관심종목 밖 하한
MARKET_MAX = 12          # 관심종목 밖은 기법 수·거래량 순으로 이만큼만


def crossed_up(df, i, col):
    return df["Close"].iloc[i - 1] <= df[col].iloc[i - 1] and df["Close"].iloc[i] > df[col].iloc[i]


def signals(df, i):
    """i 번째 봉에서 새로 성립한 기법들 [(이름, 근거, 실패 기준)]."""
    t, p = df.iloc[i], df.iloc[i - 1]
    vol_x = t["Volume"] / df["VMA20"].iloc[i - 1]
    has224 = pd.notna(t["MA224"])
    out = []

    b = bowl_at(df, i)
    if b:
        two = ", ".join(x for x in [f"매집봉 {b['acc_n']}개" if b["acc_n"] else "",
                                    f"골파기 {len(b['gol'])}회" if b["gol"] else ""] if x) or "매집 흔적 약함"
        out.append(("밥그릇 3번", f"1번 {b['high_date'][2:7].replace('-', '.')} 고점 대비 {b['drop']}%, "
                                 f"2번 바닥 후 {b['base_days']}일 횡보({two}), 3번 224선 {b['ma224']:,} {'재돌파' if b['re'] else '돌파'}",
                    f"종가 224선 {b['ma224']:,} 이탈"))

    strong = t["chg"] >= 2 and vol_x >= 1.5
    if has224 and crossed_up(df, i, "MA224"):
        on112 = int((df["Close"].iloc[i - 20:i] > df["MA112"].iloc[i - 20:i]).sum())
        if strong and on112 >= 10 and t["MA112"] < t["MA224"]:
            out.append(("112·224", f"112선 위 {on112}/20일 지지 후 224선 {t['MA224']:,.0f} 돌파", f"종가 112선 {t['MA112']:,.0f} 이탈"))
        w = df.iloc[i - 223:i + 1]
        diff = (w["Close"] - w["MA224"]) / w["MA224"]
        buy, sell = diff.clip(lower=0).sum(), (-diff).clip(lower=0).sum()
        share = buy / (buy + sell) * 100 if buy + sell else 50
        if 35 <= share <= 65 and vol_x >= 1.2:
            out.append(("지분", f"224선 회복, 매수 지분 {share:.0f}% : 매도 {100 - share:.0f}%", f"종가 224선 {t['MA224']:,.0f} 이탈"))

    if crossed_up(df, i, "MA60") and strong and t["Close"] > t["MA20"]:
        gc = ((df["MA5"] > df["MA20"]) & (df["MA5"].shift(1) <= df["MA20"].shift(1))).iloc[i - 20:i].any()
        zone = ((df["Close"] > df["MA20"]) & (df["Close"] < df["MA60"])).iloc[i - 10:i].sum()
        if gc and zone >= 3 and t["MA20"] < t["MA60"]:
            out.append(("256", f"5/20 골든크로스 후 256 자리 {zone}일, 60선 {t['MA60']:,.0f} 돌파", f"종가 20선 {t['MA20']:,.0f} 이탈"))

    if has224 and i >= 62:
        past = df.iloc[i - 60]
        rev = past["MA20"] < past["MA60"] < past["MA112"] < past["MA224"]
        spikes = int((df["Volume"].iloc[i - 40:i + 1] > 3 * df["VMA20"].shift(1).iloc[i - 40:i + 1]).sum())
        settle = t["Close"] > t["MA60"] and p["Close"] > p["MA60"] and df["Close"].iloc[i - 2] <= df["MA60"].iloc[i - 2]
        if rev and spikes and settle:
            tgt = f"1차 목표 224선 {t['MA224']:,.0f}"
            out.append(("역매공파", f"역배열 바닥에서 거래량 급증 {spikes}회 뒤 60선 위 안착 2일째, {tgt}", f"60선 {t['MA60']:,.0f} 이탈"))

    pp = df.iloc[i - 2]
    odol = (p["Close"] < p["MA5"] and pp["Close"] < pp["MA5"] and t["Close"] > t["MA5"] and t["Close"] > t["Open"]
            and t["Close"] > p["Open"] and t["Volume"] > p["Volume"] and vol_x >= 1.0)
    if odol:
        spot = None
        if t["MA20"] < t["Close"] < t["MA60"]:
            spot = "256 자리"
        else:
            for n in [60, 112, 224]:
                m = t[f"MA{n}"]
                if pd.notna(m) and t["Low"] <= m * 1.03 and t["Close"] > m:
                    spot = f"{n}선 지지"
                    break
        if spot:
            out.append(("오돌이", f"{spot}에서 5선 {t['MA5']:,.0f} 회복 양봉, 거래량 전일 {t['Volume'] / p['Volume']:.1f}배",
                        f"오늘 저가 {t['Low']:,.0f} 이탈"))
    return out, round(float(vol_x), 1)


def fmt_hit(h):
    head = f"• {h['name']} {h['close']:,} ({h['chg']:+.1f}%, 거래량 {h['vol_x']}배) {' + '.join(s[0] for s in h['sig'])}"
    body = [f"  {name}: {why}" for name, why, _ in h["sig"]]
    fails = list(dict.fromkeys(f for _, _, f in h["sig"]))
    return "\n".join([head] + body + [f"  실패 기준: {', '.join(fails)}"])


def main(mode):
    now = datetime.now(KST)
    today = now.date()
    uni = universe()
    mine = set(watchlist())

    def one(r):
        try:
            df = load(r["Code"], mode, today)
            if len(df) < 120:
                return None
            i = len(df) - 1
            val20 = float((df["Close"] * df["Volume"]).iloc[-20:].mean())
            is_mine = r["Name"] in mine
            if val20 < (MIN_VALUE_MINE if is_mine else MIN_VALUE_MARKET):
                return str(df.index[i].date()), None
            sig, vol_x = signals(df, i)
            if not sig:
                return str(df.index[i].date()), None
            return str(df.index[i].date()), {
                "name": r["Name"], "code": r["Code"], "mine": is_mine, "close": int(df["Close"].iloc[i]),
                "chg": round(float(df["chg"].iloc[i]), 1), "vol_x": vol_x, "val20_eok": round(val20 / 1e8),
                "sig": sig}
        except Exception as e:
            print(f"{r['Name']} failed: {e!r}", file=sys.stderr)
            return None

    with ThreadPoolExecutor(12) as ex:
        res = [x for x in ex.map(one, [r for _, r in uni.iterrows()]) if x]
    asof = max(d for d, _ in res)
    hits = [h for d, h in res if h and d == asof]
    order = {"밥그릇 3번": 0, "112·224": 1, "지분": 2, "역매공파": 3, "256": 4, "오돌이": 5}
    key = lambda h: (-len(h["sig"]), min(order[s[0]] for s in h["sig"]), -h["vol_x"])
    my = sorted([h for h in hits if h["mine"]], key=key)
    mk = sorted([h for h in hits if not h["mine"]], key=key)

    label = {"am": "아침", "pm": "장마감"}[mode]
    lines = [f"[{label}] 단테 기법 발굴 ({pd.Timestamp(asof):%m/%d} 종가 기준, {len(res)}종목 확인)"]
    lines.append("관심종목" + ("" if my else ": 새로 성립한 기법 없음"))
    lines += [fmt_hit(h) for h in my]
    if mk:
        more = f" (상위 {MARKET_MAX}개, 전체 {len(mk)}개)" if len(mk) > MARKET_MAX else ""
        lines.append("관심종목 밖" + more)
        lines += [fmt_hit(h) for h in mk[:MARKET_MAX]]

    out = ROOT / "reports" / "dante"
    out.mkdir(parents=True, exist_ok=True)
    data = {"mode": mode, "asof": asof, "generated": now.isoformat(timespec="minutes"), "message": "\n".join(lines),
            "checked": len(res), "hits": my + mk}
    (out / "latest.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(data["message"])


def backtest(days, names):
    lst = fdr.StockListing("KRX").set_index("Name")
    for n in names:
        df = prepare(fdr.DataReader(lst.loc[n, "Code"], "2023-01-01"))
        for i in range(len(df) - days, len(df)):
            sig, vol_x = signals(df, i)
            if sig:
                print(f"{n} {df.index[i].date()} {df['Close'].iloc[i]:,.0f} ({df['chg'].iloc[i]:+.1f}%, {vol_x}배): "
                      + " / ".join(f"{a} - {b}" for a, b, _ in sig))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "backtest":
        backtest(int(sys.argv[2]), sys.argv[3:])
    else:
        main(sys.argv[1] if len(sys.argv) > 1 else "pm")
