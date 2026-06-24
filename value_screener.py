"""
value_screener.py - an agentic value-stock screener (cloud edition)

WHAT THIS IS
    A research/screening tool that builds a daily shortlist of blue-chip stocks
    that (a) have pulled back, (b) look cheap vs their own history, (c) are
    fundamentally healthy, and (d) are down for a TRANSIENT reason rather than a
    structural one. It emails you the ranked shortlist with the evidence.

WHAT THIS IS NOT
    Financial advice. This surfaces candidates and the reasons behind them; YOU
    make the buy decision. The catalyst classifier is the value-trap filter and
    is deliberately conservative, but no screen is a substitute for judgment.

CONFIG comes from environment variables so secrets never live in the code:
    PROVIDER             "synthetic" (offline demo) | "yfinance" (live, free)
    CLASSIFIER           "mock" (offline keyword rules) | "groq" (free LLM) | "anthropic" (paid)
    GROQ_API_KEY         free key from console.groq.com   (if CLASSIFIER=groq)
    EMAIL_FROM           your Gmail address
    EMAIL_APP_PASSWORD   Gmail app password (not your login password)
    EMAIL_TO             where the digest goes
    (if email vars are unset, the digest prints to stdout instead of sending)

RUN
    Offline test:   python value_screener.py
    Live local:     PROVIDER=yfinance CLASSIFIER=groq GROQ_API_KEY=... python value_screener.py
    Cloud:          GitHub Actions sets the env vars from repo secrets (see workflow).
"""

from __future__ import annotations

import json
import math
import os
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional, Protocol


# ════════════════════════════════════════════════════════════════════════════
# CONFIG
# ════════════════════════════════════════════════════════════════════════════

PROVIDER = os.getenv("PROVIDER", "synthetic")
CLASSIFIER = os.getenv("CLASSIFIER", "mock")
STATE_FILE = "state.json"

# Blue-chip universe across NYSE/NASDAQ + TSX. Expand freely; in production you
# can build this from index membership (S&P 500 + NASDAQ-100 + TSX 60) instead
# of a hand list. TSX tickers use the .TO suffix.
UNIVERSE = {
    "NYSE/NASDAQ": ["AAPL", "MSFT", "JNJ", "PG", "KO", "PEP", "HD", "V",
                    "JPM", "DIS", "NKE", "MCD", "CSCO", "INTC", "VZ", "ABBV",
                    "MRK", "CVX", "WMT", "UNH"],
    "TSX":         ["RY.TO", "TD.TO", "ENB.TO", "BNS.TO", "CNR.TO", "SHOP.TO",
                    "BCE.TO", "SU.TO"],
}

WEIGHTS = {
    "technical":   0.20,   # how oversold / how big the pullback
    "valuation":   0.30,   # cheap vs its own history (core value lens)
    "quality":     0.25,   # fundamental health (the trap filter)
    "catalyst":    0.15,   # how transient the reason for the drop is
    "sentiment":   0.10,   # news/analyst tone, panic vs deterioration
}

THRESHOLDS = {
    "min_pullback_pct":   0.10,   # >=10% off 52w high to count as "corrected"
    "rsi_oversold":       35,
    "min_market_cap_b":   10,     # blue-chip floor ($B)
    "min_quality_score":  4,      # of 9; below this is a hard veto
    "min_composite":      60,     # only email candidates scoring >= this
    "earnings_blackout":  3,      # skip if earnings within N days (event risk)
    "cooldown_days":      5,      # don't re-surface the same name for N days
    "max_picks":          5,
}


# ════════════════════════════════════════════════════════════════════════════
# DATA MODELS
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class Fundamentals:
    ticker: str
    name: str
    exchange: str
    market_cap_b: float
    sector: str
    pe: float
    pe_5y_low: Optional[float]
    pe_5y_high: Optional[float]
    pb: float
    ev_ebitda: float
    fcf_positive: bool
    net_income_positive: bool
    op_cash_flow_positive: bool
    roe: float                      # %
    debt_to_equity: float
    interest_coverage: float
    current_ratio: float
    gross_margin: float             # %
    margin_trend_up: bool
    dividend_yield: float           # %
    div_yield_5y_avg: Optional[float]  # %, key "vs its own history" signal
    payout_ratio: float             # %
    next_earnings: Optional[date]


