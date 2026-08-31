from datetime import date

import reporting_integrity as ri


class FakeProvider:
    def __init__(self, after=None, before=None):
        self.after = after or {}
        self.before = before or {}

    def price_on_or_after(self, ticker, target_date):
        return self.after.get((ticker, target_date.isoformat()))

    def price_on_or_before(self, ticker, target_date):
        return self.before.get((ticker, target_date.isoformat()))


def _v1_record(ticker="ABC", pick_date="2026-07-01", pick_price=100.0):
    return {
        "ticker": ticker,
        "pick_date": pick_date,
        "pick_price": pick_price,
        "signals": {"valuation_parts": {"pe": 80}},
        "outcomes": {"1w": 99.0, "1m": 88.0, "3m": None, "6m": None},
        "outcomes_bench": {"1w": 77.0, "1m": 66.0, "3m": None, "6m": None},
    }


def test_backfill_preserves_legacy_and_uses_exact_horizon():
    r = _v1_record()
    log = {"picks": [r]}
    provider = FakeProvider(
        after={
            ("ABC", "2026-07-08"): 110.0,
            ("SPY", "2026-07-08"): 505.0,
            ("ABC", "2026-07-31"): 120.0,
            ("SPY", "2026-07-31"): 510.0,
        },
        before={("SPY", "2026-07-01"): 500.0},
    )
    filled = ri.backfill_legacy_exact_horizons(provider, log, date(2026, 8, 31))
    assert filled == 2
    assert r["legacy_outcomes_snapshot"]["1m"] == 88.0
    assert r["outcomes"]["1m"] == 88.0
    assert r["corrected_outcomes"]["1w"] == 10.0
    assert r["corrected_outcomes"]["1m"] == 20.0
    assert r["corrected_outcomes_bench"]["1m"] == 2.0


def test_tsx_legacy_uses_xiu_benchmark():
    r = _v1_record(ticker="ABC.TO")
    log = {"picks": [r]}
    provider = FakeProvider(
        after={
            ("ABC.TO", "2026-07-08"): 105.0,
            ("XIU.TO", "2026-07-08"): 41.0,
            ("ABC.TO", "2026-07-31"): 110.0,
            ("XIU.TO", "2026-07-31"): 42.0,
        },
        before={("XIU.TO", "2026-07-01"): 40.0},
    )
    ri.backfill_legacy_exact_horizons(provider, log, date(2026, 8, 31))
    assert r["corrected_benchmark"] == "XIU.TO"
    assert r["corrected_outcomes_bench"]["1m"] == 5.0


def test_stats_separate_v1_v2_and_use_comparable_denominator():
    v1 = _v1_record()
    v1["corrected_outcomes"] = {"1m": 10.0}
    v1["corrected_outcomes_bench"] = {"1m": 5.0}
    v2 = {
        "ticker": "XYZ",
        "pick_date": "2026-07-01",
        "strategy_version": "v2",
        "signals": {},
        "outcomes": {"1m": 3.0},
        "outcomes_bench": {"1m": 4.0},
    }
    v2_missing_bench = {
        "ticker": "NOPE",
        "pick_date": "2026-07-01",
        "strategy_version": "v2",
        "signals": {},
        "outcomes": {"1m": 9.0},
        "outcomes_bench": {"1m": None},
    }
    stats = ri.performance_by_strategy({"picks": [v1, v2, v2_missing_bench]})
    assert stats["v1"]["comparable_n"] == 1
    assert stats["v1"]["beats"] == 1
    assert stats["v1"]["avg_excess"] == 5.0
    assert stats["v2"]["return_n"] == 2
    assert stats["v2"]["comparable_n"] == 1
    assert stats["v2"]["beats"] == 0
    assert stats["v2"]["avg_excess"] == -1.0


def test_pre_v1_is_not_counted_as_v1():
    old = _v1_record()
    old["signals"] = {}
    assert ri.strategy_bucket(old) == "pre_v1"


def test_weekly_workflow_manual_runs_cannot_persist_or_email():
    from pathlib import Path

    text = Path(".github/workflows/weekly-screen.yml").read_text()
    assert "if: github.event_name == 'schedule'" in text
    assert "OFFICIAL_RUN: ${{ github.event_name == 'schedule' && '1' || '0' }}" in text
    assert "EMAIL_TO: ${{ github.event_name == 'schedule' && secrets.EMAIL_TO || '' }}" in text
