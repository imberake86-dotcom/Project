"""종목 하나를 즉석에서 확인: 네이버 실시간 시세(정규장·넥스트레이드 구분), 최근 뉴스·공시 제목, 최근 일봉·이평선을 출력한다.

사용: python screener/probe.py 원일특강 [종목명 ...]   (Actions probe.yml 에서 workflow_dispatch 로 실행)
"""
import json
import os
import re
import sys

import requests

from dante import MAS, find_code, krx_listing, load_prices


def realtime(code):
    for url in (f"https://polling.finance.naver.com/api/realtime/domestic/stock/{code}",
                f"https://m.stock.naver.com/api/stock/{code}/basic"):
        try:
            print(url)
            j = requests.get(url, timeout=5).json()
            print(json.dumps(j, ensure_ascii=False))
            d = (j.get("datas") or [j])[0]
            num = lambda k: int(str(d.get(k, "0")).replace(",", ""))
            print("전일 정규장 종가(기준가):", num("closePrice") - num("compareToPreviousClosePrice"))
        except Exception as e:
            print(f"failed: {e!r}")


def news(code):
    """최근 뉴스·공시 제목 (네이버). 실패해도 계속 진행."""
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


def naver_daily(code):
    # 네이버 일별 시세(모바일 API)가 정규장 종가인지 통합 종가인지 대조용으로 원문을 찍는다.
    for url in (f"https://m.stock.naver.com/api/stock/{code}/price?pageSize=3&page=1",
                f"https://m.stock.naver.com/api/stock/{code}/integration"):
        try:
            r = requests.get(url, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
            print(url, r.status_code)
            print(r.text[:3000])
        except Exception as e:
            print(f"failed: {e!r}")


def krx_close(code):
    # 정규장 종가 대조용. 네이버 일봉·fchart에는 넥스트레이드 시간외가 섞인다(10/8 오상 6,780 vs 6,560).
    # StockListing("KRX")는 FinanceData 깃허브 캐시 CSV라 장중 스냅샷일 수 있다(10/12 20:52에 그린 2,360, 실제 2,305).
    # 확실한 전일 정규장 종가는 다음 날 네이버 실시간의 closePrice - compareToPreviousClosePrice(기준가)로 본다.
    try:
        row = krx_listing().set_index("Code").loc[code]
        print("KRX 상장목록 캐시(스냅샷일 수 있음):", {k: row[k] for k in ("Open", "High", "Low", "Close", "Changes", "ChagesRatio", "Volume") if k in row})
    except Exception as e:
        print(f"KRX listing failed: {e!r}")


def main(names):
    for name in names:
        code = name if name.isdigit() and len(name) == 6 else find_code(name)
        print(f"=== {name} {code}")
        realtime(code)
        news(code)
        krx_close(code)
        naver_daily(code)
        df = load_prices(code)
        cols = ["Open", "High", "Low", "Close", "Volume"] + [f"MA{n}" for n in MAS] + ["VMA20"]
        print(df[cols].tail(int(os.environ.get("PROBE_ROWS", "25"))).round(0).to_string())


if __name__ == "__main__":
    main(sys.argv[1:])
