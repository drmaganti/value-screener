"""
Value Screener V2 strategy layer.

V2 keeps the proven data providers, catalyst classifier, reporting, and cache from
value_screener.py, but changes the selection policy based on the first live
cohorts:

- value is an eligibility gate, not merely a weighted preference;
- the technical lens rewards dislocation + stabilization rather than a falling
  knife simply for becoming more oversold;
- quality is normalized over available observations so unavailable numeric data
  is not automatically a failure;
- outcomes are measured at each horizon's first trading day on/after the target
  date and non-finite values are repaired/ignored;
- TSX picks use XIU.TO as their benchmark while legacy V1 records retain SPY;
- every post-pullback candidate is logged, including rejects/near-misses, so
  future tuning has a counterfactual dataset.
"""

from __future__ import annotations

import copy
import json
import math
import os
from datetime import date, timedelta

import value_screener as core

STRATEGY_VERSION = "v2"
CANDIDATES_LOG = "candidate_log.json"

V2 = {
    "min_valuation_score": 60.0,
    "min_valuation_metrics": 3,
    "min_composite": 62.0,
    "strong_composite": 70.0,
    "tsx_benchmark": "XIU.TO",
    "us_benchmark": "SPY",
    "min_quality_observations": 5,
}

VALUE_EVIDENCE_PARTS = {
    "earnings_yield",
    "fcf_yield",
    "ev_ebitda",
    "pb",
    "ps",
    "vs_sector",
    "pe_vs_history",
}


_V1_ANALYSIS_INPUTS = core._analysis_inputs


def analysis_inputs_v2(c):
    """Preserve the report schema but remove V1's 'fresh dip favors a bounce' claim."""
    d = _V1_ANALYSIS_INPUTS(c)
    if c.tech.days_in_decline < 30:
        d["reversal_note"] = (
            "a fresh decline; V2 treats it as lower-confidence until stabilization appears"
        )
    elif getattr(c.tech, "stabilizing", False):
        d["reversal_note"] = (
            "the decline is older and short-term price action is showing stabilization"
        )
    else:
        d["reversal_note"] = (
            "the decline is established, but short-term stabilization still needs confirmation"
        )
    return d


def _finite(x):
    try:
        return x is not None and math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def benchmark_for_ticker(ticker):
    return V2["tsx_benchmark"] if ticker.endswith(".TO") else V2["us_benchmark"]


def _rsi_stability_score(rsi):
    """0-25. Rewards a recovery/neutral zone; does not reward extreme oversold."""
    if rsi < 25:
        return 0.0
    if rsi < 40:
        return 5.0 + (rsi - 25.0) / 15.0 * 13.0
    if rsi <= 65:
        return 25.0
    if rsi <= 75:
        return 25.0 - (rsi - 65.0) / 10.0 * 12.0
    return 5.0


def compute_technicals_v2(p):
    """Dislocation + stabilization score.

    A large drawdown still opens the opportunity set, but V2 explicitly penalizes
    a fresh slide and rewards evidence that selling pressure has started to
    stabilize. This is intentionally different from V1's "lower RSI = better"
    formulation.
    """
    pullback = (p.high_52w - p.last) / p.high_52w if p.high_52w else 0.0
    rsi = core._rsi(p.closes)
    above_200 = p.last > core._ma(p.closes, 200)
    below_50 = p.last < core._ma(p.closes, 50)
    days = core._days_in_decline(p.closes)

    # 0 at the 10% candidate floor; saturates around a 35% dislocation.
    dislocation = 30.0 * _clamp((pullback - 0.10) / 0.25)

    # First live cohorts strongly underperformed when the peak was <30 days ago.
    # Ramp conviction from 30 to 120 days rather than rewarding a fresh knife.
    maturity = 0.0 if days < 30 else 10.0 + 15.0 * _clamp((days - 30) / 90.0)

    rsi_component = _rsi_stability_score(rsi)

    ret20 = 0.0
    if len(p.closes) >= 21 and p.closes[-21]:
        ret20 = p.last / p.closes[-21] - 1.0

    # Direct stabilization confirmation gets more credit than merely remaining
    # above a long-term average.
    if ret20 > 0.0:
        stabilization = 20.0
    elif not below_50:
        stabilization = 15.0
    elif above_200:
        stabilization = 8.0
    else:
        stabilization = 0.0

    raw = round(dislocation + maturity + rsi_component + stabilization, 1)
    out = core.TechnicalSignals(
        pullback_pct=pullback,
        rsi=rsi,
        above_200ma=above_200,
        below_50ma=below_50,
        days_in_decline=days,
        score=raw,
    )
    # Dynamic fields preserve compatibility with the V1 dataclass while giving
    # the learning log richer V2 factors.
    out.return_20d = round(ret20 * 100.0, 2)
    out.stabilizing = bool(ret20 > 0.0 or not below_50)
    out.score_parts = {
        "dislocation": round(dislocation, 1),
        "maturity": round(maturity, 1),
        "rsi_stability": round(rsi_component, 1),
        "stabilization": round(stabilization, 1),
    }
    return out


