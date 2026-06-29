"""
value_screener.py - agentic value-stock screener (Phase 1: weekly, tracked)

WHAT THIS IS
    A research/screening tool. Each WEEK it scans a large blue-chip universe
    (S&P 500 + NASDAQ-100 + TSX 60), funnels to names that have pulled back and
    look cheap, healthy, and down for a transient reason, scores them, logs them,
    tracks how they do over time, and emails a modern HTML digest. YOU make the
    buy decisions; this is not investment advice.

PHASE 1 PIPELINE (per weekly run)
    load pick log
      -> update outcomes on past picks whose horizons have matured (vs SPY)
      -> exclude names that are still open bets (dedup)
      -> paced batch price scan across the whole universe (slow + polite)
      -> funnel: keep corrected names, enrich + classify + score the survivors
      -> keep qualifiers scoring >= MIN_COMPOSITE
      -> top 3 get a written ~200-word analysis
      -> log this run's picks, build + send the HTML email, save the log

DATA ACCESS is behind providers so you can start free (yfinance) and swap in a
paid source later. A SyntheticProvider runs the whole thing offline for testing.
"""

from __future__ import annotations

import json
import math
import os
import random
import time
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from typing import Optional, Protocol


# ════════════════════════════════════════════════════════════════════════════
# CONFIG
# ════════════════════════════════════════════════════════════════════════════

PROVIDER = os.getenv("PROVIDER", "synthetic")     # "synthetic" | "yfinance"
CLASSIFIER = os.getenv("CLASSIFIER", "mock")      # "mock" | "groq" | "anthropic"

UNIVERSE_FILE = "universe.json"     # produced by refresh_universe.py; seed fallback below
PICKS_LOG = "picks_log.json"        # the outcome log (committed back each run)
FUND_CACHE = "fundamentals_cache.json"  # rolling fundamentals store -> sector medians (committed back)
CHECKPOINT = "scan_checkpoint.json" # lets a long scan resume if the run restarts
STATE_DIR = os.getenv("STATE_DIR", ".")

BENCHMARK = "SPY"                   # measure every pick against this

THRESHOLDS = {
    "min_pullback_pct":   0.10,    # >=10% off 52w high to count as "corrected"
    "rsi_oversold":       35,
    "min_market_cap_b":   10,      # blue-chip floor ($B)
    "min_quality_score":  4,       # of 9; hard veto below this (trap guard)
    "min_composite":      60,      # THE value-buy bar. Moderate; lower = more picks.
    "earnings_blackout":  3,       # skip if earnings within N days
    "open_window_days":   30,      # a pick stays an "open bet" this long -> not re-picked
    "max_picks_per_run":  10,      # how many qualifiers to log per run
    "featured":           3,       # how many get the written analysis (top N)
    "fund_staleness_days": 21,     # refresh a fundamentals cache entry once older than this
    "max_fundamentals_refresh": 200,  # cap fundamentals fetches per run (spreads 600 over ~3 weeks)
    "min_sector_count":   5,       # need this many cached names in a sector to trust its median
}

# Outcome horizons (days). Returns get filled in as each matures.
HORIZONS = {"1w": 7, "1m": 30, "3m": 90, "6m": 180}

# Paced scanning (only applied with live data, to stay under Yahoo's throttle).
SCAN = {"batch_size": 25, "sleep_between_batches": 2.0, "max_retries": 3, "retry_sleep": 5.0}

WEIGHTS = {"technical": 0.20, "valuation": 0.30, "quality": 0.25,
           "catalyst": 0.15, "sentiment": 0.10}

# Seed universe used in synthetic mode or if universe.json is missing.
SEED_UNIVERSE = {
    "NYSE/NASDAQ": ["AAPL", "MSFT", "JNJ", "PG", "KO", "PEP", "HD", "V", "JPM",
                    "DIS", "NKE", "MCD", "CSCO", "INTC", "VZ", "ABBV", "MRK",
                    "CVX", "WMT", "UNH"],
    "TSX":         ["RY.TO", "TD.TO", "ENB.TO", "BNS.TO", "CNR.TO", "BCE.TO", "SU.TO"],
}


def _p(name):
    return os.path.join(STATE_DIR, name)


# ════════════════════════════════════════════════════════════════════════════
# DATA MODELS
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class Fundamentals:
    ticker: str; name: str; exchange: str; market_cap_b: float; sector: str
    pe: float; pe_5y_low: Optional[float]; pe_5y_high: Optional[float]
    pb: float; ev_ebitda: float; ps: float; fcf: float          # ps = price/sales; fcf = free cash flow ($)
    fcf_positive: bool; net_income_positive: bool; op_cash_flow_positive: bool
    roe: float; debt_to_equity: float; interest_coverage: float; current_ratio: float
    gross_margin: float; margin_trend_up: bool
    dividend_yield: float; div_yield_5y_avg: Optional[float]; payout_ratio: float
    next_earnings: Optional[date]
    price: float = float("nan")                                  # current price (for the upside calc)
    target_mean: float = float("nan"); target_high: float = float("nan")
    target_low: float = float("nan"); num_analysts: int = 0      # consensus analyst price targets


@dataclass
class PriceHistory:
    ticker: str; closes: list[float]
    @property
    def last(self): return self.closes[-1]
    @property
    def high_52w(self): return max(self.closes[-252:]) if self.closes else self.last


@dataclass
class TechnicalSignals:
    pullback_pct: float; rsi: float; above_200ma: bool; below_50ma: bool
    days_in_decline: int = 0          # how long it's been falling (reversal vs momentum)
    score: float = 0.0


