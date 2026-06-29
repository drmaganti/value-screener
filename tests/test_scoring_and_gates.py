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

def test_valuation_pe_history_method_still_computes_percentile():
    f = healthy_fund()
    f.pe, f.pe_5y_low, f.pe_5y_high = 15.0, 10.0, 20.0
    assert abs(vs.compute_valuation(f).pe_percentile - 0.5) < 1e-6   # mid-band


def test_valuation_cheap_across_all_metrics():
    # Cheap on everything -> high blended score, cheap flag set.
    f = healthy_fund()
    f.pe, f.pe_5y_low, f.pe_5y_high = 8.0, 7.0, 30.0   # low P/E (high earnings yield) + low in band
    f.pb, f.ps, f.ev_ebitda = 1.0, 1.0, 7.0            # cheap multiples
    f.market_cap_b, f.fcf = 100, 8e9                   # 8% FCF yield
    v = vs.compute_valuation(f)
    assert v.cheap and v.score >= 70


def test_valuation_expensive_across_all_metrics():
    f = healthy_fund()
    f.pe, f.pe_5y_low, f.pe_5y_high = 60.0, 10.0, 65.0
    f.pb, f.ps, f.ev_ebitda = 9.0, 12.0, 25.0
    f.market_cap_b, f.fcf = 100, 0.0
    f.dividend_yield, f.div_yield_5y_avg = 0.0, None
    v = vs.compute_valuation(f)
    assert not v.cheap and v.score <= 40


def test_valuation_scores_non_dividend_payer():
    # THE point of this rebuild: a company paying NO dividend still gets a real,
    # non-hollow score from earnings yield, FCF yield, EV/EBITDA, P/B, P/S.
    f = healthy_fund()
    f.dividend_yield, f.div_yield_5y_avg = 0.0, None   # pays nothing
    f.pe_5y_low = f.pe_5y_high = None                  # no history band either
    f.pe, f.pb, f.ps, f.ev_ebitda = 12.0, 1.5, 1.5, 8.0
    f.market_cap_b, f.fcf = 100, 6e9
    v = vs.compute_valuation(f)
    assert "yield_vs_history" not in v.parts           # no dividend signal
    assert {"earnings_yield", "fcf_yield", "ev_ebitda", "pb", "ps"} <= set(v.parts)
    assert v.cheap and v.score >= 60                   # still gets a strong read


def test_valuation_only_blends_available_metrics():
    f = healthy_fund()
    f.pe = float("nan"); f.pe_5y_low = f.pe_5y_high = None
    f.ev_ebitda = float("nan"); f.fcf = float("nan")
    f.dividend_yield, f.div_yield_5y_avg = 0.0, None
    f.num_analysts = 0                                 # exclude analyst upside too
    f.pb, f.ps = 1.2, 1.2                              # only these two have data
    v = vs.compute_valuation(f)
    assert set(v.parts) == {"pb", "ps"}                # blends just what's present


def test_valuation_never_crashes_on_missing_data():
    f = healthy_fund()
    for attr in ("pe", "pb", "ps", "ev_ebitda", "fcf"):
        setattr(f, attr, float("nan"))
    f.pe_5y_low = f.pe_5y_high = None
    f.dividend_yield, f.div_yield_5y_avg = 0.0, None
    f.num_analysts = 0                                 # no analyst coverage either
    v = vs.compute_valuation(f)
    assert 0 <= v.score <= 100 and v.parts == {}       # neutral 50, no crash


def test_analyst_upside_high_when_target_far_above_price():
    f = healthy_fund()
    f.price, f.target_mean, f.num_analysts = 100.0, 140.0, 12   # +40% upside, well-covered
    v = vs.compute_valuation(f)
    assert v.parts["analyst_upside"] >= 90


def test_analyst_upside_low_when_price_above_target():
    f = healthy_fund()
    f.price, f.target_mean, f.num_analysts = 100.0, 95.0, 12     # trading above consensus target
    v = vs.compute_valuation(f)
    assert v.parts["analyst_upside"] <= 10


def test_analyst_upside_skipped_on_thin_coverage():
    f = healthy_fund()
    f.price, f.target_mean, f.num_analysts = 100.0, 140.0, 2     # only 2 analysts -> not trusted
    assert "analyst_upside" not in vs.compute_valuation(f).parts


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
