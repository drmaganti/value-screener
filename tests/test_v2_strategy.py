"""Behavioral tests for the V2 screening strategy."""
from datetime import date

import value_screener as core
import value_screener_v2 as v2


SYN = core.SyntheticProvider()


def _healthy_fund():
    f = SYN.fundamentals("JNJ")
    f.market_cap_b = 100
    f.next_earnings = date.today() + core.timedelta(days=40)
    # Make the baseline unambiguously cheap with broad valuation coverage.
    f.pe = 10.0
    f.pb = 1.2
    f.ps = 1.5
    f.ev_ebitda = 8.0
    f.fcf = 7e9
    f.price = 100.0
    f.target_mean = 120.0
    f.num_analysts = 10
    return f


def _candidate(f=None):
    f = f or _healthy_fund()
    tech = core.TechnicalSignals(
        pullback_pct=0.20,
        rsi=48,
        above_200ma=False,
        below_50ma=False,
        days_in_decline=90,
        score=70,
    )
    c = core.Candidate(fund=f, tech=tech)
    c.cat = core.CatalystVerdict(
        "market", True, "broad-market dip", "src", veto=False
    )
    return c


def test_v2_hard_gates_expensive_stock_even_when_other_lenses_are_strong():
    f = _healthy_fund()
    f.pe = 60.0
    f.pb = 9.0
    f.ps = 12.0
    f.ev_ebitda = 25.0
    f.fcf = 0.0
    f.pe_5y_low = f.pe_5y_high = None
    f.dividend_yield = 0.0
    f.div_yield_5y_avg = None
    f.target_mean = 95.0

    c = v2.evaluate_v2(_candidate(f), ["broad market dip"])
    assert c.vetoed_for is not None
    assert "valuation floor" in c.vetoed_for
    assert c.composite == 0


def test_v2_requires_broad_valuation_coverage():
    f = _healthy_fund()
    f.pe = float("nan")
    f.ev_ebitda = float("nan")
    f.fcf = float("nan")
    f.pb = 1.0
    f.ps = 1.0
    f.pe_5y_low = f.pe_5y_high = None
    f.dividend_yield = 0.0
    f.div_yield_5y_avg = None
    f.num_analysts = 0

    c = v2.evaluate_v2(_candidate(f), ["broad market dip"])
    assert c.vetoed_for is not None
    assert "valuation coverage" in c.vetoed_for


def test_v2_quality_does_not_fail_missing_interest_coverage_by_itself():
    f = _healthy_fund()
    f.interest_coverage = float("nan")
    q = v2.compute_quality_v2(f)
    assert q.healthy
    assert q.available_checks >= v2.V2["min_quality_observations"]


def test_v2_rewards_stabilized_dislocation_over_fresh_falling_knife():
    # Same approximate high and ending price. The first series made its peak only
    # ~20 sessions ago; the second peaked much earlier and has started rebounding.
    fresh = [100.0] * 230 + [
        120.0 - (30.0 * i / 21.0) for i in range(22)
    ]

    older = [100.0] * 130 + [120.0]
    older += [120.0 - (38.0 * i / 80.0) for i in range(1, 81)]
    older += [82.0 + (8.0 * i / 40.0) for i in range(1, 41)]

    fresh_sig = v2.compute_technicals_v2(core.PriceHistory("FRESH", fresh))
    older_sig = v2.compute_technicals_v2(core.PriceHistory("OLDER", older))

    assert fresh_sig.days_in_decline < 30
    assert older_sig.days_in_decline >= 90
    assert older_sig.stabilizing
    assert older_sig.score > fresh_sig.score


class ExactPriceProvider:
    def __init__(self, prices):
        self._prices = prices

    def price_on_or_after(self, ticker, target_date):
        return self._prices.get((ticker, target_date))


def test_outcomes_use_each_horizons_exact_target_price():
    pick = date(2026, 1, 1)
    provider = ExactPriceProvider(
        {
            ("ABC", date(2026, 1, 8)): 105.0,
            ("SPY", date(2026, 1, 8)): 102.0,
            ("ABC", date(2026, 1, 31)): 120.0,
            ("SPY", date(2026, 1, 31)): 110.0,
        }
    )
    log = {
        "picks": [
            {
                "ticker": "ABC",
                "pick_date": pick.isoformat(),
                "pick_price": 100.0,
                "benchmark": "SPY",
                "bench_at_pick": 100.0,
                "outcomes": {k: None for k in core.HORIZONS},
                "outcomes_bench": {k: None for k in core.HORIZONS},
            }
        ]
    }

    filled = v2.update_outcomes_v2(provider, log, date(2026, 2, 5))
    row = log["picks"][0]
    assert filled == 2
    assert row["outcomes"]["1w"] == 5.0
    assert row["outcomes"]["1m"] == 20.0
    assert row["outcomes_bench"]["1w"] == 2.0
    assert row["outcomes_bench"]["1m"] == 10.0


def test_track_record_stats_ignore_nan_outcomes():
    log = {
        "picks": [
            {
                "outcomes": {"1m": float("nan")},
                "outcomes_bench": {"1m": 2.0},
            },
            {
                "outcomes": {"1m": 10.0},
                "outcomes_bench": {"1m": 4.0},
            },
        ]
    }
    stats = v2.track_record_stats_v2(log)
    assert stats["n"] == 1
    assert stats["beats"] == 1
    assert stats["avg"] == 10.0


def test_tsx_uses_canadian_benchmark_but_us_uses_spy():
    assert v2.benchmark_for_ticker("RY.TO") == "XIU.TO"
    assert v2.benchmark_for_ticker("AAPL") == "SPY"