@dataclass
class ValuationSignals:
    score: float = 0.0                              # blended 0-100 (cheaper = higher)
    cheap: bool = False
    parts: dict = field(default_factory=dict)       # metric name -> sub-score 0-100 (data-permitting)
    summary: str = ""                               # short text for the email
    pe_percentile: float = float("nan")             # kept for the P/E-vs-history method + display
    yield_vs_norm: float = float("nan")             # kept for the yield-vs-history method + display


@dataclass
class QualitySignals:
    fscore: int; healthy: bool; notes: list[str] = field(default_factory=list); score: float = 0.0


@dataclass
class CatalystVerdict:
    category: str; transient: bool; reason: str; source: str
    veto: bool = False; score: float = 0.0


@dataclass
class Candidate:
    fund: Fundamentals; tech: TechnicalSignals
    val: Optional[ValuationSignals] = None
    qual: Optional[QualitySignals] = None
    cat: Optional[CatalystVerdict] = None
    composite: float = 0.0
    vetoed_for: Optional[str] = None
    analysis: str = ""                # the ~200-word writeup (featured picks only)


def _nan(x): return x is None or (isinstance(x, float) and math.isnan(x))
def _clamp(x, lo=0.0, hi=1.0): return max(lo, min(hi, x))


# ════════════════════════════════════════════════════════════════════════════
# DATA PROVIDERS
# ════════════════════════════════════════════════════════════════════════════

class DataProvider(Protocol):
    def prices(self, ticker: str) -> PriceHistory: ...
    def fundamentals(self, ticker: str) -> Fundamentals: ...
    def recent_news(self, ticker: str) -> list[str]: ...


class YFinanceProvider:
    """Live data via yfinance (free). Defensive: field availability varies, so
    every access falls back gracefully rather than killing the weekly run."""

    def __init__(self):
        import yfinance as yf
        self.yf = yf
        self._cache = {}

    def _t(self, ticker):
        if ticker not in self._cache:
            self._cache[ticker] = self.yf.Ticker(ticker)
        return self._cache[ticker]

    def prices(self, ticker):
        hist = self._t(ticker).history(period="1y")
        return PriceHistory(ticker, [float(x) for x in hist["Close"].values])

    def last_price(self, ticker):
        try:
            h = self._t(ticker).history(period="5d")
            return float(h["Close"].values[-1])
        except Exception:
            return None

    def _earnings_date(self, t):
        try:
            cal = t.calendar
            ed = cal.get("Earnings Date") if isinstance(cal, dict) else None
            if ed:
                d = ed[0] if isinstance(ed, (list, tuple)) else ed
                return d if isinstance(d, date) else None
        except Exception:
            pass
        return None

    def _margin_trend(self, t):
        try:
            fin = t.financials
            rev, gp = fin.loc["Total Revenue"], fin.loc["Gross Profit"]
            return bool(gp.iloc[0] / rev.iloc[0] > gp.iloc[1] / rev.iloc[1])
        except Exception:
            return False

    def fundamentals(self, ticker):
        t = self._t(ticker)
        try:
            info = t.info or {}
        except Exception:
            info = {}
        def g(k, d=float("nan")):
            v = info.get(k)
            return d if v is None else v
        return Fundamentals(
            ticker=ticker, name=info.get("shortName", ticker),
            exchange=info.get("fullExchangeName", "TSX" if ticker.endswith(".TO") else "?"),
            market_cap_b=(g("marketCap", 0) or 0) / 1e9, sector=info.get("sector", "?"),
            pe=g("trailingPE"), pe_5y_low=None, pe_5y_high=None,
            pb=g("priceToBook"), ev_ebitda=g("enterpriseToEbitda"),
            ps=g("priceToSalesTrailing12Months"), fcf=g("freeCashflow"),
            fcf_positive=(g("freeCashflow", 0) or 0) > 0,
            net_income_positive=(g("netIncomeToCommon", 0) or 0) > 0,
            op_cash_flow_positive=(g("operatingCashflow", 0) or 0) > 0,
            roe=(g("returnOnEquity", 0) or 0) * 100, debt_to_equity=g("debtToEquity"),
            interest_coverage=float("nan"), current_ratio=g("currentRatio"),
            gross_margin=(g("grossMargins", 0) or 0) * 100, margin_trend_up=self._margin_trend(t),
            dividend_yield=(g("dividendYield", 0) or 0) * 100,
            div_yield_5y_avg=info.get("fiveYearAvgDividendYield"),
            payout_ratio=(g("payoutRatio", 0) or 0) * 100, next_earnings=self._earnings_date(t),
            price=g("currentPrice"), target_mean=g("targetMeanPrice"),
            target_high=g("targetHighPrice"), target_low=g("targetLowPrice"),
            num_analysts=int(g("numberOfAnalystOpinions", 0) or 0))

    def recent_news(self, ticker):
        try:
            items = self._t(ticker).news or []
            titles = [(n.get("title") or n.get("content", {}).get("title", "")) for n in items[:8]]
            return [x for x in titles if x] or ["No recent headlines retrieved"]
        except Exception:
            return ["No recent headlines retrieved"]


