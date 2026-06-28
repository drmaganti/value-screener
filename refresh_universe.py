"""
refresh_universe.py - builds universe.json from index constituents.

Pulls S&P 500, NASDAQ-100, and S&P/TSX 60 members and writes them to
universe.json, which value_screener.py reads. Run weekly (the workflow does it
before the scan) or periodically; indices rebalance, so a stale list drifts.

Fetches via requests with a real User-Agent header -- Wikipedia returns HTTP 403
to bare/default user-agents, so we fetch the page ourselves and hand the HTML to
pandas rather than letting pandas fetch it. Defensive: if one source fails, the
others still write. yfinance conventions: TSX names get .TO; dotted US tickers
(BRK.B) become BRK-B.
"""
import json
import time
from datetime import date
from io import StringIO

# A descriptive User-Agent (Wikipedia etiquette) that won't be 403'd.
HEADERS = {"User-Agent": "ValueScreener/1.0 (personal stock screener; +https://github.com)"}

# url -> (hinted table index, candidate column names to look for)
WIKI = {
    "sp500": ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", 0, ["Symbol", "Ticker"]),
    "ndx":   ("https://en.wikipedia.org/wiki/Nasdaq-100", 4, ["Ticker", "Symbol"]),
    "tsx60": ("https://en.wikipedia.org/wiki/S%26P/TSX_60", 0, ["Symbol", "Ticker"]),
}


def _fetch(url, table_idx, cols):
    import pandas as pd
    import requests
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()                       # surfaces a clear error if still blocked
    tables = pd.read_html(StringIO(resp.text))
    # Find the table that actually has one of the expected columns, near the hint.
    order = [table_idx] + [i for i in range(len(tables)) if i != table_idx]
    for idx in order:
        if idx >= len(tables):
            continue
        df = tables[idx]
        names = [str(c) for c in df.columns]
        for want in cols:
            match = next((c for c in names if want.lower() == c.lower()), None) \
                or next((c for c in names if want.lower() in c.lower()), None)
            if match:
                return [str(s).strip() for s in df[match].tolist()]
    raise ValueError(f"no column like {cols} found at {url}")


def _us(t):
    return t.replace(".", "-").upper()            # BRK.B -> BRK-B for yfinance


def _tsx(t):
    t = t.replace(".", "-").upper()
    return t if t.endswith(".TO") else f"{t}.TO"


def build():
    universe = {"NYSE/NASDAQ": [], "TSX": [], "as_of": date.today().isoformat()}
    us = set()
    for key in ("sp500", "ndx"):
        url, idx, cols = WIKI[key]
        try:
            us.update(_us(t) for t in _fetch(url, idx, cols) if t and t.lower() != "nan")
            print(f"  {key}: ok ({len(us)} US names so far)")
        except Exception as e:
            print(f"  {key}: FAILED ({type(e).__name__}: {e})")
        time.sleep(1)                             # be polite between requests
    universe["NYSE/NASDAQ"] = sorted(us)

    try:
        url, idx, cols = WIKI["tsx60"]
        universe["TSX"] = sorted({_tsx(t) for t in _fetch(url, idx, cols) if t and t.lower() != "nan"})
        print(f"  tsx60: ok ({len(universe['TSX'])} names)")
    except Exception as e:
        print(f"  tsx60: FAILED ({type(e).__name__}: {e})")

    total = len(universe["NYSE/NASDAQ"]) + len(universe["TSX"])
    if total < 100:
        print(f"  WARNING: only {total} tickers fetched; not overwriting universe.json")
        return
    with open("universe.json", "w") as fh:
        json.dump(universe, fh, indent=2)
    print(f"Wrote universe.json with {total} tickers ({universe['as_of']}).")


if __name__ == "__main__":
    build()
