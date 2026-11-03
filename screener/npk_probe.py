"""엔피케이(048830) 즉석 확인: 일봉 이평·거래량, 오늘 분봉(정규장/넥스트레이드), 실시간 원문, 투자자 동향."""
import json, re, requests
import pandas as pd
C = "048830"; H = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}
def get(u):
    try:
        r = requests.get(u, headers=H, timeout=10); print("##", u, r.status_code); return r
    except Exception as e:
        print("##", u, "fail", e); return None
for u in (f"https://polling.finance.naver.com/api/realtime/domestic/stock/{C}",
          f"https://m.stock.naver.com/api/stock/{C}/basic",
          f"https://m.stock.naver.com/api/stock/{C}/integration",
          f"https://m.stock.naver.com/api/stock/{C}/price?pageSize=10&page=1",
          f"https://m.stock.naver.com/api/stock/{C}/trend?pageSize=10",
          f"https://m.stock.naver.com/api/news/stock/{C}?pageSize=10&page=1"):
    r = get(u)
    if r is not None: print(r.text[:4000])
r = get(f"https://fchart.stock.naver.com/sise.nhn?symbol={C}&timeframe=day&count=600&requestType=0")
rows = re.findall(r'data="([^"]+)"', r.text)
df = pd.DataFrame([x.split("|") for x in rows], columns=["d","o","h","l","c","v"]).set_index("d").astype(float)
for n in (5, 20, 60, 112, 224, 256, 448): df[f"m{n}"] = df.c.rolling(n).mean()
df["vm20"] = df.v.rolling(20).mean()
pd.set_option("display.width", 250)
print(df.tail(40).round(0).to_string())
print("52주 고저", df.h.tail(250).max(), df.l.tail(250).min(), "| 600일 고저", df.h.max(), df.l.min())
big = df[df.v > 3 * df.vm20].tail(15); print("거래량 3배 이상 봉\n", big[["o","h","l","c","v","vm20"]].round(0).to_string())
for tf in ("minute",):
    r = get(f"https://fchart.stock.naver.com/sise.nhn?symbol={C}&timeframe={tf}&count=700&requestType=0")
    rows = re.findall(r'data="([^"]+)"', r.text)
    print(len(rows)); print("\n".join(rows[-450:]))
for u in (f"https://api.stock.naver.com/chart/domestic/item/{C}/minute?periodType=dayCandle",
          f"https://api.stock.naver.com/chart/domestic/item/{C}?periodType=minute&count=500",
          f"https://polling.finance.naver.com/api/realtime/domestic/stock/{C}?marketType=NXT",
          f"https://m.stock.naver.com/api/stock/{C}/askingPrice"):
    r = get(u)
    if r is not None: print(r.text[:6000])