class SyntheticProvider:
    """Offline data so the whole pipeline runs with no network/keys."""

    ARCHETYPES = {
        "JNJ": ("clean_buy", "Sector-wide selloff drags healthcare names lower"),
        "PG": ("clean_buy", "Broad market dip on rate fears hits consumer staples"),
        "KO": ("clean_buy", "Defensive names slip in risk-off session"),
        "MCD": ("clean_buy", "Fast food stocks pull back on consumer spending worry"),
        "PEP": ("clean_buy", "Staples retreat as market rotates to growth"),
        "INTC": ("value_trap", "Intel cuts full-year guidance, flags share loss"),
        "VZ": ("value_trap", "Verizon faces lawsuit over legacy lead cables"),
        "DIS": ("mild", "Disney slips after mixed quarterly earnings"),
    }

    def __init__(self, seed=7): self.seed = seed
    def _rng(self, ticker): return random.Random(f"{self.seed}-{ticker}")

    def prices(self, ticker):
        r = self._rng(ticker)
        kind = self.ARCHETYPES.get(ticker, ("normal", ""))[0]
        n, base = 252, r.uniform(50, 300)
        closes = [base]; drift = r.uniform(0.0002, 0.0006)
        for _ in range(n - 1):
            closes.append(closes[-1] * (1 + r.gauss(drift, 0.012)))
        if kind in ("clean_buy", "value_trap", "mild"):
            depth = r.uniform(0.12, 0.20)
            for i in range(n - 20, n):
                closes[i] *= (1 - depth * ((i - (n - 20)) / 20))
        return PriceHistory(ticker, closes)

    def last_price(self, ticker):
        # Simulate some forward drift so logged outcomes aren't all flat in demos.
        r = self._rng(ticker + "-fwd")
        return self.prices(ticker).last * (1 + r.gauss(0.01, 0.06))

    def fundamentals(self, ticker):
        r = self._rng(ticker)
        kind = self.ARCHETYPES.get(ticker, ("normal", ""))[0]
        healthy = kind != "value_trap"
        pe = r.uniform(11, 18) if kind == "clean_buy" else r.uniform(9, 26)
        yld = r.uniform(2.0, 4.5)
        pays_div = r.random() > 0.35                      # ~1/3 of names pay no dividend
        mcap = r.uniform(15, 400)
        return Fundamentals(
            ticker=ticker, name=f"{ticker} Inc.",
            exchange="Toronto" if ticker.endswith(".TO") else "NASDAQ/NYSE",
            market_cap_b=mcap,
            sector=r.choice(["Consumer Staples", "Healthcare", "Technology", "Financials", "Communications"]),
            pe=pe, pe_5y_low=pe * r.uniform(0.75, 0.9), pe_5y_high=pe * r.uniform(1.4, 2.0),
            pb=r.uniform(1.5, 6), ev_ebitda=r.uniform(7, 16),
            ps=r.uniform(1.0, 9.0),
            fcf=mcap * 1e9 * (r.uniform(0.03, 0.08) if healthy else r.uniform(-0.02, 0.02)),
            fcf_positive=healthy, net_income_positive=healthy,
            op_cash_flow_positive=healthy or r.random() > 0.5,
            roe=r.uniform(14, 30) if healthy else r.uniform(-5, 8),
            debt_to_equity=r.uniform(30, 90) if healthy else r.uniform(140, 260),
            interest_coverage=r.uniform(6, 20) if healthy else r.uniform(0.8, 3),
            current_ratio=r.uniform(1.1, 2.2) if healthy else r.uniform(0.6, 1.0),
            gross_margin=r.uniform(35, 60), margin_trend_up=healthy,
            dividend_yield=yld if pays_div else 0.0,
            div_yield_5y_avg=(yld * r.uniform(0.82, 0.94)) if pays_div else None,
            payout_ratio=(r.uniform(35, 60) if healthy else r.uniform(85, 130)) if pays_div else 0.0,
            next_earnings=date.today() + timedelta(days=r.randint(6, 60)),
            price=(px := r.uniform(50, 300)),
            target_mean=px * (1 + (up := r.uniform(-0.10, 0.40) if healthy else r.uniform(-0.20, 0.15))),
            target_high=px * (1 + up + r.uniform(0.05, 0.20)),
            target_low=px * (1 + up - r.uniform(0.05, 0.20)),
            num_analysts=r.randint(0, 25))

    def recent_news(self, ticker):
        head = self.ARCHETYPES.get(ticker, ("normal", "No major company-specific news"))[1]
        return [head, "Analysts weigh in on the move", "Volume elevated in session"]


# ════════════════════════════════════════════════════════════════════════════
# INDICATORS + LENSES
# ════════════════════════════════════════════════════════════════════════════

def _rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains = losses = 0.0
    for i in range(-period, 0):
        chg = closes[i] - closes[i - 1]
        gains += max(chg, 0); losses += max(-chg, 0)
    ag, al = gains / period, losses / period
    return 100.0 if al == 0 else 100 - (100 / (1 + ag / al))


def _ma(closes, window):
    w = closes[-window:] if len(closes) >= window else closes
    return sum(w) / len(w)


def _days_in_decline(closes):
    """How many days since the recent peak -- distinguishes a fresh dip (short-
    term reversal candidate) from a months-long slide (momentum loser)."""
    if not closes:
        return 0
    peak_idx = max(range(len(closes)), key=lambda i: closes[i])
    return len(closes) - 1 - peak_idx


def compute_technicals(p):
    pullback = (p.high_52w - p.last) / p.high_52w if p.high_52w else 0.0
    rsi = _rsi(p.closes)
    above_200 = p.last > _ma(p.closes, 200)
    below_50 = p.last < _ma(p.closes, 50)
    ddec = _days_in_decline(p.closes)
    raw = min(pullback / 0.25, 1.0) * 60 + max(0, (50 - rsi) / 50) * 40
    if not above_200:
        raw *= 0.5
    return TechnicalSignals(pullback, rsi, above_200, below_50, ddec, round(raw, 1))


def is_corrected(t):
    return t.pullback_pct >= THRESHOLDS["min_pullback_pct"] or t.rsi <= THRESHOLDS["rsi_oversold"]