def compute_quality_v2(f):
    """Normalize quality over fields that are actually observable.

    V1 implicitly failed unavailable numeric observations (notably interest
    coverage from yfinance). V2 skips unavailable numeric checks and converts the
    resulting pass rate back to the familiar 0-9 display scale.
    """
    checks = []

    def add(label, available, passed):
        if available:
            checks.append((label, bool(passed)))

    # These two are retained as provider-level profitability signals.
    add("net income positive", True, f.net_income_positive)
    add("operating cash flow +", True, f.op_cash_flow_positive)

    add("free cash flow +", _finite(f.fcf), f.fcf > 0 if _finite(f.fcf) else False)
    add("ROE > 12%", _finite(f.roe), f.roe > 12 if _finite(f.roe) else False)
    add(
        "low leverage (D/E<100)",
        _finite(f.debt_to_equity),
        f.debt_to_equity < 100 if _finite(f.debt_to_equity) else False,
    )
    add(
        "interest coverage > 4",
        _finite(f.interest_coverage),
        f.interest_coverage > 4 if _finite(f.interest_coverage) else False,
    )
    add(
        "current ratio > 1",
        _finite(f.current_ratio),
        f.current_ratio > 1 if _finite(f.current_ratio) else False,
    )
    add(
        "gross margin > 30%",
        _finite(f.gross_margin),
        f.gross_margin > 30 if _finite(f.gross_margin) else False,
    )

    available = len(checks)
    passed = sum(ok for _, ok in checks)
    ratio = passed / available if available else 0.0
    fscore = int(round(ratio * 9.0))
    score = round(ratio * 100.0, 1)
    healthy = (
        available >= V2["min_quality_observations"]
        and fscore >= core.THRESHOLDS["min_quality_score"]
    )
    notes = [label for label, ok in checks if not ok]
    if available < V2["min_quality_observations"]:
        notes.append(f"insufficient quality coverage ({available} observations)")

    out = core.QualitySignals(fscore=fscore, healthy=healthy, notes=notes, score=score)
    out.available_checks = available
    out.passed_checks = passed
    return out


def _valuation_coverage(val):
    return sum(1 for k in val.parts if k in VALUE_EVIDENCE_PARTS)


def evaluate_v2(c, news, sector_medians=None, today=None):
    """Apply hard V2 eligibility gates, then rank the survivors."""
    today = today or date.today()
    c.val = core.compute_valuation(c.fund, sector_medians)
    c.qual = compute_quality_v2(c.fund)
    sent = core.score_sentiment(news)
    c.cat.score = core.score_catalyst(c.cat)

    valuation_coverage = _valuation_coverage(c.val)
    c.val.coverage = valuation_coverage

    if c.cat.veto:
        c.vetoed_for = f"catalyst: {c.cat.reason}"
    elif c.fund.market_cap_b < core.THRESHOLDS["min_market_cap_b"]:
        c.vetoed_for = f"below blue-chip cap (${c.fund.market_cap_b:.0f}B)"
    elif (
        c.fund.next_earnings
        and 0 <= (c.fund.next_earnings - today).days <= core.THRESHOLDS["earnings_blackout"]
    ):
        c.vetoed_for = f"earnings in {(c.fund.next_earnings - today).days}d"
    elif not c.qual.healthy:
        c.vetoed_for = (
            f"quality floor: normalized F-score {c.qual.fscore}/9 "
            f"({getattr(c.qual, 'available_checks', 0)} observations)"
        )
    elif valuation_coverage < V2["min_valuation_metrics"]:
        c.vetoed_for = (
            f"valuation coverage: {valuation_coverage} usable value metrics "
            f"(<{V2['min_valuation_metrics']})"
        )
    elif c.val.score < V2["min_valuation_score"]:
        c.vetoed_for = (
            f"valuation floor: {c.val.score:.1f}/100 "
            f"(<{V2['min_valuation_score']:.0f})"
        )

    if c.vetoed_for:
        return c

    c.composite = round(
        core.WEIGHTS["technical"] * c.tech.score
        + core.WEIGHTS["valuation"] * c.val.score
        + core.WEIGHTS["quality"] * c.qual.score
        + core.WEIGHTS["catalyst"] * c.cat.score
        + core.WEIGHTS["sentiment"] * sent,
        1,
    )
    c.conviction = "strong" if c.composite >= V2["strong_composite"] else "candidate"
    return c