@dataclass
class PriceHistory:
    ticker: str
    closes: list[float]

    @property
    def last(self) -> float:
        return self.closes[-1]

    @property
    def high_52w(self) -> float:
        return max(self.closes[-252:]) if self.closes else self.last


@dataclass
class TechnicalSignals:
    pullback_pct: float
    rsi: float
    above_200ma: bool
    below_50ma: bool
    score: float = 0.0


@dataclass
class ValuationSignals:
    pe_percentile: float       # 0=cheapest in 5y band; nan if unavailable
    yield_vs_norm: float       # >1 = yielding more than its 5y average; nan if n/a
    cheap: bool
    score: float = 0.0


@dataclass
class QualitySignals:
    fscore: int
    healthy: bool
    notes: list[str] = field(default_factory=list)
    score: float = 0.0


@dataclass
class CatalystVerdict:
    category: str              # market | sector | one_off_operational | structural
    transient: bool
    reason: str
    source: str
    veto: bool = False
    score: float = 0.0


@dataclass
class Candidate:
    fund: Fundamentals
    tech: TechnicalSignals
    val: Optional[ValuationSignals] = None
    qual: Optional[QualitySignals] = None
    cat: Optional[CatalystVerdict] = None
    composite: float = 0.0
    vetoed_for: Optional[str] = None