def compute_valuation(f, sector_medians=None):
    """Adaptive multi-metric value score. Scores the stock on whichever metrics
    it has data for, then blends. Crucially this works for NON-DIVIDEND-PAYERS:
    earnings yield, FCF yield, EV/EBITDA, P/B and P/S don't need a dividend.

    When sector_medians is supplied, it also adds a SECTOR-RELATIVE sub-metric --
    the stock's multiples versus its own sector's median rather than an absolute
    band -- so a cheap-for-software name isn't unfairly beaten by a utility on raw
    P/E. This is the more meaningful comparison; the absolute bands remain as a
    sanity check on absolute level and a fallback for thin sectors.

    Every sub-score is logged so the factor leaderboard can later tell you which
    metrics (including vs-sector) actually predict returns, and you tune from
    there rather than trusting all of them equally."""
    parts = {}

    def band(value, lo, hi, valid=True):
        if not valid or _nan(value):
            return None
        return _clamp((value - lo) / (hi - lo)) if lo != hi else None

    # Universal multiples (work without dividends) -------------------------------
    if not _nan(f.pe) and f.pe > 0:                                  # earnings yield E/P
        s = band(1.0 / f.pe, 0.03, 0.10)
        if s is not None: parts["earnings_yield"] = round(s * 100, 1)
    mc = f.market_cap_b * 1e9 if f.market_cap_b else None
    if mc and not _nan(f.fcf):                                       # free cash flow yield
        s = band(f.fcf / mc, 0.02, 0.09)
        if s is not None: parts["fcf_yield"] = round(s * 100, 1)
    if not _nan(f.ev_ebitda) and f.ev_ebitda > 0:                    # EV/EBITDA (lower cheaper)
        s = band(f.ev_ebitda, 16.0, 7.0)
        if s is not None: parts["ev_ebitda"] = round(s * 100, 1)
    if not _nan(f.pb) and f.pb > 0:                                  # price/book (lower cheaper)
        s = band(f.pb, 5.0, 1.0)
        if s is not None: parts["pb"] = round(s * 100, 1)
    if not _nan(f.ps) and f.ps > 0:                                  # price/sales (catches no-earnings names)
        s = band(f.ps, 8.0, 1.0)
        if s is not None: parts["ps"] = round(s * 100, 1)

    # Sector-relative: cheap FOR ITS SECTOR (the apples-to-apples comparison) -----
    if sector_medians and f.sector in sector_medians:
        sm = sector_medians[f.sector]
        rel = []
        for key, val in (("pe", f.pe), ("pb", f.pb), ("ev_ebitda", f.ev_ebitda), ("ps", f.ps)):
            m = sm.get(key)
            if m and m > 0 and not _nan(val) and val > 0:
                rel.append(_clamp(1.5 - val / m))   # at sector median ->0.5, half ->1.0, 1.5x ->0.0
        if rel:
            parts["vs_sector"] = round(sum(rel) / len(rel) * 100, 1)

    # Analyst consensus upside: room to the mean 12-month price target. This is the
    # plain (unweighted) consensus from yfinance, gated on >=3 analysts for data
    # quality. NOTE it's a known-weak, optimism-biased signal and runs counter to a
    # contrarian value thesis -- it's one sub-metric among many and the leaderboard
    # will show whether it earns its place.
    if not _nan(f.target_mean) and not _nan(f.price) and f.price > 0 and f.num_analysts >= 3:
        upside = f.target_mean / f.price - 1.0
        parts["analyst_upside"] = round(band(upside, 0.0, 0.35) * 100, 1)   # 0% ->0, +35% ->100

    # History-relative methods (per-stock; only when data exists) -----------------
    pe_pct = float("nan")
    span = (f.pe_5y_high - f.pe_5y_low) if (f.pe_5y_high and f.pe_5y_low) else None
    if span and span > 0 and not _nan(f.pe):                        # P/E vs its own 5y band
        pe_pct = _clamp((f.pe - f.pe_5y_low) / span)
        parts["pe_vs_history"] = round((1 - pe_pct) * 100, 1)
    yld = float("nan")
    if f.div_yield_5y_avg and f.div_yield_5y_avg > 0 and f.dividend_yield > 0:   # yield vs its own avg
        yld = f.dividend_yield / f.div_yield_5y_avg
        parts["yield_vs_history"] = round(_clamp(0.5 + (yld - 1.0)) * 100, 1)

    score = round(sum(parts.values()) / len(parts), 1) if parts else 50.0
    return ValuationSignals(score=score, cheap=score >= 60, parts=parts,
                            summary=_val_summary(parts),
                            pe_percentile=round(pe_pct, 2) if not _nan(pe_pct) else float("nan"),
                            yield_vs_norm=round(yld, 2) if not _nan(yld) else float("nan"))


VAL_LABELS = {"earnings_yield": "earnings yield", "fcf_yield": "FCF yield",
              "ev_ebitda": "EV/EBITDA", "pb": "P/B", "ps": "P/S", "vs_sector": "vs sector",
              "analyst_upside": "analyst upside",
              "pe_vs_history": "P/E vs history", "yield_vs_history": "yield vs history"}


def _val_summary(parts):
    if not parts:
        return "no valuation data"
    cheap = sorted((k for k, v in parts.items() if v >= 60), key=lambda k: -parts[k])
    if cheap:
        return "cheap on " + ", ".join(VAL_LABELS[k] for k in cheap[:3])
    return f"fair value ({len(parts)} metrics)"


def compute_quality(f):
    checks = {
        "net income positive": f.net_income_positive,
        "operating cash flow +": f.op_cash_flow_positive,
        "free cash flow +": f.fcf_positive,
        "ROE > 12%": not _nan(f.roe) and f.roe > 12,
        "low leverage (D/E<100)": not _nan(f.debt_to_equity) and f.debt_to_equity < 100,
        "interest coverage > 4": not _nan(f.interest_coverage) and f.interest_coverage > 4,
        "current ratio > 1": not _nan(f.current_ratio) and f.current_ratio > 1,
        "gross margin > 30%": not _nan(f.gross_margin) and f.gross_margin > 30,
        "margins improving": f.margin_trend_up,
    }
    score = sum(1 for ok in checks.values() if ok)
    return QualitySignals(score, score >= THRESHOLDS["min_quality_score"],
                          [k for k, ok in checks.items() if not ok], round(score / 9 * 100, 1))


