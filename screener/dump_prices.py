"""관심 종목의 일봉(최근 3년)을 data/prices/<종목명>.csv 로 저장한다.

클라우드 세션은 시세 사이트에 접근할 수 없으므로, Actions 에서 받은 일봉을
저장소에 남겨 두고 분석은 이 CSV 로 한다.
"""
import sys

from dante import ROOT, WATCHLIST, find_code, load_prices


def main(names):
    out = ROOT / "data" / "prices"
    out.mkdir(parents=True, exist_ok=True)
    for name in names:
        code = find_code(name)
        df = load_prices(code)[["Open", "High", "Low", "Close", "Volume"]]
        df.to_csv(out / f"{name}.csv", index_label="Date")
        print(name, code, len(df), df.index[-1].date())


if __name__ == "__main__":
    main(sys.argv[1:] or WATCHLIST)
