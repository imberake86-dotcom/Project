"""주식단테 기법 스크리너.

docs/dante_technique.md 의 기법(밥그릇, 256, 112·224, 지분, 역매공파, 오돌이)을
일봉 종가·거래량 규칙으로 옮겨 관심 종목을 판정한다.

사용: python screener/dante.py [종목명 ...]
결과: reports/latest.md, reports/latest.json
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

WATCHLIST = ["원일특강", "오상헬스케어", "그린생명과학", "이건홀딩스", "블루엠텍"]
MAS = [5, 20, 60, 112, 224, 448]
RECENT = 3  # 돌파를 '오늘의 타점'으로 볼 기간(거래일)
ROOT = Path(__file__).resolve().parent.parent


def find_code(name):
    """종목명 → 6자리 코드. KRX 상장목록, 실패하면 네이버 자동완성."""
    try:
        import FinanceDataReader as fdr
        df = fdr.StockListing("KRX")
        hit = df.loc[df["Name"] == name, "Code"]
        if len(hit):
            return hit.iloc[0]
    except Exception as e:
        print(f"KRX listing failed: {e!r}", file=sys.stderr)
    import requests
    res = requests.get("https://ac.stock.naver.com/ac", params={"q": name, "target": "stock"}, timeout=10).json()
    for item in res.get("items", []):
        if item.get("name") == name and item.get("nationCode", "KOR") == "KOR":
            return item["code"]
    return None


def load_prices(code):
    import FinanceDataReader as fdr
    start = (date.today() - timedelta(days=365 * 3)).isoformat()
    df = fdr.DataReader(code, start)
    df = df[df["Volume"] > 0].copy()
    for n in MAS:
        df[f"MA{n}"] = df["Close"].rolling(n).mean()
    df["VMA20"] = df["Volume"].rolling(20).mean()
    return df


def crossed_up(df, ma, within=RECENT):
    """최근 within일 안에 종가가 ma를 아래에서 위로 돌파했는지."""
    c, m = df["Close"], df[ma]
    above = c > m
    cross = above & ~above.shift(1, fill_value=True)
    return bool(cross.iloc[-within:].any()) and bool(above.iloc[-1])


def pct(a, b):
    return (a / b - 1) * 100 if b and not np.isnan(b) else float("nan")


def analyze(df):
    t = df.iloc[-1]
    close = t["Close"]
    n = len(df)
    vol_ratio = t["Volume"] / t["VMA20"] if t["VMA20"] else float("nan")
    r = {"date": str(df.index[-1].date()), "close": float(close), "bars": n,
         "vol_ratio": round(float(vol_ratio), 2)}
    for m in MAS:
        v = t[f"MA{m}"]
        r[f"MA{m}"] = None if np.isnan(v) else round(float(v), 1)
        r[f"gap{m}"] = None if np.isnan(v) else round(pct(close, v), 1)
    s = {}

    # 1. 밥그릇: 224선 돌파(3번)를 먼저 찾고, 1번(낙폭)·2번(횡보 매집)을 역추적
    if n >= 224 and r["MA224"]:
        hi = df["High"].iloc[-480:].max()
        lo = df["Low"].iloc[-240:].min()
        drop = pct(lo, hi)
        base = df.iloc[-120:-RECENT]
        base_range = pct(base["High"].max(), base["Low"].min())
        spikes = int((base["Volume"] > 3 * base["VMA20"]).sum())
        brk = crossed_up(df, "MA224") and vol_ratio >= 1.5
        s["밥그릇"] = {
            "signal": brk and drop <= -35,
            "detail": f"1번 낙폭 {drop:.0f}% / 2번 120일 박스 폭 {base_range:.0f}%, 매집성 거래량(20일평균 3배↑) {spikes}회 / "
                      f"3번 224선 {'돌파' if brk else ('위' if close > t['MA224'] else '아래')} (224선 대비 {r['gap224']:+.1f}%)",
        }
    else:
        s["밥그릇"] = {"signal": False, "detail": f"상장 {n}거래일로 224일선이 아직 없음"}

    # 2-a. 256: 5일선 회복 + 5/20 골든크로스 후 20~60 사이(256 자리) → 60선 돌파
    if r["MA60"]:
        gc520 = bool(((df["MA5"] > df["MA20"]) & (df["MA5"].shift(1) <= df["MA20"].shift(1))).iloc[-20:].any())
        in_zone = t["MA20"] < close < t["MA60"] and close > t["MA5"]
        brk60 = crossed_up(df, "MA60")
        s["256"] = {
            "signal": brk60 and close > t["MA20"] and vol_ratio >= 1.5,
            "zone": in_zone,
            "detail": " / ".join(x for x in [
                f"5/20 골든크로스(20일 내) {'있음' if gc520 else '없음'}",
                "256 자리(20선 지지·60선 저항)" if in_zone else "",
                f"{RECENT}일 내 60선 돌파" if brk60 else "",
                f"60선 대비 {r['gap60']:+.1f}%"] if x),
        }

    # 2-b. 112·224: 112선 위에서 지지 → 224선 공격
    if r["MA224"]:
        above112 = int((df["Close"].iloc[-20:] > df["MA112"].iloc[-20:]).sum())
        s["112·224"] = {
            "signal": crossed_up(df, "MA224") and above112 >= 10,
            "zone": close > t["MA112"] and close < t["MA224"] and above112 >= 10,
            "detail": f"최근 20일 중 112선 위 {above112}일 / 112선 {r['gap112']:+.1f}%, 224선 {r['gap224']:+.1f}%",
        }
    elif r["MA112"]:
        s["112·224"] = {"signal": False, "detail": f"224선 없음, 112선 대비 {r['gap112']:+.1f}%"}

    # 3. 지분: 최근 224일 동안 224선 위(매수)·아래(매도) 면적 비율
    if r["MA224"]:
        w = df.iloc[-224:].dropna(subset=["MA224"])
        diff = (w["Close"] - w["MA224"]) / w["MA224"]
        buy, sell = diff.clip(lower=0).sum(), (-diff).clip(lower=0).sum()
        share = buy / (buy + sell) * 100 if buy + sell else 50
        s["지분"] = {
            "signal": 40 <= share and crossed_up(df, "MA224", within=5),
            "share": round(float(share), 0),
            "detail": f"매수 지분 {share:.0f}% : 매도 지분 {100 - share:.0f}% (50:50 근접 + 224선 회복 시 신호)",
        }

    # 4. 역매공파: 역배열 바닥 → 거래량 턴어라운드 → 60선 위 안착
    if r["MA224"]:
        past = df.iloc[-60]
        rev = past["MA20"] < past["MA60"] < past["MA112"] < past["MA224"]
        turn = int((df["Volume"].iloc[-40:] > 3 * df["VMA20"].iloc[-40:]).sum())
        on60 = int((df["Close"].iloc[-5:] > df["MA60"].iloc[-5:]).sum())
        s["역매공파"] = {
            "signal": rev and turn > 0 and crossed_up(df, "MA60", within=5) and on60 >= 2,
            "zone": rev and turn > 0,
            "detail": f"60일 전 역배열 {'예' if rev else '아니오'} / 40일 내 거래량 급증 {turn}회 / "
                      f"최근 5일 중 60선 위 {on60}일 / 목표 224선 {r['MA224']}"
                      + (f", 448선 {r['MA448']}" if r["MA448"] else ""),
        }

    # 5. 오돌이: 5일선 아래 캔들이 5일선을 잡아먹는 양봉
    p = df.iloc[-2]
    odol = p["Close"] < p["MA5"] and t["Close"] > t["MA5"] and t["Close"] > t["Open"] and t["Close"] > p["Open"]
    s["오돌이"] = {
        "signal": bool(odol),
        "detail": f"전일 5선 {'아래' if p['Close'] < p['MA5'] else '위'} → 오늘 {'5선 회복 양봉' if odol else '변곡 없음'} "
                  f"(거래량 20일평균 {vol_ratio:.1f}배; 장 시작 1분 거래량 조건은 분봉 필요)",
    }

    for k in s:
        s[k]["signal"] = bool(s[k]["signal"])
        if "zone" in s[k]:
            s[k]["zone"] = bool(s[k]["zone"])
    r["setups"] = s
    tail = df.iloc[-10:]
    r["recent"] = [
        {"date": str(i.date()), "close": float(b["Close"]), "chg": round(float(pct(b["Close"], df["Close"].shift(1)[i])), 1),
         "vol_x": round(float(b["Volume"] / b["VMA20"]), 1),
         "above": [m for m in (5, 20, 60, 112, 224) if b["Close"] > b[f"MA{m}"]]}
        for i, b in tail.iterrows()
    ]
    # 추세전환 타점: 중장기 돌파 신호, 또는 256 자리에서의 오돌이
    major = [k for k in ("밥그릇", "256", "112·224", "지분", "역매공파") if s.get(k, {}).get("signal")]
    if s["오돌이"]["signal"] and s.get("256", {}).get("zone"):
        major.append("오돌이(256 자리)")
    r["entry"] = major
    return r


def stage(r):
    g = lambda k: r.get(k)
    if g("gap224") is not None and g("gap224") > 0:
        return "224선 위 (상승 추세)"
    if g("gap60") is not None and g("gap60") > 0:
        return "60선 위·224선 아래 (추세 전환 진행)"
    if g("gap20") is not None and g("gap20") > 0:
        return "20선 위·60선 아래 (256 구간)"
    return "20선 아래 (하락·바닥 다지기)"


def main(names):
    results = {}
    for name in names:
        code = find_code(name)
        if not code:
            results[name] = {"error": "KRX 상장목록에서 종목명을 찾지 못함"}
            continue
        try:
            r = analyze(load_prices(code))
            r["code"] = code
            r["stage"] = stage(r)
            results[name] = r
        except Exception as e:  # 한 종목 실패가 전체를 막지 않게
            results[name] = {"code": code, "error": repr(e)}

    out = ROOT / "reports"
    out.mkdir(parents=True, exist_ok=True)
    (out / "latest.json").write_text(json.dumps(results, ensure_ascii=False, indent=1, default=float))
    lines = ["# 주식단테 기법 점검", ""]
    hits = [n for n, r in results.items() if r.get("entry")]
    lines.append("**타점 발생: " + (", ".join(f"{n}({'/'.join(results[n]['entry'])})" for n in hits) if hits else "없음") + "**")
    lines.append("")
    for name, r in results.items():
        if "error" in r:
            lines += [f"## {name}", f"- 오류: {r['error']}", ""]
            continue
        lines.append(f"## {name} ({r['code']}) {r['date']} 종가 {r['close']:,.0f}")
        lines.append(f"- 위치: {r['stage']} · 이평 대비 " + ", ".join(
            f"{m}선 {r[f'gap{m}']:+.1f}%" for m in MAS if r.get(f"gap{m}") is not None))
        for k, v in r["setups"].items():
            mark = "🟢" if v["signal"] else ("🟡" if v.get("zone") else "⚪")
            lines.append(f"- {mark} {k}: {v['detail']}")
        lines.append("- 최근 5일: " + ", ".join(
            f"{b['date'][5:]} {b['chg']:+.1f}% 거래량{b['vol_x']}배" for b in r["recent"][-5:]))
        lines.append("")
    (out / "latest.md").write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main(sys.argv[1:] or WATCHLIST)