# ════════════════════════════════════════════════════════════════════════════
# CATALYST CLASSIFIER (the value-trap filter)
# ════════════════════════════════════════════════════════════════════════════

CATALYST_PROMPT = """You are a sell-side analyst triaging why a stock dropped.
Given recent headlines for {ticker}, classify the PRIMARY reason for weakness.

Headlines:
{news}

Return ONLY a JSON object, no prose:
{{"category": "market" | "sector" | "one_off_operational" | "structural",
  "transient": true | false, "reason": "<one sentence>", "source": "<headline>"}}

- market: broad selloff / macro -> transient true
- sector: rotation or sector pressure -> transient true
- one_off_operational: single miss / one-time charge -> usually transient true
- structural: guidance cut, lawsuit, regulator action, accounting issue,
  governance turmoil, secular decline -> transient false. When in doubt, structural."""


def _verdict(raw):
    raw = raw.replace("```json", "").replace("```", "").strip()
    d = json.loads(raw)
    cat, tr = d["category"], bool(d["transient"])
    return CatalystVerdict(cat, tr, d.get("reason", ""), d.get("source", ""),
                           veto=(cat == "structural" or not tr))


class MockClassifier:
    def classify(self, ticker, news):
        h = (news[0] if news else "").lower(); src = news[0] if news else ""
        if any(w in h for w in ["guidance", "lawsuit", "regulator", "restat", "fraud", "cuts", "probe"]):
            return CatalystVerdict("structural", False, "Company-specific deterioration.", src, veto=True)
        if any(w in h for w in ["sector", "healthcare", "staples", "fast food"]):
            return CatalystVerdict("sector", True, "Sector-wide pressure; fundamentals intact.", src)
        if any(w in h for w in ["market", "selloff", "dip", "risk-off"]):
            return CatalystVerdict("market", True, "Broad-market weakness.", src)
        if "earnings" in h:
            return CatalystVerdict("one_off_operational", True, "Single-quarter reaction.", src)
        return CatalystVerdict("market", True, "No company-specific negative catalyst found.", src)


class GroqClassifier:
    def __init__(self, model="llama-3.3-70b-versatile"):
        self.key = os.environ["GROQ_API_KEY"]; self.model = model

    def classify(self, ticker, news):
        import requests
        prompt = CATALYST_PROMPT.format(ticker=ticker, news="\n".join(f"- {n}" for n in news))
        try:
            r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                              headers={"Authorization": f"Bearer {self.key}"},
                              json={"model": self.model, "temperature": 0, "max_tokens": 300,
                                    "messages": [{"role": "user", "content": prompt}]}, timeout=30)
            r.raise_for_status()
            return _verdict(r.json()["choices"][0]["message"]["content"])
        except Exception as e:
            return CatalystVerdict("structural", False,
                                   f"Could not classify ({type(e).__name__}); excluded.", "", veto=True)


class AnthropicClassifier:
    def __init__(self, model="claude-sonnet-4-6"):
        import anthropic
        self.client = anthropic.Anthropic(); self.model = model

    def classify(self, ticker, news):
        prompt = CATALYST_PROMPT.format(ticker=ticker, news="\n".join(f"- {n}" for n in news))
        try:
            m = self.client.messages.create(model=self.model, max_tokens=300,
                                             messages=[{"role": "user", "content": prompt}])
            return _verdict("".join(b.text for b in m.content if b.type == "text"))
        except Exception as e:
            return CatalystVerdict("structural", False, f"Could not classify ({type(e).__name__}).", "", veto=True)


def score_catalyst(v):
    return float({"market": 90, "sector": 80, "one_off_operational": 60, "structural": 0}.get(v.category, 30))


def score_sentiment(news):
    neg = sum(any(w in n.lower() for w in ["cut", "lawsuit", "loss", "miss", "fraud", "probe"]) for n in news)
    return max(20.0, 80.0 - neg * 25)


# ════════════════════════════════════════════════════════════════════════════
# ANALYSIS WRITER (the ~200-word writeup for featured picks)
# ════════════════════════════════════════════════════════════════════════════

ANALYSIS_PROMPT = """Write a ~200-word investment analysis explaining why {ticker}
({name}, {sector}) screens as a value buy. Ground it ONLY in these signals; do not
invent numbers. Be plain and grounded, no hype, no jargon filler.

- Composite score: {composite}/100
- Pullback: {pullback:.1f}% off its 52-week high; RSI {rsi:.0f}; {trend}
- Days since recent peak: {ddec} ({reversal_note})
- Valuation: {val_line}; dividend yield {dy:.1f}%
- Quality: Piotroski-style F-score {fscore}/9 (financial health)
- Why it's down: [{cat}] {cat_reason}

Cover, in flowing prose: what makes it cheap, why the business looks healthy (so
this is likely a temporary dislocation rather than a value trap), what's driving
the dip and why it appears transient, and the main risk to watch. End with one
sober sentence. Do not give a price target or say "buy"."""