def _price_on_or_after(provider, ticker, target_date):
    """First close on/after target_date, allowing weekends/holidays."""
    custom = getattr(provider, "price_on_or_after", None)
    if callable(custom):
        value = custom(ticker, target_date)
        return float(value) if _finite(value) else None

    # Live provider: query a narrow date range around the exact horizon.
    if isinstance(provider, core.YFinanceProvider):
        try:
            end = target_date + timedelta(days=8)
            hist = provider._t(ticker).history(
                start=target_date.isoformat(), end=end.isoformat()
            )
            if hist is None or len(hist) == 0:
                return None
            value = float(hist["Close"].iloc[0])
            return value if _finite(value) else None
        except Exception:
            return None

    # Synthetic/demo mode has no calendar-aware historical API.
    if isinstance(provider, core.SyntheticProvider):
        value = provider.last_price(ticker)
        return float(value) if _finite(value) else None

    return None


def _needs_outcome(value):
    return value is None or not _finite(value)


def update_outcomes_v2(provider, log, today):
    """Fill each due horizon at its own target date; repair invalid NaNs."""
    filled = 0
    for r in log.get("picks", []):
        pick_date = date.fromisoformat(r["pick_date"])
        pick_price = r.get("pick_price")
        if not _finite(pick_price) or float(pick_price) <= 0:
            continue

        # Legacy records used SPY and stored a SPY entry price. Never silently
        # reinterpret those old bench_at_pick values as a Canadian benchmark.
        benchmark = r.get("benchmark") or core.BENCHMARK
        bench_at_pick = r.get("bench_at_pick")

        r.setdefault("outcomes", {})
        r.setdefault("outcomes_bench", {})

        for horizon, days in core.HORIZONS.items():
            target = pick_date + timedelta(days=days)
            if today < target or not _needs_outcome(r["outcomes"].get(horizon)):
                continue

            px = _price_on_or_after(provider, r["ticker"], target)
            if not _finite(px):
                continue

            ret = (float(px) / float(pick_price) - 1.0) * 100.0
            if not _finite(ret):
                continue

            bret = None
            if _finite(bench_at_pick) and float(bench_at_pick) > 0:
                bpx = _price_on_or_after(provider, benchmark, target)
                if _finite(bpx):
                    bret = (float(bpx) / float(bench_at_pick) - 1.0) * 100.0

            r["outcomes"][horizon] = round(ret, 2)
            r["outcomes_bench"][horizon] = round(bret, 2) if _finite(bret) else None
            filled += 1
    return filled


def track_record_stats_v2(log):
    """NaN-safe one-month summary across whatever benchmark each pick recorded."""
    matured = []
    for r in log.get("picks", []):
        value = r.get("outcomes", {}).get("1m")
        if _finite(value):
            matured.append(r)

    if not matured:
        return None

    comparable = [
        r
        for r in matured
        if _finite(r.get("outcomes_bench", {}).get("1m"))
    ]
    beats = sum(
        r["outcomes"]["1m"] > r["outcomes_bench"]["1m"]
        for r in comparable
    )
    avg = sum(float(r["outcomes"]["1m"]) for r in matured) / len(matured)
    avg_b = (
        sum(float(r["outcomes_bench"]["1m"]) for r in comparable) / len(comparable)
        if comparable
        else 0.0
    )
    return {
        "n": len(matured),
        "beats": beats,
        "comparable_n": len(comparable),
        "avg": round(avg, 1),
        "avg_bench": round(avg_b, 1),
    }