def _nan(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


# ════════════════════════════════════════════════════════════════════════════
# DATA PROVIDERS
# ════════════════════════════════════════════════════════════════════════════

class DataProvider(Protocol):
    def prices(self, ticker: str) -> PriceHistory: ...
    def fundamentals(self, ticker: str) -> Fundamentals: ...
    def recent_news(self, ticker: str) -> list[str]: ...


class YFinanceProvider:
    """Live data via yfinance (free, no key). Defensive throughout: yfinance
    field availability varies by ticker and version, so every access falls back
    gracefully rather than crashing the daily run.

    Honest fidelity note: yfinance gives clean prices and current ratios, but
    NOT a clean 5-year P/E band. The 'cheap vs own history' lens therefore leans
    on dividend-yield-vs-5y-average (which yfinance DOES expose) plus current
    multiples. For full P/E-percentile fidelity, swap in FMP/Polygon later."""

    def __init__(self):
        import yfinance as yf
        self.yf = yf
        self._cache: dict[str, object] = {}

    def _t(self, ticker):
        if ticker not in self._cache:
            self._cache[ticker] = self.yf.Ticker(ticker)
        return self._cache[ticker]

    def prices(self, ticker):
        hist = self._t(ticker).history(period="1y")
        return PriceHistory(ticker, [float(x) for x in hist["Close"].values])

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
        # Best-effort: gross margin up year over year. Safe-defaults to False.
        try:
            fin = t.financials
            rev = fin.loc["Total Revenue"]
            gp = fin.loc["Gross Profit"]
            m_new = gp.iloc[0] / rev.iloc[0]
            m_old = gp.iloc[1] / rev.iloc[1]
            return bool(m_new > m_old)
        except Exception:
            return False

    def fundamentals(self, ticker):
        t = self._t(ticker)
        info = {}
        try:
            info = t.info or {}
        except Exception:
            info = {}

        def g(key, default=float("nan")):
            v = info.get(key)
            return default if v is None else v

        return Fundamentals(
            ticker=ticker,
            name=info.get("shortName", ticker),
            exchange=info.get("fullExchangeName", "TSX" if ticker.endswith(".TO") else "?"),
            market_cap_b=(g("marketCap", 0) or 0) / 1e9,
            sector=info.get("sector", "?"),
            pe=g("trailingPE"),
            pe_5y_low=None,                       # needs historical EPS source
            pe_5y_high=None,                      # needs historical EPS source
            pb=g("priceToBook"),
            ev_ebitda=g("enterpriseToEbitda"),
            fcf_positive=(g("freeCashflow", 0) or 0) > 0,
            net_income_positive=(g("netIncomeToCommon", 0) or 0) > 0,
            op_cash_flow_positive=(g("operatingCashflow", 0) or 0) > 0,
            roe=(g("returnOnEquity", 0) or 0) * 100,
            debt_to_equity=g("debtToEquity"),
            interest_coverage=float("nan"),       # derive from financials if needed
            current_ratio=g("currentRatio"),
            gross_margin=(g("grossMargins", 0) or 0) * 100,
            margin_trend_up=self._margin_trend(t),
            dividend_yield=(g("dividendYield", 0) or 0) * 100,
            div_yield_5y_avg=info.get("fiveYearAvgDividendYield"),  # already in %
            payout_ratio=(g("payoutRatio", 0) or 0) * 100,
            next_earnings=self._earnings_date(t),
        )

    def recent_news(self, ticker):
        try:
            items = self._t(ticker).news or []
            titles = []
            for n in items[:8]:
                title = n.get("title") or n.get("content", {}).get("title", "")
                if title:
                    titles.append(title)
            return titles or ["No recent headlines retrieved"]
        except Exception:
            return ["No recent headlines retrieved"]


class SyntheticProvider:
    """Offline data so the whole pipeline runs with no network/keys. Seeded for
    reproducibility, with hand-built archetypes so the demo is illustrative."""

    ARCHETYPES = {
        "JNJ":  ("clean_buy",  "Sector-wide selloff drags healthcare names lower"),
        "PG":   ("clean_buy",  "Broad market dip on rate fears hits consumer staples"),
        "KO":   ("clean_buy",  "Defensive names slip in risk-off session"),
        "MCD":  ("clean_buy",  "Fast food stocks pull back on consumer spending worry"),
        "INTC": ("value_trap", "Intel cuts full-year guidance, flags share loss"),
        "VZ":   ("value_trap", "Verizon faces lawsuit over legacy lead cables"),
        "DIS":  ("mild",       "Disney slips after mixed quarterly earnings"),
    }

    def __init__(self, seed=7):
        self.seed = seed

    def _rng(self, ticker):
        return random.Random(f"{self.seed}-{ticker}")

    def prices(self, ticker):
        r = self._rng(ticker)
        kind = self.ARCHETYPES.get(ticker, ("normal", ""))[0]
        n, base = 252, r.uniform(50, 300)
        closes = [base]
        drift = r.uniform(0.0002, 0.0006)
        for _ in range(n - 1):
            closes.append(closes[-1] * (1 + r.gauss(drift, 0.012)))
        if kind in ("clean_buy", "value_trap", "mild"):
            depth = r.uniform(0.12, 0.20)
            for i in range(n - 20, n):
                closes[i] *= (1 - depth * ((i - (n - 20)) / 20))
        return PriceHistory(ticker, closes)

    def fundamentals(self, ticker):
        r = self._rng(ticker)
        kind = self.ARCHETYPES.get(ticker, ("normal", ""))[0]
        healthy = kind != "value_trap"
        pe = r.uniform(11, 18) if kind == "clean_buy" else r.uniform(9, 26)
        yld = r.uniform(2.0, 4.5)
        return Fundamentals(
            ticker=ticker, name=f"{ticker} Inc.",
            exchange="TSX" if ticker.endswith(".TO") else "NASDAQ/NYSE",
            market_cap_b=r.uniform(15, 400),
            sector=r.choice(["Staples", "Healthcare", "Tech", "Financials", "Comms"]),
            pe=pe, pe_5y_low=pe * r.uniform(0.75, 0.9), pe_5y_high=pe * r.uniform(1.4, 2.0),
            pb=r.uniform(1.5, 6), ev_ebitda=r.uniform(7, 16),
            fcf_positive=healthy, net_income_positive=healthy,
            op_cash_flow_positive=healthy or r.random() > 0.5,
            roe=r.uniform(14, 30) if healthy else r.uniform(-5, 8),
            debt_to_equity=r.uniform(30, 90) if healthy else r.uniform(140, 260),
            interest_coverage=r.uniform(6, 20) if healthy else r.uniform(0.8, 3),
            current_ratio=r.uniform(1.1, 2.2) if healthy else r.uniform(0.6, 1.0),
            gross_margin=r.uniform(35, 60), margin_trend_up=healthy,
            dividend_yield=yld, div_yield_5y_avg=yld * r.uniform(0.82, 0.94),
            payout_ratio=r.uniform(35, 60) if healthy else r.uniform(85, 130),
            next_earnings=date.today() + timedelta(days=r.randint(6, 60)),
        )

    def recent_news(self, ticker):
        head = self.ARCHETYPES.get(ticker, ("normal", "No major company-specific news"))[1]
        return [head, "Analysts weigh in on the move", "Volume elevated in session"]


# ════════════════════════════════════════════════════════════════════════════
# TOOLS (the screening lenses)
# ════════════════════════════════════════════════════════════════════════════

def _rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains = losses = 0.0
    for i in range(-period, 0):
        chg = closes[i] - closes[i - 1]
        gains += max(chg, 0); losses += max(-chg, 0)
    avg_gain, avg_loss = gains / period, losses / period
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + avg_gain / avg_loss))