def _analysis_inputs(c):
    f, t, v = c.fund, c.tech, c.val
    cheap_metrics = ", ".join(f"{VAL_LABELS[k]} ({v.parts[k]:.0f}/100)"
                              for k in sorted(v.parts, key=lambda k: -v.parts[k])[:3]) or "limited data"
    val_line = f"blended value score {v.score:.0f}/100; cheapest on {cheap_metrics}"
    reversal_note = ("a fresh dip, which favors a near-term bounce" if t.days_in_decline <= 25
                     else "a longer slide, so confirm the trend has stabilized")
    return dict(ticker=f.ticker, name=f.name, sector=f.sector, composite=c.composite,
                pullback=t.pullback_pct * 100, rsi=t.rsi,
                trend="still above its 200-day average (long-term trend intact)" if t.above_200ma
                      else "below its 200-day average (trend broken -- higher risk)",
                ddec=t.days_in_decline, reversal_note=reversal_note,
                val_line=val_line, dy=f.dividend_yield, fscore=c.qual.fscore,
                cat=c.cat.category, cat_reason=c.cat.reason)


class SyntheticAnalyst:
    """Deterministic ~200-word writeup from the numbers, for offline demos."""
    def write(self, c):
        d = _analysis_inputs(c)
        return (
            f"{d['name']} screens as a value candidate at {d['composite']}/100, and the case rests on "
            f"a gap between price and fundamentals. The stock sits {d['pullback']:.1f}% below its 52-week "
            f"high with an RSI of {d['rsi']:.0f}, {d['trend']}. On valuation it looks inexpensive: "
            f"{d['val_line']}, supported by a {d['dy']:.1f}% dividend yield that pays you to wait. "
            f"What separates this from a falling knife is the balance sheet -- a Piotroski-style health "
            f"score of {d['fscore']}/9 points to a business that is still profitable and solvent rather "
            f"than quietly deteriorating, which is the usual mark of a value trap. The weakness traces to "
            f"a [{d['cat']}] cause: {d['cat_reason'].rstrip('.')}, which reads as a temporary dislocation "
            f"rather than lasting damage. The price has been easing for {d['ddec']} days -- {d['reversal_note']}. "
            f"The main risk is that the catalyst proves more durable than it looks, so the fundamentals are "
            f"worth re-checking before acting. On balance, a healthy, cheap name caught in a short-term "
            f"drawdown -- the exact setup this screen is built to surface."
        )


class GroqAnalyst:
    """The real ~200-word writeup via Groq (free)."""
    def __init__(self, model="llama-3.3-70b-versatile"):
        self.key = os.environ["GROQ_API_KEY"]; self.model = model

    def write(self, c):
        import requests
        prompt = ANALYSIS_PROMPT.format(**_analysis_inputs(c))
        try:
            r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                              headers={"Authorization": f"Bearer {self.key}"},
                              json={"model": self.model, "temperature": 0.4, "max_tokens": 400,
                                    "messages": [{"role": "user", "content": prompt}]}, timeout=40)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except Exception:
            return SyntheticAnalyst().write(c)   # fail safe to the deterministic version


# ════════════════════════════════════════════════════════════════════════════
# SCORING + GATES
# ════════════════════════════════════════════════════════════════════════════

def evaluate(c, news, sector_medians=None):
    c.val = compute_valuation(c.fund, sector_medians)
    c.qual = compute_quality(c.fund)
    sent = score_sentiment(news)
    c.cat.score = score_catalyst(c.cat)
    if c.cat.veto:
        c.vetoed_for = f"catalyst: {c.cat.reason}"
    elif c.qual.fscore < THRESHOLDS["min_quality_score"]:
        c.vetoed_for = f"quality floor: F-score {c.qual.fscore}/9"
    elif c.fund.market_cap_b < THRESHOLDS["min_market_cap_b"]:
        c.vetoed_for = f"below blue-chip cap (${c.fund.market_cap_b:.0f}B)"
    elif c.fund.next_earnings and (c.fund.next_earnings - date.today()).days <= THRESHOLDS["earnings_blackout"]:
        c.vetoed_for = f"earnings in {(c.fund.next_earnings - date.today()).days}d"
    if c.vetoed_for:
        return c
    c.composite = round(WEIGHTS["technical"] * c.tech.score + WEIGHTS["valuation"] * c.val.score +
                        WEIGHTS["quality"] * c.qual.score + WEIGHTS["catalyst"] * c.cat.score +
                        WEIGHTS["sentiment"] * sent, 1)
    return c


# ════════════════════════════════════════════════════════════════════════════
# UNIVERSE
# ════════════════════════════════════════════════════════════════════════════

def load_universe():
    if PROVIDER == "synthetic":
        return SEED_UNIVERSE
    try:
        with open(_p(UNIVERSE_FILE)) as fh:
            data = json.load(fh)
        groups = {k: v for k, v in data.items() if isinstance(v, list)}
        if groups:
            return groups
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    print(f"  {UNIVERSE_FILE} missing/empty -- using built-in seed universe.")
    return SEED_UNIVERSE


# ════════════════════════════════════════════════════════════════════════════
# FUNDAMENTALS CACHE -> SECTOR MEDIANS
# A rolling store of fundamentals for the whole universe, refreshed a slice at a
# time (so all ~600 get covered over a few weeks without a heavy weekly pull).
# Its job is to compute sector medians so each stock can be judged cheap FOR ITS
# SECTOR, not against a one-size-fits-all band.
# ════════════════════════════════════════════════════════════════════════════

def load_fund_cache():
    try:
        with open(_p(FUND_CACHE)) as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_fund_cache(cache):
    with open(_p(FUND_CACHE), "w") as fh:
        json.dump(cache, fh, default=str)


def cache_put(cache, f, today):
    d = asdict(f)
    d["next_earnings"] = f.next_earnings.isoformat() if f.next_earnings else None
    d["_cached"] = today.isoformat()
    cache[f.ticker] = d


