"""주식단테 300편 규칙(R01~R14) 중 일봉으로 계산할 수 있는 항목을 종목별로 뽑는다.

입력: data/prices/<종목명>.csv (dump_prices.py)
출력: reports/rules.json  — 판정 근거가 되는 수치만 담고, 진입 여부 판단은 하지 않는다.

규칙 근거: /mnt/project-files/주식단테_최근300쇼츠_규칙정리.md
"""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
MAS = [5, 10, 20, 60, 112, 224, 448]


def load(name):
    df = pd.read_csv(ROOT / "data" / "prices" / f"{name}.csv", parse_dates=["Date"], index_col="Date")
    for n in MAS:
        df[f"MA{n}"] = df["Close"].rolling(n).mean()
    df["VMA20"] = df["Volume"].rolling(20).mean()
    df["chg"] = df["Close"].pct_change() * 100
    df["body"] = (df["Close"] / df["Open"] - 1) * 100
    return df


def r(x, d=0):
    return None if x is None or pd.isna(x) else round(float(x), d)


def last_cross(df, fast, slow, lookback=120):
    """최근 lookback 일 안에서 fast 가 slow 를 위로 교차한 마지막 날과 경과일."""
    a, b = df[fast], df[slow]
    up = (a > b) & (a.shift(1) <= b.shift(1))
    dn = (a < b) & (a.shift(1) >= b.shift(1))
    w = df.index[-lookback:]
    ups = [d for d in w if up.get(d, False)]
    dns = [d for d in w if dn.get(d, False)]
    lu = ups[-1] if ups else None
    ld = dns[-1] if dns else None
    return {
        "last_up": str(lu.date()) if lu is not None else None,
        "days_since_up": int(len(df.loc[lu:]) - 1) if lu is not None else None,
        "last_down": str(ld.date()) if ld is not None else None,
        "now_above": bool(a.iloc[-1] > b.iloc[-1]),
    }


def ref_candles(df, lookback=40):
    """기준봉 후보 (R04): 최근 lookback 일 중 몸통 +5% 이상 양봉이면서 거래량 20일평균 2배 이상,
    그리고 직전 20일 고가를 종가로 넘어선 봉. 화자가 수치를 정하지 않았으므로 이 기준은 임의 근사다."""
    out = []
    w = df.iloc[-lookback:]
    for d, b in w.iterrows():
        i = df.index.get_loc(d)
        prev_hi = df["High"].iloc[max(0, i - 20):i].max()
        if b["body"] >= 5 and b["Volume"] >= 2 * df["VMA20"].iloc[i - 1] and b["Close"] > prev_hi:
            out.append({"date": str(d.date()), "open": r(b["Open"]), "close": r(b["Close"]), "high": r(b["High"]),
                        "body_pct": r(b["body"], 1), "vol_x": r(b["Volume"] / df["VMA20"].iloc[i - 1], 1),
                        "broke_20d_high": r(prev_hi),
                        "open_held_since": bool((df["Close"].iloc[i:] >= b["Open"]).all())})
    return out


def swing_highs(df, lookback=480, k=10):
    """저항 후보: lookback 일 안의 국소 고점(앞뒤 k일 중 최고) 가운데 현재가 위에 있는 것."""
    w = df.iloc[-lookback:]
    hi = w["High"]
    piv = hi[(hi == hi.rolling(2 * k + 1, center=True).max())]
    px = df["Close"].iloc[-1]
    above = sorted({r(v) for v in piv if v > px})
    return above[:6]