def _ma(closes, window):
    w = closes[-window:] if len(closes) >= window else closes
    return sum(w) / len(w)


def compute_technicals(p):
    pullback = (p.high_52w - p.last) / p.high_52w if p.high_52w else 0.0
    rsi = _rsi(p.closes)
    above_200 = p.last > _ma(p.closes, 200)
    below_50 = p.last < _ma(p.closes, 50)
    raw = min(pullback / 0.25, 1.0) * 60 + max(0, (50 - rsi) / 50) * 40
    if not above_200:
        raw *= 0.5   # falling-knife penalty: long-term trend broken
    return TechnicalSignals(pullback, rsi, above_200, below_50, round(raw, 1))


def is_corrected(t):
    return t.pullback_pct >= THRESHOLDS["min_pullback_pct"] or t.rsi <= THRESHOLDS["rsi_oversold"]


def compute_valuation(f):
    """Cheap-vs-own-history, blending whichever signals are available."""
    parts = []
    pe_pct = float("nan")
    span = (f.pe_5y_high - f.pe_5y_low) if (f.pe_5y_high and f.pe_5y_low) else None
    if span and span > 0 and not _nan(f.pe):
        pe_pct = _clamp((f.pe - f.pe_5y_low) / span)
        parts.append(1 - pe_pct)                       # cheaper in band -> higher
    yld_ratio = float("nan")
    if f.div_yield_5y_avg and f.div_yield_5y_avg > 0 and f.dividend_yield > 0:
        yld_ratio = f.dividend_yield / f.div_yield_5y_avg
        parts.append(_clamp(0.5 + (yld_ratio - 1.0)))  # yield above its norm -> on sale
    if not parts and not _nan(f.pe):
        parts.append(_clamp((25 - f.pe) / 20))         # weak absolute fallback
    score01 = sum(parts) / len(parts) if parts else 0.5
    return ValuationSignals(
        round(pe_pct, 2) if not _nan(pe_pct) else float("nan"),
        round(yld_ratio, 2) if not _nan(yld_ratio) else float("nan"),
        score01 >= 0.6, round(score01 * 100, 1))