def refresh_fundamentals_cache(provider, cache, universe_tickers, today):
    """Fetch fundamentals for the most-stale slice of the universe (paced), so
    coverage rotates through all names over ~fund_staleness_days. Returns count."""
    staleness = timedelta(days=THRESHOLDS["fund_staleness_days"])

    def stale(tk):
        e = cache.get(tk)
        if not e:
            return True
        try:
            return date.fromisoformat(e.get("_cached", "2000-01-01")) < today - staleness
        except Exception:
            return True

    todo = sorted((tk for tk in universe_tickers if stale(tk)),
                  key=lambda tk: cache.get(tk, {}).get("_cached", "2000-01-01"))
    todo = todo[:THRESHOLDS["max_fundamentals_refresh"]]
    paced = (PROVIDER == "yfinance")
    for i in range(0, len(todo), SCAN["batch_size"]):
        for tk in todo[i:i + SCAN["batch_size"]]:
            try:
                cache_put(cache, provider.fundamentals(tk), today)
            except Exception:
                pass
        if paced and i + SCAN["batch_size"] < len(todo):
            time.sleep(SCAN["sleep_between_batches"])
    return len(todo)


def compute_sector_medians(cache):
    """Median P/E, P/B, EV/EBITDA, P/S per sector, from the cache. Median (not
    mean) so a few extreme multiples don't skew it. Sectors with too few names
    are dropped -- those stocks fall back to the absolute bands."""
    by_sector = {}
    for entry in cache.values():
        sec = entry.get("sector")
        if sec and sec != "?":
            by_sector.setdefault(sec, []).append(entry)

    def median(entries, key):
        vals = sorted(e[key] for e in entries
                      if e.get(key) is not None and not _nan(e[key]) and e[key] > 0)
        return vals[len(vals) // 2] if vals else None

    out = {}
    for sec, entries in by_sector.items():
        if len(entries) < THRESHOLDS["min_sector_count"]:
            continue
        out[sec] = {"pe": median(entries, "pe"), "pb": median(entries, "pb"),
                    "ev_ebitda": median(entries, "ev_ebitda"), "ps": median(entries, "ps"),
                    "count": len(entries)}
    return out


# ════════════════════════════════════════════════════════════════════════════
# PACED SCAN (slow + polite, with retries + checkpoint)
# ════════════════════════════════════════════════════════════════════════════

def _load_checkpoint():
    try:
        with open(_p(CHECKPOINT)) as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def scan_prices(provider, tickers):
    """Fetch prices for the whole universe in paced batches so free data
    survives. Checkpoints completed tickers so a restart resumes."""
    done = _load_checkpoint()                       # ticker -> "ok"/"fail" from a prior partial run
    results, paced = {}, (PROVIDER == "yfinance")
    todo = [t for t in tickers if t not in done]
    for i in range(0, len(todo), SCAN["batch_size"]):
        batch = todo[i:i + SCAN["batch_size"]]
        for tk in batch:
            ph = None
            for attempt in range(SCAN["max_retries"]):
                try:
                    ph = provider.prices(tk); break
                except Exception:
                    if paced and attempt < SCAN["max_retries"] - 1:
                        time.sleep(SCAN["retry_sleep"])
            if ph and len(ph.closes) >= 60:
                results[tk] = ph; done[tk] = "ok"
            else:
                done[tk] = "fail"
        with open(_p(CHECKPOINT), "w") as fh:
            json.dump(done, fh)
        if paced and i + SCAN["batch_size"] < len(todo):
            time.sleep(SCAN["sleep_between_batches"])
    # Re-load price objects for any that succeeded earlier but aren't in this run's dict
    # (kept simple: on a clean run `done` starts empty, so results is complete).
    if os.path.exists(_p(CHECKPOINT)):
        os.remove(_p(CHECKPOINT))                   # clear on successful completion
    return results


# ════════════════════════════════════════════════════════════════════════════
# OUTCOME LOG (the heart of Phase 1)
# ════════════════════════════════════════════════════════════════════════════

def load_log():
    try:
        with open(_p(PICKS_LOG)) as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"picks": []}


def save_log(log):
    with open(_p(PICKS_LOG), "w") as fh:
        json.dump(log, fh, indent=2, default=str)


def open_tickers(log, today):
    """Names picked within the open-bet window -- excluded from re-picking."""
    cutoff = today - timedelta(days=THRESHOLDS["open_window_days"])
    return {r["ticker"] for r in log["picks"]
            if date.fromisoformat(r["pick_date"]) > cutoff}


def record_picks(log, picks, bench_price, today):
    for c in picks:
        log["picks"].append({
            "ticker": c.fund.ticker, "name": c.fund.name, "exchange": c.fund.exchange,
            "sector": c.fund.sector, "pick_date": today.isoformat(),
            "pick_price": round(c.fund_price, 2), "bench_at_pick": round(bench_price, 2) if bench_price else None,
            "composite": c.composite,
            "signals": {"technical": c.tech.score, "valuation": c.val.score,
                        "quality": c.qual.score, "catalyst": c.cat.score,
                        "pullback_pct": round(c.tech.pullback_pct, 3), "rsi": round(c.tech.rsi),
                        "days_in_decline": c.tech.days_in_decline, "fscore": c.qual.fscore,
                        "catalyst_category": c.cat.category,
                        "valuation_parts": c.val.parts},   # per-metric sub-scores for the leaderboard
            "outcomes": {k: None for k in HORIZONS},
            "outcomes_bench": {k: None for k in HORIZONS},
        })