def features(name):
    df = load(name)
    t, p = df.iloc[-1], df.iloc[-2]
    px = t["Close"]
    f = {"name": name, "date": str(df.index[-1].date()), "bars": len(df),
         "ohlcv": {k: r(t[k]) for k in ["Open", "High", "Low", "Close", "Volume"]},
         "chg_pct": r(t["chg"], 2), "vol_x": r(t["Volume"] / df["VMA20"].iloc[-2], 2)}
    f["ma"] = {n: r(t[f"MA{n}"]) for n in MAS}
    f["gap_to_ma_pct"] = {n: r((px / t[f"MA{n}"] - 1) * 100, 1) for n in MAS}
    f["ma_slope_5d_pct"] = {n: r((t[f"MA{n}"] / df[f"MA{n}"].iloc[-6] - 1) * 100, 2) for n in MAS}

    # R02 오돌이: 전일 5선 아래 → 오늘 5선 위, 5선 상승 전환
    f["R02_odol"] = {"prev_below_ma5": bool(p["Close"] <= p["MA5"]), "now_above_ma5": bool(px > t["MA5"]),
                     "ma5_turning_up": bool(t["MA5"] > p["MA5"]), "bullish": bool(px > t["Open"])}
    # R03 5·20·60 과 5·112·224
    f["R03_5_20"] = last_cross(df, "MA5", "MA20")
    f["R03_5_112"] = last_cross(df, "MA5", "MA112", lookback=240)
    f["R03_stack_now"] = {"above_5_20_below_60": bool(px > t["MA5"] and px > t["MA20"] and px < t["MA60"]),
                          "above_5_112_below_224": bool(px > t["MA5"] and px > t["MA112"] and px < t["MA224"])}
    # 교차 직전 역배열(60>20>5) 이었는지 (R03 / 규칙정리 5절 B: 교차 '전' 시점에 확인)
    cu = f["R03_5_20"]["last_up"]
    if cu:
        i = df.index.get_loc(pd.Timestamp(cu))
        b = df.iloc[i - 1]
        f["R03_5_20"]["reverse_before_cross"] = bool(b["MA60"] > b["MA20"] > b["MA5"])
    cu = f["R03_5_112"]["last_up"]
    if cu and pd.notna(df["MA224"].iloc[df.index.get_loc(pd.Timestamp(cu)) - 1]):
        i = df.index.get_loc(pd.Timestamp(cu))
        b = df.iloc[i - 1]
        f["R03_5_112"]["reverse_before_cross"] = bool(b["MA224"] > b["MA112"] > b["MA5"])
    # 최근 20일 종가가 20선/112선 아래로 내려간 날 수 (지지 유지 확인)
    f["closes_below_ma20_last10"] = int((df["Close"].iloc[-10:] < df["MA20"].iloc[-10:]).sum())
    f["closes_below_ma112_last10"] = int((df["Close"].iloc[-10:] < df["MA112"].iloc[-10:]).sum())

    # R04 기준봉
    f["R04_ref_candles"] = ref_candles(df)
    # R06 장기선 위 지지 + 저점 상승: 최근 3개 20일 구간의 저점
    lows = [r(df["Low"].iloc[-60 + 20 * k:-40 + 20 * k if k < 2 else None].min()) for k in range(3)]
    f["R06"] = {"lows_3x20d": lows, "higher_lows": bool(lows[0] < lows[1] < lows[2]),
                "above_112": bool(px > t["MA112"]), "above_224": bool(px > t["MA224"])}
    # R08 기간 신고가 (당일 고가 기준, 당일 제외한 직전 N일 고가 대비)
    f["R08_new_high"] = {n: bool(t["High"] > df["High"].iloc[-n - 1:-1].max()) for n in [5, 10, 20, 60, 120, 224, 448]
                         if len(df) > n}
    f["R08_close_new_high"] = {n: bool(px > df["High"].iloc[-n - 1:-1].max()) for n in [5, 10, 20, 60, 120, 224]
                               if len(df) > n}
    # R09 저항: 현재가 위 이평선 + 국소 고점
    f["R09_resistance"] = {"ma_above": {n: r(t[f"MA{n}"]) for n in MAS if pd.notna(t[f"MA{n}"]) and t[f"MA{n}"] > px},
                           "swing_highs_above": swing_highs(df)}
    # R11 갭: 오늘 시가의 전일 종가 대비, 전일 고가 대비
    f["R11_gap"] = {"open_vs_prev_close_pct": r((t["Open"] / p["Close"] - 1) * 100, 1),
                    "open_vs_prev_high_pct": r((t["Open"] / p["High"] - 1) * 100, 1)}
    # R01 위상: 60일 전 대비 하락폭, 최근 60일 저점·고점, 저점 이후 경과일
    w = df.iloc[-120:]
    lo_d = w["Low"].idxmin()
    f["R01"] = {"low_120d": r(w["Low"].min()), "low_date": str(lo_d.date()),
                "days_since_low": int(len(df.loc[lo_d:]) - 1),
                "from_low_pct": r((px / w["Low"].min() - 1) * 100, 1),
                "high_480d": r(df["High"].iloc[-480:].max()),
                "drawdown_from_480d_high_pct": r((px / df["High"].iloc[-480:].max() - 1) * 100, 1)}
    # 유동성 (R12): 최근 20일 평균 거래대금(원)
    f["R12_avg_value_20d_won"] = r((df["Close"] * df["Volume"]).iloc[-20:].mean())
    f["last15"] = [{"d": str(d.date())[5:], "o": r(b.Open), "h": r(b.High), "l": r(b.Low), "c": r(b.Close),
                    "v": r(b.Volume), "ma5": r(b.MA5), "ma20": r(b.MA20), "ma60": r(b.MA60), "ma112": r(b.MA112),
                    "ma224": r(b.MA224)} for d, b in df.iloc[-15:].iterrows()]
    return f


def main(names):
    res = {n: features(n) for n in names}
    out = ROOT / "reports" / "rules.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print(out)


if __name__ == "__main__":
    main(sys.argv[1:] or ["원일특강", "오상헬스케어", "그린생명과학", "이건홀딩스", "블루엠텍"])