def compute_quality(f):
    """Piotroski-style subset (0-9). Real F-score needs YoY deltas; this is a
    documented approximation leaning on reliably-available point-in-time health."""
    checks = {
        "net income positive":    f.net_income_positive,
        "operating cash flow +":  f.op_cash_flow_positive,
        "free cash flow +":       f.fcf_positive,
        "ROE > 12%":              not _nan(f.roe) and f.roe > 12,
        "low leverage (D/E<100)": not _nan(f.debt_to_equity) and f.debt_to_equity < 100,
        "interest coverage > 4":  not _nan(f.interest_coverage) and f.interest_coverage > 4,
        "current ratio > 1":      not _nan(f.current_ratio) and f.current_ratio > 1,
        "gross margin > 30%":     not _nan(f.gross_margin) and f.gross_margin > 30,
        "margins improving":      f.margin_trend_up,
    }
    score = sum(1 for ok in checks.values() if ok)
    return QualitySignals(score, score >= THRESHOLDS["min_quality_score"],
                          [k for k, ok in checks.items() if not ok],
                          round(score / 9 * 100, 1))


# ════════════════════════════════════════════════════════════════════════════
# CATALYST CLASSIFIER (the LLM step - the value-trap filter)
# ════════════════════════════════════════════════════════════════════════════

CATALYST_PROMPT = """You are a sell-side analyst triaging why a stock dropped.
Given recent headlines for {ticker}, classify the PRIMARY reason for weakness.

Headlines:
{news}

Return ONLY a JSON object, no prose:
{{"category": "market" | "sector" | "one_off_operational" | "structural",
  "transient": true | false,
  "reason": "<one sentence>",
  "source": "<which headline drove this>"}}

Guidance:
- "market": broad selloff / rates / macro -> transient true
- "sector": rotation or sector-wide pressure -> transient true
- "one_off_operational": single earnings miss, one-time charge -> usually transient true
- "structural": guidance cut, lawsuit, regulatory action, accounting issue,
  governance/exec turmoil, secular decline -> transient false. This is the
  value-trap signal; when in doubt here, choose structural."""


def _verdict_from_json(raw):
    raw = raw.replace("```json", "").replace("```", "").strip()
    d = json.loads(raw)
    cat = d["category"]
    transient = bool(d["transient"])
    return CatalystVerdict(cat, transient, d.get("reason", ""), d.get("source", ""),
                           veto=(cat == "structural" or not transient))


class MockClassifier:
    """Offline keyword rules so the pipeline runs with no key."""
    def classify(self, ticker, news):
        head = (news[0] if news else "").lower()
        src = news[0] if news else ""
        if any(w in head for w in ["guidance", "lawsuit", "regulator", "restat", "fraud", "cuts", "probe"]):
            return CatalystVerdict("structural", False,
                                   "Company-specific deterioration, not a market dip.", src, veto=True)
        if any(w in head for w in ["sector", "healthcare", "staples", "fast food"]):
            return CatalystVerdict("sector", True,
                                   "Sector-wide pressure; company fundamentals intact.", src)
        if any(w in head for w in ["market", "selloff", "dip", "risk-off"]):
            return CatalystVerdict("market", True, "Broad-market weakness, not company-specific.", src)
        if "earnings" in head:
            return CatalystVerdict("one_off_operational", True,
                                   "Single-quarter reaction; watch for follow-through.", src)
        return CatalystVerdict("market", True, "No company-specific negative catalyst found.", src)


class GroqClassifier:
    """Free LLM classification via Groq (OpenAI-compatible, no credit card).
    llama-3.3-70b-versatile for nuance; swap to llama-3.1-8b-instant for higher
    rate limits. Fails SAFE: unparseable output -> veto rather than risk a trap."""
    def __init__(self, model="llama-3.3-70b-versatile"):
        self.key = os.environ["GROQ_API_KEY"]
        self.model = model

    def classify(self, ticker, news):
        import requests
        prompt = CATALYST_PROMPT.format(ticker=ticker, news="\n".join(f"- {n}" for n in news))
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.key}"},
                json={"model": self.model, "temperature": 0, "max_tokens": 300,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=30)
            r.raise_for_status()
            return _verdict_from_json(r.json()["choices"][0]["message"]["content"])
        except Exception as e:
            return CatalystVerdict("structural", False,
                                   f"Could not classify ({type(e).__name__}); excluded as a precaution.",
                                   "", veto=True)