def update_outcomes(provider, log, today):
    """Fill in return slots for picks whose horizons have matured. Each return is
    measured vs the pick price, alongside SPY over the same window for context."""
    bench_now = provider.last_price(BENCHMARK)
    filled = 0
    for r in log["picks"]:
        age = (today - date.fromisoformat(r["pick_date"])).days
        needs = [h for h, d in HORIZONS.items() if age >= d and r["outcomes"].get(h) is None]
        if not needs:
            continue
        now = provider.last_price(r["ticker"])
        if now is None or not r.get("pick_price"):
            continue
        ret = (now / r["pick_price"] - 1) * 100
        bret = ((bench_now / r["bench_at_pick"] - 1) * 100
                if bench_now and r.get("bench_at_pick") else None)
        for h in needs:
            r["outcomes"][h] = round(ret, 2)
            r["outcomes_bench"][h] = round(bret, 2) if bret is not None else None
            filled += 1
    return filled


def track_record_stats(log):
    """Summary over picks that have at least a 1-month outcome."""
    matured = [r for r in log["picks"] if r["outcomes"].get("1m") is not None]
    if not matured:
        return None
    beats = sum(1 for r in matured
                if r["outcomes_bench"].get("1m") is None or r["outcomes"]["1m"] > r["outcomes_bench"]["1m"])
    avg = sum(r["outcomes"]["1m"] for r in matured) / len(matured)
    avg_b = sum(r["outcomes_bench"]["1m"] for r in matured
                if r["outcomes_bench"].get("1m") is not None) / max(1, sum(
                    1 for r in matured if r["outcomes_bench"].get("1m") is not None))
    return {"n": len(matured), "beats": beats, "avg": round(avg, 1), "avg_bench": round(avg_b, 1)}


# ════════════════════════════════════════════════════════════════════════════
# PIPELINE
# ════════════════════════════════════════════════════════════════════════════

def run_screen(provider, classifier, analyst, exclude, sector_medians=None, fund_cache=None, today=None):
    universe = load_universe()
    tickers = [t for ts in universe.values() for t in ts if t not in exclude]
    print(f"  scanning {len(tickers)} names ({len(exclude)} excluded as open bets)")
    today = today or date.today()

    prices = scan_prices(provider, tickers)
    survivors = []
    for tk, ph in prices.items():
        tech = compute_technicals(ph)
        if is_corrected(tech):
            try:
                f = provider.fundamentals(tk)        # fresh -> P/E reflects the just-dropped price
                if fund_cache is not None:
                    cache_put(fund_cache, f, today)  # feed the freshest data into the cache too
                c = Candidate(fund=f, tech=tech)
                c.fund_price = ph.last               # remember pick price for the log
                survivors.append(c)
            except Exception:
                pass

    scored, rejected = [], []
    for c in survivors:
        news = provider.recent_news(c.fund.ticker)
        c.cat = classifier.classify(c.fund.ticker, news)
        c = evaluate(c, news, sector_medians)        # sector-relative scoring when medians available
        (rejected if c.vetoed_for else scored).append(c)

    scored.sort(key=lambda x: x.composite, reverse=True)
    qualifiers = [c for c in scored if c.composite >= THRESHOLDS["min_composite"]][:THRESHOLDS["max_picks_per_run"]]
    featured = qualifiers[:THRESHOLDS["featured"]]
    for c in featured:                          # write the ~200-word analysis for the top N
        c.analysis = analyst.write(c)
    return qualifiers, featured, rejected


# ════════════════════════════════════════════════════════════════════════════
# EMAIL
# ════════════════════════════════════════════════════════════════════════════

def send_email(html, text, subject):
    sender, pw, to = os.getenv("EMAIL_FROM"), os.getenv("EMAIL_APP_PASSWORD"), os.getenv("EMAIL_TO")
    if not (sender and pw and to):
        print("[no email creds set - writing preview to email_preview.html]")
        with open(_p("email_preview.html"), "w") as fh:
            fh.write(html)
        return
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, sender, to
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(sender, pw); s.send_message(msg)
    print(f"Emailed digest to {to}")


# ════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════

def build_provider():
    return YFinanceProvider() if PROVIDER == "yfinance" else SyntheticProvider()


def build_classifier():
    return {"groq": GroqClassifier, "anthropic": AnthropicClassifier, "mock": MockClassifier}[CLASSIFIER]()


def build_analyst():
    return GroqAnalyst() if CLASSIFIER in ("groq",) else SyntheticAnalyst()


def main():
    import email_report
    today = date.today()
    print(f"Weekly screen  [provider={PROVIDER}  classifier={CLASSIFIER}]  {today}")
    provider = build_provider()

    log = load_log()
    filled = update_outcomes(provider, log, today)          # 1) mature past picks
    print(f"  updated {filled} outcome slots on existing picks")

    # 2) refresh a slice of the fundamentals cache, then build sector medians
    universe = load_universe()
    all_tickers = [t for ts in universe.values() for t in ts]
    fund_cache = load_fund_cache()
    refreshed = refresh_fundamentals_cache(provider, fund_cache, all_tickers, today)
    sector_medians = compute_sector_medians(fund_cache)
    print(f"  refreshed {refreshed} fundamentals; sector medians for {len(sector_medians)} sectors "
          f"({len(fund_cache)} names cached)")

    exclude = open_tickers(log, today)                      # 3) dedup open bets
    qualifiers, featured, rejected = run_screen(            # 4) scan + score (sector-relative)
        provider, build_classifier(), build_analyst(), exclude,
        sector_medians=sector_medians, fund_cache=fund_cache, today=today)

    bench_price = provider.last_price(BENCHMARK)
    record_picks(log, qualifiers, bench_price, today)       # 5) log this week's picks
    save_log(log)
    save_fund_cache(fund_cache)

    stats = track_record_stats(log)
    html, text = email_report.build(today, featured, qualifiers, log["picks"], stats)
    send_email(html, text, f"Weekly Value Screen - {today.strftime('%b %d, %Y')}")
    print(f"Done. {len(qualifiers)} qualifiers ({len(featured)} featured), {len(rejected)} screened out.")


if __name__ == "__main__":
    main()
