"""
Tests for the scoring lenses and — more importantly — the veto gates.

The gate tests encode the safety invariants of the whole system: the things
that must ALWAYS hold no matter what data comes in. A value trap reaching the
buy list is the single worst failure this tool can have, so those cases are
written as hard guarantees, not nice-to-haves.
"""
import math
import value_screener as vs

SYN = vs.SyntheticProvider()


def healthy_fund():
    """A fundamentally sound name (clean_buy archetype, deterministic)."""
    return SYN.fundamentals("JNJ")


def trap_fund():
    """A financially weak name (value_trap archetype, deterministic)."""
    return SYN.fundamentals("INTC")


# ── Valuation lens ──────────────────────────────────────────────────────────

def test_valuation_pe_percentile_midband():
    f = healthy_fund()
    f.pe, f.pe_5y_low, f.pe_5y_high = 15.0, 10.0, 20.0
    f.div_yield_5y_avg = None                      # isolate the P/E signal
    assert abs(vs.compute_valuation(f).pe_percentile - 0.5) < 1e-6


def test_valuation_cheap_when_pe_near_band_bottom():
    f = healthy_fund()
    f.pe, f.pe_5y_low, f.pe_5y_high = 11.0, 10.0, 30.0
    f.div_yield_5y_avg = None
    assert vs.compute_valuation(f).cheap


def test_valuation_yield_above_history_reads_cheap():
    # No P/E band available -> the lens should fall back to yield-vs-its-own-
    # 5y-average, which is the signal yfinance actually provides.
    f = healthy_fund()
    f.pe_5y_low = f.pe_5y_high = None
    f.dividend_yield, f.div_yield_5y_avg = 4.0, 2.0   # yielding double its norm
    v = vs.compute_valuation(f)
    assert v.cheap and v.yield_vs_norm == 2.0


def test_valuation_never_crashes_on_missing_data():
    f = healthy_fund()
    f.pe = float("nan")
    f.pe_5y_low = f.pe_5y_high = None
    f.dividend_yield, f.div_yield_5y_avg = 0.0, None
    v = vs.compute_valuation(f)
    assert 0 <= v.score <= 100                       # neutral, not an exception


# ── Quality lens ────────────────────────────────────────────────────────────

def test_quality_healthy_clears_floor():
    q = vs.compute_quality(healthy_fund())
    assert q.fscore >= vs.THRESHOLDS["min_quality_score"] and q.healthy


def test_quality_trap_fails_floor():
    q = vs.compute_quality(trap_fund())
    assert q.fscore < vs.THRESHOLDS["min_quality_score"] and not q.healthy


# ── Catalyst & sentiment scoring ────────────────────────────────────────────

def test_catalyst_score_ordering():
    s, mk = vs.score_catalyst, lambda c: vs.CatalystVerdict(c, True, "", "")
    assert (s(mk("market")) > s(mk("sector"))
            > s(mk("one_off_operational")) > s(mk("structural")))


def test_sentiment_drops_on_negative_language():
    calm = vs.score_sentiment(["quiet, range-bound trading session"])
    rough = vs.score_sentiment(["company faces lawsuit after earnings miss"])
    assert rough < calm


# ── Veto gates: the safety invariants ───────────────────────────────────────

def _passing_candidate():
    """A candidate that should clear every gate, as a baseline to break."""
    f = healthy_fund()
    f.market_cap_b = 100
    f.next_earnings = vs.date.today() + vs.timedelta(days=40)
    tech = vs.TechnicalSignals(pullback_pct=0.15, rsi=28,
                               above_200ma=True, below_50ma=True, score=70)
    c = vs.Candidate(fund=f, tech=tech)
    c.cat = vs.CatalystVerdict("market", True, "broad-market dip", "src", veto=False)
    return c


def test_clean_candidate_scores_positive():
    c = vs.evaluate(_passing_candidate(), ["broad market dip"])
    assert c.vetoed_for is None and c.composite > 0


def test_structural_catalyst_is_always_vetoed():
    # THE critical invariant: a structural reason never yields a buy.
    c = _passing_candidate()
    c.cat = vs.CatalystVerdict("structural", False, "full-year guidance cut", "src", veto=True)
    c = vs.evaluate(c, ["company cuts full-year guidance"])
    assert c.vetoed_for is not None and c.composite == 0


def test_low_quality_is_always_vetoed():
    c = _passing_candidate()
    f = trap_fund()
    f.market_cap_b = 100
    f.next_earnings = vs.date.today() + vs.timedelta(days=40)
    c.fund = f
    c = vs.evaluate(c, ["broad market dip"])
    assert c.vetoed_for is not None and "quality" in c.vetoed_for


def test_below_blue_chip_cap_is_vetoed():
    c = _passing_candidate()
    c.fund.market_cap_b = 2.0                         # under the $10B floor
    c = vs.evaluate(c, ["broad market dip"])
    assert c.vetoed_for is not None and "cap" in c.vetoed_for


def test_earnings_blackout_is_vetoed():
    c = _passing_candidate()
    c.fund.next_earnings = vs.date.today() + vs.timedelta(days=1)   # too close
    c = vs.evaluate(c, ["broad market dip"])
    assert c.vetoed_for is not None and "earnings" in c.vetoed_for