def _signals_for_log(c):
    return {
        "technical": c.tech.score,
        "valuation": c.val.score,
        "quality": c.qual.score,
        "catalyst": c.cat.score,
        "pullback_pct": round(c.tech.pullback_pct, 3),
        "rsi": round(c.tech.rsi),
        "days_in_decline": c.tech.days_in_decline,
        "return_20d": getattr(c.tech, "return_20d", None),
        "stabilizing": getattr(c.tech, "stabilizing", None),
        "technical_parts": getattr(c.tech, "score_parts", {}),
        "fscore": c.qual.fscore,
        "quality_observations": getattr(c.qual, "available_checks", None),
        "catalyst_category": c.cat.category,
        "valuation_coverage": getattr(c.val, "coverage", _valuation_coverage(c.val)),
        "valuation_parts": c.val.parts,
    }


def record_picks_v2(log, picks, provider, today):
    """Write V2 picks with explicit strategy and benchmark metadata."""
    benchmark_prices = {}
    for c in picks:
        benchmark = benchmark_for_ticker(c.fund.ticker)
        if benchmark not in benchmark_prices:
            benchmark_prices[benchmark] = provider.last_price(benchmark)
        bench_price = benchmark_prices[benchmark]

        log.setdefault("picks", []).append(
            {
                "ticker": c.fund.ticker,
                "name": c.fund.name,
                "exchange": c.fund.exchange,
                "sector": c.fund.sector,
                "pick_date": today.isoformat(),
                "pick_price": round(c.fund_price, 2),
                "benchmark": benchmark,
                "bench_at_pick": (
                    round(float(bench_price), 2) if _finite(bench_price) else None
                ),
                "strategy_version": STRATEGY_VERSION,
                "conviction": getattr(c, "conviction", "candidate"),
                "composite": c.composite,
                "signals": _signals_for_log(c),
                "outcomes": {k: None for k in core.HORIZONS},
                "outcomes_bench": {k: None for k in core.HORIZONS},
            }
        )


def load_candidate_log():
    try:
        with open(core._p(CANDIDATES_LOG)) as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {"candidates": []}
    except (FileNotFoundError, json.JSONDecodeError):
        return {"candidates": []}


def save_candidate_log(log):
    with open(core._p(CANDIDATES_LOG), "w") as fh:
        json.dump(log, fh, indent=2, default=str)


def update_candidate_outcomes(provider, log, today):
    # Reuse exactly the same horizon logic/schema as the selected-pick log.
    proxy = {"picks": log.get("candidates", [])}
    return update_outcomes_v2(provider, proxy, today)


def record_candidate_observations(provider, log, candidates, qualifiers, today):
    """Log all post-pullback candidates, including hard rejects and near-misses."""
    selected = {c.fund.ticker for c in qualifiers}
    benchmark_prices = {}

    for c in candidates:
        benchmark = benchmark_for_ticker(c.fund.ticker)
        if benchmark not in benchmark_prices:
            benchmark_prices[benchmark] = provider.last_price(benchmark)
        bench_price = benchmark_prices[benchmark]

        if c.vetoed_for:
            reason = c.vetoed_for
        elif c.composite < V2["min_composite"]:
            reason = (
                f"composite floor: {c.composite:.1f}/100 "
                f"(<{V2['min_composite']:.0f})"
            )
        else:
            reason = None

        log.setdefault("candidates", []).append(
            {
                "ticker": c.fund.ticker,
                "sector": c.fund.sector,
                "screen_date": today.isoformat(),
                # Keep pick_date too so the common outcome updater can operate.
                "pick_date": today.isoformat(),
                "pick_price": round(c.fund_price, 2),
                "benchmark": benchmark,
                "bench_at_pick": (
                    round(float(bench_price), 2) if _finite(bench_price) else None
                ),
                "strategy_version": STRATEGY_VERSION,
                "selected": c.fund.ticker in selected,
                "rejection_reason": reason,
                "composite": c.composite,
                "v1_shadow_qualified": getattr(c, "v1_shadow_qualified", False),
                "v1_shadow_composite": getattr(c, "v1_shadow_composite", 0.0),
                "v1_shadow_vetoed_for": getattr(c, "v1_shadow_vetoed_for", None),
                "signals": _signals_for_log(c),
                "outcomes": {k: None for k in core.HORIZONS},
                "outcomes_bench": {k: None for k in core.HORIZONS},
            }
        )


