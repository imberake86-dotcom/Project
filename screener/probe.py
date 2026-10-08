"""종목 하나를 즉석에서 확인: 네이버 실시간 시세(정규장·넥스트레이드 구분)와 최근 일봉·이평선을 출력한다.

사용: python screener/probe.py 원일특강 [종목명 ...]   (Actions probe.yml 에서 workflow_dispatch 로 실행)
"""
import json
import sys

import requests

from dante import MAS, find_code, load_prices


def realtime(code):
    for url in (f"https://polling.finance.naver.com/api/realtime/domestic/stock/{code}",
                f"https://m.stock.naver.com/api/stock/{code}/basic"):
        try:
            print(url)
            print(json.dumps(requests.get(url, timeout=5).json(), ensure_ascii=False))
        except Exception as e:
            print(f"failed: {e!r}")


def main(names):
    for name in names:
        code = find_code(name)
        print(f"=== {name} {code}")
        realtime(code)
        df = load_prices(code)
        cols = ["Open", "High", "Low", "Close", "Volume"] + [f"MA{n}" for n in MAS] + ["VMA20"]
        print(df[cols].tail(25).round(0).to_string())


if __name__ == "__main__":
    main(sys.argv[1:])