class AnthropicClassifier:
    """Paid alternative. Kept for completeness; not used by default."""
    def __init__(self, model="claude-sonnet-4-6"):
        import anthropic
        self.client = anthropic.Anthropic()
        self.model = model

    def classify(self, ticker, news):
        prompt = CATALYST_PROMPT.format(ticker=ticker, news="\n".join(f"- {n}" for n in news))
        try:
            msg = self.client.messages.create(
                model=self.model, max_tokens=300,
                messages=[{"role": "user", "content": prompt}])
            return _verdict_from_json("".join(b.text for b in msg.content if b.type == "text"))
        except Exception as e:
            return CatalystVerdict("structural", False,
                                   f"Could not classify ({type(e).__name__}); excluded.", "", veto=True)


def score_catalyst(v):
    return float({"market": 90, "sector": 80, "one_off_operational": 60, "structural": 0}.get(v.category, 30))


# ════════════════════════════════════════════════════════════════════════════
# SCORING + GATES
# ════════════════════════════════════════════════════════════════════════════

def score_sentiment(news):
    neg = sum(any(w in n.lower() for w in ["cut", "lawsuit", "loss", "miss", "fraud", "probe"]) for n in news)
    return max(20.0, 80.0 - neg * 25)


def evaluate(c, news):
    c.val = compute_valuation(c.fund)
    c.qual = compute_quality(c.fund)
    sent = score_sentiment(news)
    c.cat.score = score_catalyst(c.cat)

    if c.cat.veto:
        c.vetoed_for = f"catalyst: {c.cat.reason}"
    elif c.qual.fscore < THRESHOLDS["min_quality_score"]:
        c.vetoed_for = f"quality floor: F-score {c.qual.fscore}/9 (failed: {', '.join(c.qual.notes[:3])})"
    elif c.fund.market_cap_b < THRESHOLDS["min_market_cap_b"]:
        c.vetoed_for = f"below blue-chip cap (${c.fund.market_cap_b:.0f}B)"
    elif c.fund.next_earnings and (c.fund.next_earnings - date.today()).days <= THRESHOLDS["earnings_blackout"]:
        c.vetoed_for = f"earnings in {(c.fund.next_earnings - date.today()).days}d (event risk)"
    if c.vetoed_for:
        return c

    c.composite = round(
        WEIGHTS["technical"] * c.tech.score + WEIGHTS["valuation"] * c.val.score +
        WEIGHTS["quality"] * c.qual.score + WEIGHTS["catalyst"] * c.cat.score +
        WEIGHTS["sentiment"] * sent, 1)
    return c


# ════════════════════════════════════════════════════════════════════════════
# PIPELINE
# ════════════════════════════════════════════════════════════════════════════

def run_screen(provider, classifier, recently_sent):
    tickers = [t for ts in UNIVERSE.values() for t in ts]
    survivors, rejected, scored = [], [], []

    for tk in tickers:
        if tk in recently_sent:
            continue
        try:
            tech = compute_technicals(provider.prices(tk))
        except Exception as e:
            print(f"  skip {tk}: price fetch failed ({type(e).__name__})")
            continue
        if is_corrected(tech):
            try:
                survivors.append(Candidate(fund=provider.fundamentals(tk), tech=tech))
            except Exception as e:
                print(f"  skip {tk}: fundamentals failed ({type(e).__name__})")

    for c in survivors:
        news = provider.recent_news(c.fund.ticker)
        c.cat = classifier.classify(c.fund.ticker, news)
        c = evaluate(c, news)
        (rejected if c.vetoed_for else scored).append(c)

    scored.sort(key=lambda x: x.composite, reverse=True)
    passing = [c for c in scored if c.composite >= THRESHOLDS["min_composite"]]
    return passing[:THRESHOLDS["max_picks"]], rejected


# ════════════════════════════════════════════════════════════════════════════
# COOLDOWN STATE (persisted to state.json; committed back by the workflow)
# ════════════════════════════════════════════════════════════════════════════

