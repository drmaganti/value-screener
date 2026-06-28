"""
refresh_universe.py - builds universe.json from index constituents.

Pulls S&P 500, NASDAQ-100, and S&P/TSX 60 members and writes them to
universe.json, which value_screener.py reads. Run this weekly (the workflow does
it before the scan) or periodically; indices rebalance, so a stale list slowly
drifts. Defensive: if one source fails, the others still write.

NOTE: this fetches from Wikipedia, which needs open network (works on the GitHub
Actions runner; will NOT work behind a restricted sandbox). yfinance ticker
conventions: TSX names get a .TO suffix; dotted US tickers (BRK.B) become BRK-B.
"""
import json
from datetime import date

WIKI = {
    "sp500": ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", 0, "Symbol"),
    "ndx":   ("https://en.wikipedia.org/wiki/Nasdaq-100", 4, "Ticker"),
    "tsx60": ("https://en.wikipedia.org/wiki/S%26P/TSX_60", 0, "Symbol"),
}


def _fetch(url, table_idx, col):
    import pandas as pd
    tables = pd.read_html(url)
    # Find the table that actually has the expected column, near the hinted index.
    for idx in [table_idx] + list(range(len(tables))):
        df = tables[idx]
        cols = [str(c) for c in df.columns]
        match = next((c for c in cols if col.lower() in c.lower()), None)
        if match:
            return [str(s).strip() for s in df[match].tolist()]
    raise ValueError(f"no column like '{col}' at {url}")


def _us(t):
    return t.replace(".", "-").upper()          # BRK.B -> BRK-B for yfinance


def _tsx(t):
    t = t.replace(".", "-").upper()
    return t if t.endswith(".TO") else f"{t}.TO"


def build():
    universe = {"NYSE/NASDAQ": [], "TSX": [], "as_of": date.today().isoformat()}
    us = set()
    for key in ("sp500", "ndx"):
        url, idx, col = WIKI[key]
        try:
            us.update(_us(t) for t in _fetch(url, idx, col) if t and t.lower() != "nan")
            print(f"  {key}: ok ({len(us)} US names so far)")
        except Exception as e:
            print(f"  {key}: FAILED ({type(e).__name__}: {e})")
    universe["NYSE/NASDAQ"] = sorted(us)

    try:
        url, idx, col = WIKI["tsx60"]
        universe["TSX"] = sorted({_tsx(t) for t in _fetch(url, idx, col) if t and t.lower() != "nan"})
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