def run_screen_v2(
    provider,
    classifier,
    analyst,
    exclude,
    sector_medians=None,
    fund_cache=None,
    today=None,
):
    universe = core.load_universe()
    tickers = [t for ts in universe.values() for t in ts if t not in exclude]
    print(
        f"  scanning {len(tickers)} names "
        f"({len(exclude)} excluded as open bets) [strategy={STRATEGY_VERSION}]"
    )
    today = today or date.today()

    prices = core.scan_prices(provider, tickers)
    survivors = []
    for tk, ph in prices.items():
        tech = compute_technicals_v2(ph)
        if core.is_corrected(tech):
            try:
                f = provider.fundamentals(tk)
                if fund_cache is not None:
                    core.cache_put(fund_cache, f, today)
                c = core.Candidate(fund=f, tech=tech)
                c.fund_price = ph.last
                # Run the legacy scoring logic in shadow on the exact same stock/date.
                # This gives the scheduled V1-vs-V2 review a contemporaneous control.
                c.v1_tech = core.compute_technicals(ph)
                survivors.append(c)
            except Exception:
                pass

    scored, rejected = [], []
    for c in survivors:
        news = provider.recent_news(c.fund.ticker)
        c.cat = classifier.classify(c.fund.ticker, news)
        c = evaluate_v2(c, news, sector_medians, today=today)

        shadow = core.Candidate(fund=c.fund, tech=c.v1_tech)
        shadow.cat = copy.deepcopy(c.cat)
        shadow = core.evaluate(shadow, news, sector_medians)
        c.v1_shadow_composite = shadow.composite
        c.v1_shadow_vetoed_for = shadow.vetoed_for
        c.v1_shadow_qualified = bool(
            not shadow.vetoed_for
            and shadow.composite >= core.THRESHOLDS["min_composite"]
        )

        (rejected if c.vetoed_for else scored).append(c)

    scored.sort(key=lambda x: x.composite, reverse=True)
    qualifiers = [
        c for c in scored if c.composite >= V2["min_composite"]
    ][: core.THRESHOLDS["max_picks_per_run"]]
    featured = qualifiers[: core.THRESHOLDS["featured"]]

    for c in featured:
        c.analysis = analyst.write(c)

    all_candidates = rejected + scored
    return qualifiers, featured, rejected, all_candidates


def _make_report(today, featured, qualifiers, picks, stats):
    import email_report

    html, text = email_report.build(today, featured, qualifiers, picks, stats)
    # V2 can use SPY or XIU.TO, so avoid claiming every comparison is S&P.
    html = html.replace("S&amp;P", "benchmark")
    text = text.replace("S&P", "benchmark")
    return html, text


def main():
    today = date.today()
    print(
        f"Weekly screen  [provider={core.PROVIDER}  classifier={core.CLASSIFIER} "
        f"strategy={STRATEGY_VERSION}]  {today}"
    )
    provider = core.build_provider()

    log = core.load_log()
    filled = update_outcomes_v2(provider, log, today)
    print(f"  updated/repaired {filled} pick outcome slots")

    candidate_log = load_candidate_log()
    candidate_filled = update_candidate_outcomes(provider, candidate_log, today)
    print(f"  updated/repaired {candidate_filled} candidate outcome slots")

    universe = core.load_universe()
    all_tickers = [t for ts in universe.values() for t in ts]
    fund_cache = core.load_fund_cache()
    refreshed = core.refresh_fundamentals_cache(
        provider, fund_cache, all_tickers, today
    )
    sector_medians = core.compute_sector_medians(fund_cache)
    print(
        f"  refreshed {refreshed} fundamentals; sector medians for "
        f"{len(sector_medians)} sectors ({len(fund_cache)} names cached)"
    )

    exclude = core.open_tickers(log, today)
    core._analysis_inputs = analysis_inputs_v2
    qualifiers, featured, rejected, all_candidates = run_screen_v2(
        provider,
        core.build_classifier(),
        core.build_analyst(),
        exclude,
        sector_medians=sector_medians,
        fund_cache=fund_cache,
        today=today,
    )

    record_picks_v2(log, qualifiers, provider, today)
    record_candidate_observations(
        provider, candidate_log, all_candidates, qualifiers, today
    )

    core.save_log(log)
    save_candidate_log(candidate_log)
    core.save_fund_cache(fund_cache)

    stats = track_record_stats_v2(log)
    html, text = _make_report(today, featured, qualifiers, log["picks"], stats)
    core.send_email(
        html,
        text,
        f"Weekly Value Screen V2 - {today.strftime('%b %d, %Y')}",
    )
    print(
        f"Done. {len(qualifiers)} qualifiers ({len(featured)} featured), "
        f"{len(rejected)} hard-screened out, {len(all_candidates)} candidates logged."
    )


if __name__ == "__main__":
    main()
