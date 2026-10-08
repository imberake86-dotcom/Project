"""밥그릇 3번 자리 알림 (관심종목 + 코스피·코스닥 전 종목).

docs/dante_technique.md 의 밥그릇 기법: 3번(224일선 강한 돌파)을 먼저 찾고, 과거로 거슬러
1번(충분한 낙폭)과 2번(224선 아래 바닥 횡보·매집 흔적)이 있었는지 확인한다.

  3번  마지막 확정 봉에서 종가가 224선을 아래→위로 돌파. 상승률 +3%↑, 거래량 20일 평균 2배↑
  1번  돌파 전 60~480봉 사이 고점 대비, 최근 240봉 저점까지 -35% 이상 하락 (고점이 저점보다 먼저)
  2번  최근 240봉 바닥(최저가) 이후 20봉 이상 경과, 그 기간 종가의 80% 이상이 224선 아래
       흔적: 매집봉(224선 아래에서 거래량 20일 평균 3배↑ + 상승 5%↑ 또는 윗꼬리 3%↑),
             골파기(직전 60봉 저점을 깼다가 10봉 안에 종가로 회복)
  매집봉·골파기가 하나도 없으면 '2번 흔적 약함'으로 표시한다.

수치 기준은 영상에서 확정된 값이 아니라 판정용 근사다 (docs/dante_technique.md, 규칙정리 R05 참고).
마지막 봉은 네이버 실시간 시세의 KRX 정규장 OHLCV 로 교정한다 (넥스트레이드 체결 제외).

출력: reports/bowl/latest.json  ({"mode", "asof", "message", "hits"})
사용: python screener/bowl.py am|pm   (am: 장 시작 전, 어제 봉 기준 / pm: 장 마감 뒤, 오늘 봉 기준)
     python screener/bowl.py backtest 종목명...   (최근 250봉에서 3번 자리가 나왔던 날 출력)
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import FinanceDataReader as fdr
import pandas as pd

from brief import naver_regular, watchlist

ROOT = Path(__file__).resolve().parent.parent
KST = timezone(timedelta(hours=9))


def prepare(df):
    df = df[df["Volume"] > 0].copy()
    for n in [5, 20, 60, 112, 224]:
        df[f"MA{n}"] = df["Close"].rolling(n).mean()
    df["VMA20"] = df["Volume"].rolling(20).mean()
    df["chg"] = df["Close"].pct_change() * 100
    return df


def bowl_at(df, i):
    """i 번째 봉이 밥그릇 3번 자리면 근거를 담은 dict, 아니면 None."""
    if i < 300:
        return None
    t, p = df.iloc[i], df.iloc[i - 1]
    if pd.isna(t["MA224"]) or pd.isna(p["MA224"]):
        return None
    vol_x = t["Volume"] / df["VMA20"].iloc[i - 1]
    if not (p["Close"] <= p["MA224"] and t["Close"] > t["MA224"] and t["chg"] >= 3 and vol_x >= 2):
        return None

    # 1번: 낙폭
    past = df.iloc[max(0, i - 480):i - 60]
    recent = df.iloc[i - 240:i]
    hi_d, lo_d = past["High"].idxmax(), recent["Low"].idxmin()
    hi, lo = past["High"].max(), recent["Low"].min()
    drop = (lo / hi - 1) * 100
    if drop > -35 or hi_d >= lo_d:
        return None

    # 2번: 바닥(최저가) 이후 224선 아래 횡보
    lo_i = df.index.get_loc(lo_d)
    since_low = i - lo_i
    base = df.iloc[lo_i:i]
    below = float((base["Close"] < base["MA224"]).mean()) if since_low else 0
    if below < 0.8 or since_low < 20:
        return None
    ws = max(i - 120, lo_i - 20)  # 매집 흔적은 바닥 직전부터 돌파 전까지 (최대 120봉)
    acc = []
    for j in range(ws, i - 3):  # 돌파 직전 3봉은 매집이 아니라 상승 시작으로 본다
        b = df.iloc[j]
        wick = (b["High"] / max(b["Close"], b["Open"]) - 1) * 100
        if b["Close"] < b["MA224"] and b["Volume"] >= 3 * df["VMA20"].iloc[j - 1] and (b["chg"] >= 5 or wick >= 3):
            acc.append(df.index[j])
    gol = []
    for j in range(ws, i - 1):
        floor = df["Low"].iloc[j - 60:j].min()
        if df["Low"].iloc[j] < floor and (df["Close"].iloc[j + 1:min(i, j + 11)] > floor).any():
            if not gol or (df.index[j] - gol[-1]).days > 20:
                gol.append(df.index[j])
    box = df.iloc[max(lo_i, i - 60):i]
    re = bool((df["Close"].iloc[i - 10:i - 1] > df["MA224"].iloc[i - 10:i - 1]).any())  # 10봉 안에 224선 위였다가 밀린 뒤 다시 돌파
    return {
        "date": str(df.index[i].date()), "close": int(t["Close"]), "chg": round(float(t["chg"]), 1),
        "vol_x": round(float(vol_x), 1), "ma224": int(round(t["MA224"])),
        "drop": round(float(drop)), "high": int(hi), "high_date": str(hi_d.date()), "low": int(lo), "low_date": str(lo_d.date()),
        "base_days": int(since_low), "below224_pct": round(below * 100), "box_pct": round(float((box["High"].max() / box["Low"].min() - 1) * 100)),
        "acc": [str(d.date())[5:] for d in acc][-4:], "acc_n": len(acc), "gol": [str(d.date())[5:] for d in gol][-3:],
        "trace_ok": bool(acc or gol), "re": re,
    }


def load(code, mode, today):
    df = fdr.DataReader(code, (today - timedelta(days=800)).isoformat())
    df = df[df["Volume"] > 0].copy()
    if mode == "am":
        df = df[df.index.date < today]
    if df.empty:
        return df
    day, q = naver_regular(code)
    if q and q["Close"] > 0 and day == df.index[-1].date():
        for k, v in q.items():
            if v > 0:
                df.iloc[-1, df.columns.get_loc(k)] = v
    return prepare(df)


def universe():
    df = fdr.StockListing("KRX")
    df = df[df["Market"].isin(["KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"])]
    df = df[df["Code"].str.endswith("0")]
    df = df[~df["Name"].str.contains("스팩|리츠")]
    if "Dept" in df:
        df = df[~df["Dept"].fillna("").str.contains("관리|투자주의환기")]
    return df


def line(h):
    two = []
    if h["acc"]:
        two.append(f"매집봉 {h['acc_n']}개({', '.join(h['acc'])})")
    if h["gol"]:
        two.append(f"골파기 {', '.join(h['gol'])}")
    two = ", ".join(two) if two else "매집 흔적 약함"
    mark = "" if h["trace_ok"] else " (2번 확인 필요)"
    return (f"• {h['name']} {h['close']:,} (+{h['chg']}%, 거래량 {h['vol_x']}배){mark}\n"
            f"  1번 {h['high_date'][2:7].replace('-', '.')} 고점 {h['high']:,} → 저점 {h['low']:,} ({h['drop']}%)\n"
            f"  2번 바닥 후 {h['base_days']}일 횡보, {two}\n"
            f"  3번 224선 {h['ma224']:,} {'재돌파' if h['re'] else '돌파'}. 종가로 224선 이탈하면 실패")


def main(mode):
    now = datetime.now(KST)
    today = now.date()
    uni = universe()
    mine = set(watchlist())
    rows = [r for _, r in uni.iterrows()]

    def one(r):
        try:
            df = load(r["Code"], mode, today)
            if len(df) < 300:
                return None
            h = bowl_at(df, len(df) - 1)
            if h:
                h.update({"name": r["Name"], "code": r["Code"], "mine": r["Name"] in mine})
            return h, str(df.index[-1].date())
        except Exception as e:
            print(f"{r['Name']} failed: {e!r}", file=sys.stderr)
            return None

    with ThreadPoolExecutor(12) as ex:
        res = [x for x in ex.map(one, rows) if x]
    asof = max(d for _, d in res)
    hits = [h for h, d in res if h and d == asof]
    hits.sort(key=lambda h: (not h["mine"], not h["trace_ok"], -h["vol_x"]))

    label = {"am": "아침", "pm": "장마감"}[mode]
    lines = [f"[{label}] 밥그릇 3번 자리 ({pd.Timestamp(asof):%m/%d} 종가 기준, {len(res)}종목 확인)"]
    for title, grp in [("관심종목", [h for h in hits if h["mine"]]), ("관심종목 밖", [h for h in hits if not h["mine"]])]:
        if grp:
            lines.append(title)
            lines += [line(h) for h in grp]
    if not hits:
        lines.append("오늘은 밥그릇 3번 자리가 형성된 종목이 없습니다.")

    out = ROOT / "reports" / "bowl"
    out.mkdir(parents=True, exist_ok=True)
    data = {"mode": mode, "asof": asof, "generated": now.isoformat(timespec="minutes"), "message": "\n".join(lines),
            "checked": len(res), "hits": hits}
    (out / "latest.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(data["message"])


def backtest(names):
    lst = fdr.StockListing("KRX").set_index("Name")
    for n in names:
        df = prepare(fdr.DataReader(lst.loc[n, "Code"], "2023-01-01"))
        for i in range(len(df) - 250, len(df)):
            h = bowl_at(df, i)
            if h:
                h["name"] = n
                print(line(h).replace(h["name"], f"{n} {h['date']}", 1))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "backtest":
        backtest(sys.argv[2:])
    else:
        main(sys.argv[1] if len(sys.argv) > 1 else "pm")
