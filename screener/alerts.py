"""종목별 타점 진입 알림 조건 점검 (장중 30분 간격).

dante.py 판정에서 '대기'로 둔 자리를 이평선 기준 가격 조건으로 감시한다.
이평선은 오늘 봉(장중이면 현재가)을 포함해 매번 다시 계산한다.

결과: reports/alerts.json  (발생한 조건만 기록, 바뀔 때만 커밋됨)
"""
import json

from dante import ROOT, find_code, load_prices


def g(t, k):
    return float(t[k])


# 종목 → [(조건 이름, 판정 함수(t) -> bool, 기준선 컬럼, 손절/참고 메모)]
RULES = {
    "그린생명과학": [
        ("224선 돌파 (112·224·밥그릇 3번)", lambda t: t["Close"] > t["MA224"], "MA224", "손절: 종가 224선 이탈"),
        ("112선 눌림 지지", lambda t: t["MA112"] < t["Close"] <= t["MA112"] * 1.03 and t["Low"] <= t["MA112"] * 1.02,
         "MA112", "손절: 종가 112선 이탈"),
    ],
    "오상헬스케어": [
        ("112선 돌파 (역매공파 확인)", lambda t: t["Close"] > t["MA112"] and t["Close"] > t["MA60"], "MA112",
         "손절: 60선 이탈, 1차 목표 224선"),
        ("⚠ 60선 이탈 (손절 경고)", lambda t: t["Close"] < t["MA60"], "MA60", "역매공파 시나리오 무효"),
    ],
    "블루엠텍": [
        ("224선 돌파 (112·224)", lambda t: t["Close"] > t["MA224"], "MA224", "손절: 종가 224선 이탈 또는 112선 이탈"),
    ],
    "이건홀딩스": [
        ("112선 거래량 돌파 (역매공파)", lambda t: t["Close"] > t["MA112"] and t["Volume"] >= 1.5 * t["VMA20"], "MA112",
         "손절: 60선 이탈, 1차 목표 224선"),
    ],
}


def main():
    triggers, levels, date = [], {}, None
    for name, rules in RULES.items():
        code = find_code(name)
        df = load_prices(code)
        t = df.iloc[-1]
        date = str(df.index[-1].date())
        vol_x = round(g(t, "Volume") / g(t, "VMA20"), 1)
        levels[name] = {"price": g(t, "Close"), "vol_x": vol_x}
        for rule, fn, col, note in rules:
            levels[name][col] = round(g(t, col))
            if fn(t):
                triggers.append({"name": name, "code": code, "rule": rule, "price": g(t, "Close"),
                                 "level": round(g(t, col)), "vol_x": vol_x, "note": note})
    out = ROOT / "reports"
    out.mkdir(parents=True, exist_ok=True)
    # 오늘 처음 충족된 조건만 골라 푸시 알림용으로 따로 남긴다 (커밋하지 않음)
    seen = set()
    prev = out / "alerts.json"
    if prev.exists():
        old = json.loads(prev.read_text())
        if old.get("date") == date:
            seen = {(x["name"], x["rule"]) for x in old["triggers"]}
    new = [x for x in triggers if (x["name"], x["rule"]) not in seen]
    (out / "new_alerts.json").write_text(json.dumps(new, ensure_ascii=False))
    (out / "alerts.json").write_text(json.dumps({"date": date, "triggers": triggers}, ensure_ascii=False, indent=1))
    print(json.dumps({"date": date, "triggers": triggers, "levels": levels}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