def load_recently_sent(cooldown_days):
    try:
        with open(STATE_FILE) as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {"sent": {}}
    cutoff = date.today() - timedelta(days=cooldown_days)
    recent = {t for t, d in data.get("sent", {}).items() if date.fromisoformat(d) > cutoff}
    return recent, data


def record_sent(data, tickers, cooldown_days):
    sent = data.get("sent", {})
    today = date.today().isoformat()
    for t in tickers:
        sent[t] = today
    cutoff = date.today() - timedelta(days=cooldown_days * 3)   # prune stale entries
    data["sent"] = {t: d for t, d in sent.items() if date.fromisoformat(d) > cutoff}
    with open(STATE_FILE, "w") as fh:
        json.dump(data, fh, indent=2)


# ════════════════════════════════════════════════════════════════════════════
# EMAIL
# ════════════════════════════════════════════════════════════════════════════

def format_digest(picks, rejected):
    today = datetime.now().strftime("%A, %B %d, %Y")
    L = [f"VALUE SCREENER - {today}", "=" * 60, ""]
    if not picks:
        L.append("No candidates cleared the bar today.")
    for i, c in enumerate(picks, 1):
        f, t, v, q, cat = c.fund, c.tech, c.val, c.qual, c.cat
        val_line = f"P/E in {v.pe_percentile*100:.0f}th pct of 5y range" if not _nan(v.pe_percentile) \
            else (f"yield {v.yield_vs_norm:.2f}x its 5y avg" if not _nan(v.yield_vs_norm) else "see multiples")
        L += [
            f"{i}. {f.ticker}  ({f.exchange}, {f.sector})        SCORE {c.composite}/100",
            f"   Pullback   {t.pullback_pct*100:4.1f}% off 52w high   RSI {t.rsi:.0f}"
            f"   {'(uptrend intact)' if t.above_200ma else '(below 200ma - caution)'}",
            f"   Valuation  {val_line}   yield {f.dividend_yield:.1f}%   {'CHEAP' if v.cheap else 'fair'}",
            f"   Quality    F-score {q.fscore}/9   ROE {f.roe:.0f}%   D/E {f.debt_to_equity:.0f}",
            f"   Why down   [{cat.category}] {cat.reason}",
            f"   Source     {cat.source}", "",
        ]
    if rejected:
        L += ["-" * 60, "Screened out (corrected but failed a gate):"]
        L += [f"   {c.fund.ticker:8} - {c.vetoed_for}" for c in rejected]
        L.append("")
    L += ["-" * 60,
          "Research tool, not investment advice. Verify everything before acting.",
          "Candidates are screening output; the buy decision is yours."]
    return "\n".join(L)


def send_email(body, subject="Daily Value Screen"):
    sender, pw, to = os.getenv("EMAIL_FROM"), os.getenv("EMAIL_APP_PASSWORD"), os.getenv("EMAIL_TO")
    if not (sender and pw and to):
        print("[no email creds set - printing digest instead]\n")
        print(body)
        return
    import smtplib
    from email.mime.text import MIMEText
    msg = MIMEText(body)
    msg["Subject"], msg["From"], msg["To"] = subject, sender, to
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(sender, pw)
        s.send_message(msg)
    print(f"Emailed digest to {to}")


# ════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════

def build_provider():
    return YFinanceProvider() if PROVIDER == "yfinance" else SyntheticProvider()


def build_classifier():
    return {"groq": GroqClassifier, "anthropic": AnthropicClassifier,
            "mock": MockClassifier}[CLASSIFIER]()


def main():
    print(f"Running screen  [provider={PROVIDER}  classifier={CLASSIFIER}]")
    cooldown = THRESHOLDS["cooldown_days"]
    recently_sent, state = load_recently_sent(cooldown)
    picks, rejected = run_screen(build_provider(), build_classifier(), recently_sent)
    send_email(format_digest(picks, rejected))
    record_sent(state, [c.fund.ticker for c in picks], cooldown)
    print(f"Done. {len(picks)} emailed, {len(rejected)} screened out.")


if __name__ == "__main__":
    main()
