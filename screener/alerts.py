"""종목별 타점 진입 알림 조건 (watch.py 가 장중 1분마다 쓰고, 이 파일을 직접 실행하면 한 번 점검).

dante.py 판정에서 '대기'로 둔 자리를 이평선 기준 가격 조건으로 감시한다.
이평선은 오늘 봉(장중이면 현재가)을 포함해 매번 다시 계산한다.

결과: reports/alerts.json  (발생한 조건만 기록, 바뀔 때만 커밋됨)
"""
import json

from dante import ROOT, find_code, load_prices


def g(t, k):
    return float(t[k])


# 종목 → [(조건 이름, 판정 함수(t) -> bool, 기준선 컬럼 또는 고정 가격, 손절/참고 메모)]
# 2026-10-07 300편 규칙(R01~R14) 판정 기준. 근거: /mnt/project-files/reports/주식단테_300편규칙_타점_2026-10-07.md
# 장중 현재가 기준이라 대부분 종가로 다시 확인해야 한다. 원일특강은 거래가 얇아 장중 알림이 매일 울려 제외(장 마감 점검에서만 본다).
RULES = {
    "그린생명과학": [
        ("첫 눌림 구간 도달 (2,400 이하, 기준봉 시가 2,335 위)", lambda t: t["Low"] <= 2400 and t["Close"] > 2335, 2400,
         "바로 사지 않음. 2,335 위 지지·거래량 감소·반등봉 확인 뒤 2,400 이하 분할 진입 검토. 손절 2,330, 1차 목표 2,480"),
        ("⚠ 기준봉 시가 2,335 이탈 (실패)", lambda t: t["Close"] < 2335, 2335, "미진입이면 시나리오 폐기, 보유 중이면 손절 (R04)"),
        ("224선 돌파 관찰 (매수 신호 아님)", lambda t: t["Close"] > t["MA224"], "MA224", "종가로 확인. 보유 중이면 3차 목표 구간"),
    ],
    "블루엠텍": [
        ("224선 도달 (종가 224선 위 + 고가 3,280 돌파 확인 필요)", lambda t: t["Close"] > t["MA224"], "MA224",
         "돌파봉 추격 금지. 다음 세션 이후 224선 위 눌림 지지 때 3,250 부근 검토. 손절 종가 224선 이탈, 1차 목표 3,370"),
        # 10/8 장 마감 뒤 최대주주 변경·360억 CB 공시로 시간외 2,230까지 급락해 2,830·2,705 기준은 이미 깨졌다.
        ("⚠ 112선 이탈 (10/8 공시 급락 뒤 마지막 지지)", lambda t: t["Close"] < t["MA112"], "MA112",
         "10/8 시간외 저가 2,230과 112선 아래면 60선(약 2,040)까지 열림. 떨어지는 칼 매수 금지 (R11·R14), 종가로 확인"),
    ],
    "오상헬스케어": [
        ("112선 회복 관찰", lambda t: t["Close"] > t["MA112"], "MA112",
         "장중 터치는 진입 아님. 종가 112선 위 + 5선의 112선 상향 교차 + 다음 날 유지 시 6,900 이하에서 검토. 손절 종가 112선 이탈(약 6,750), 1차 목표 7,100"),
        ("추격 경계 6,960 이상", lambda t: t["Close"] >= 6960, 6960, "6,910~6,960 매물 위 급등 중이면 사지 않음 (R11·R14)"),
        ("⚠ 대기 무효 6,440 이하", lambda t: t["Close"] < 6450, 6450, "10/7 시가 6,450과 5·20·60 수렴대 이탈. 대기 계획 무효"),
    ],
    "이건홀딩스": [
        ("112선·2,760 돌파 (후보 편입)", lambda t: t["Close"] > max(t["MA112"], 2760), "MA112",
         "돌파봉은 따라 사지 않음. 거래량 증가 확인, 첫 눌림에서 2,760 위 종가 지지 시 검토. 손절 종가 2,750, 1차 목표 2,920"),
        ("⚠ 9/23 대량봉 시가 2,510 이탈", lambda t: t["Close"] < 2510, 2510, "지지 기준 이탈 경고. 종가로 확인"),
    ],
}


def level(t, col):
    return round(float(t[col])) if isinstance(col, str) else col


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
            levels[name][str(col)] = level(t, col)
            if fn(t):
                triggers.append({"name": name, "code": code, "rule": rule, "price": g(t, "Close"),
                                 "level": level(t, col), "vol_x": vol_x, "note": note})
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
