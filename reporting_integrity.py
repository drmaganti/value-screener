"""Reporting/data-integrity helpers for Value Screener V2.

The goal is to keep the historical audit trail intact while producing a clean,
comparable V1-vs-V2 performance record.
"""
from __future__ import annotations

import copy
import json
import math
import statistics
from datetime import date, timedelta

import value_screener as core
import value_screener_v2 as v2

V1_LABEL = "v1"
V2_LABEL = "v2"
PRE_V1_LABEL = "pre_v1"


def finite(value):
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def strategy_bucket(record):
    """Classify records without retroactively rewriting their historical schema.

    V2 records are explicit. V1 is the post-valuation-rebuild cohort, identified
    by valuation_parts being logged. Earlier picks are retained as pre-V1 legacy
    and excluded from V1-vs-V2 comparisons.
    """
    if record.get("strategy_version") == V2_LABEL:
        return V2_LABEL
    signals = record.get("signals") or {}
    if isinstance(signals.get("valuation_parts"), dict):
        return V1_LABEL
    return PRE_V1_LABEL


def _clean_outcome_map(values):
    values = values or {}
    return {k: (float(v) if finite(v) else None) for k, v in values.items()}


def snapshot_legacy_outcomes(log):
    """Preserve pre-V2 outcome values once, and normalize invalid NaN to null.

    This does not overwrite any finite legacy result. It only creates an audit
    snapshot and converts non-finite values in the working legacy maps to None so
    the log can be serialized as strict JSON.
    """
    changed = 0
    for record in log.get("picks", []):
        if strategy_bucket(record) == V2_LABEL:
            continue
        if "legacy_outcomes_snapshot" not in record:
            record["legacy_outcomes_snapshot"] = _clean_outcome_map(
                copy.deepcopy(record.get("outcomes", {}))
            )
            record["legacy_outcomes_bench_snapshot"] = _clean_outcome_map(
                copy.deepcopy(record.get("outcomes_bench", {}))
            )
            changed += 1
        for key in ("outcomes", "outcomes_bench"):
            values = record.setdefault(key, {})
            for horizon, value in list(values.items()):
                if value is not None and not finite(value):
                    values[horizon] = None
                    changed += 1
    return changed


def _price_on_or_before(provider, ticker, target_date):
    custom = getattr(provider, "price_on_or_before", None)
    if callable(custom):
        value = custom(ticker, target_date)
        return float(value) if finite(value) else None

    if isinstance(provider, core.YFinanceProvider):
        try:
            start = target_date - timedelta(days=8)
            end = target_date + timedelta(days=1)
            hist = provider._t(ticker).history(
                start=start.isoformat(), end=end.isoformat()
            )
            if hist is None or len(hist) == 0:
                return None
            value = float(hist["Close"].iloc[-1])
            return value if finite(value) else None
        except Exception:
            return None

    if isinstance(provider, core.SyntheticProvider):
        value = provider.last_price(ticker)
        return float(value) if finite(value) else None
    return None


def backfill_legacy_exact_horizons(provider, log, today):
    """Populate corrected outcomes for all pre-V2 picks without overwriting V1.

    Stock return uses the recorded pick price. Benchmark return is recomputed from
    the appropriate market benchmark: SPY for US names and XIU.TO for TSX names.
    Entry benchmark uses the last close on/before the pick date; horizon prices
    use the first close on/after the exact target date.
    """
    snapshot_legacy_outcomes(log)
    filled = 0
    benchmark_entry_cache = {}
    benchmark_target_cache = {}

    for record in log.get("picks", []):
        if strategy_bucket(record) == V2_LABEL:
            continue
        try:
            pick_date = date.fromisoformat(record["pick_date"])
        except Exception:
            continue
        pick_price = record.get("pick_price")
        if not finite(pick_price) or float(pick_price) <= 0:
            continue

        ticker = record["ticker"]
        benchmark = v2.benchmark_for_ticker(ticker)
        corrected = record.setdefault("corrected_outcomes", {})
        corrected_bench = record.setdefault("corrected_outcomes_bench", {})
        record["corrected_benchmark"] = benchmark
        record["corrected_method"] = "exact-horizon:first-close-on-or-after"

        entry_key = (benchmark, pick_date.isoformat())
        if entry_key not in benchmark_entry_cache:
            benchmark_entry_cache[entry_key] = _price_on_or_before(
                provider, benchmark, pick_date
            )
        bench_entry = benchmark_entry_cache[entry_key]
        if finite(bench_entry):
            record["corrected_bench_at_pick"] = round(float(bench_entry), 4)

        for horizon, days in core.HORIZONS.items():
            target = pick_date + timedelta(days=days)
            if today < target:
                continue
            if finite(corrected.get(horizon)) and finite(corrected_bench.get(horizon)):
                continue

            stock_px = v2._price_on_or_after(provider, ticker, target)
            if not finite(stock_px):
                continue
            stock_return = (float(stock_px) / float(pick_price) - 1.0) * 100.0
            if not finite(stock_return):
                continue
            corrected[horizon] = round(stock_return, 2)

            if finite(bench_entry) and float(bench_entry) > 0:
                target_key = (benchmark, target.isoformat())
                if target_key not in benchmark_target_cache:
                    benchmark_target_cache[target_key] = v2._price_on_or_after(
                        provider, benchmark, target
                    )
                bench_px = benchmark_target_cache[target_key]
                if finite(bench_px):
                    bench_return = (
                        float(bench_px) / float(bench_entry) - 1.0
                    ) * 100.0
                    corrected_bench[horizon] = round(bench_return, 2)
            filled += 1
    return filled


def outcome_maps_for_reporting(record):
    bucket = strategy_bucket(record)
    if bucket == V2_LABEL:
        return record.get("outcomes", {}), record.get("outcomes_bench", {})
    return record.get("corrected_outcomes", {}), record.get(
        "corrected_outcomes_bench", {}
    )


def _stats_for_records(records, horizon="1m"):
    returns = []
    benchmark_returns = []
    excess = []
    valid_return_n = 0

    for record in records:
        outcomes, bench = outcome_maps_for_reporting(record)
        value = outcomes.get(horizon)
        if finite(value):
            valid_return_n += 1
        benchmark_value = bench.get(horizon)
        if not (finite(value) and finite(benchmark_value)):
            continue
        value = float(value)
        benchmark_value = float(benchmark_value)
        returns.append(value)
        benchmark_returns.append(benchmark_value)
        excess.append(value - benchmark_value)

    if not excess:
        return {
            "return_n": valid_return_n,
            "comparable_n": 0,
            "beats": 0,
            "beat_rate": None,
            "avg_return": None,
            "avg_benchmark": None,
            "avg_excess": None,
            "median_excess": None,
        }

    beats = sum(x > 0 for x in excess)
    n = len(excess)
    return {
        "return_n": valid_return_n,
        "comparable_n": n,
        "beats": beats,
        "beat_rate": round(beats / n * 100.0, 1),
        "avg_return": round(sum(returns) / n, 1),
        "avg_benchmark": round(sum(benchmark_returns) / n, 1),
        "avg_excess": round(sum(excess) / n, 1),
        "median_excess": round(statistics.median(excess), 1),
    }


def performance_by_strategy(log, horizon="1m"):
    groups = {V2_LABEL: [], V1_LABEL: [], PRE_V1_LABEL: []}
    for record in log.get("picks", []):
        groups[strategy_bucket(record)].append(record)
    return {
        key: _stats_for_records(records, horizon=horizon)
        for key, records in groups.items()
    }


def save_json_strict(path, payload):
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2, default=str, allow_nan=False)
