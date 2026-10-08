"""종목 하나를 즉석에서 확인: 네이버 실시간 시세(정규장·넥스트레이드 구분), 최근 뉴스·공시 제목, 최근 일봉·이평선을 출력한다.

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


def news(code):
    """최근 뉴스·공시 제목 (네이버). 실패해도 계속 진행."""
    import re
    for url in (f"https://m.stock.naver.com/api/news/stock/{code}?pageSize=15&page=1",
                f"https://finance.naver.com/item/news_notice.naver?code={code}&page=1",
                f"https://finance.naver.com/item/news_news.naver?code={code}&page=1"):
        try:
            r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"})
            print(url, r.status_code)
            if "json" in r.headers.get("content-type", ""):
                for g in r.json():
                    for it in g.get("items", [g]):
                        print(" ", it.get("datetime"), it.get("officeName"), it.get("title"))
            else:
                r.encoding = r.apparent_encoding or "euc-kr"
                rows = re.findall(r'class="tit"[^>]*>(.*?)</a>.*?class="info"[^>]*>(.*?)<.*?class="date"[^>]*>(.*?)<', r.text, re.S)
                for t, info, d in rows[:15]:
                    print(" ", d.strip(), info.strip(), re.sub(r"<[^>]+>|\s+", " ", t).strip())
                if not rows:
                    print(" (no rows parsed)", re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r.text))[:1500])
        except Exception as e:
            print(f"failed: {e!r}")


def main(names):
    for name in names:
        code = find_code(name)
        print(f"=== {name} {code}")
        realtime(code)
        news(code)
        df = load_prices(code)
        cols = ["Open", "High", "Low", "Close", "Volume"] + [f"MA{n}" for n in MAS] + ["VMA20"]
        print(df[cols].tail(25).round(0).to_string())


if __name__ == "__main__":
    main(sys.argv[1:])
