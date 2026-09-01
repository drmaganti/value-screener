# Architecture — Weekly Value Screener

## System shape

The screener is a scheduled batch pipeline rather than an interactive web service.

```text
GitHub Actions schedule/manual run
          ↓
universe refresh / last-good fallback
          ↓
prior-pick outcome maturation
          ↓
rolling fundamentals cache refresh
          ↓
paced broad price scan
          ↓
cheap eligibility filters
          ↓
expensive enrichment on survivors
          ↓
catalyst classification + structural veto
          ↓
deterministic composite scoring
          ↓
pick log + HTML/text report
          ↓
email delivery + committed research history
```

## Core modules

### `value_screener.py`

Owns provider abstractions, screening lenses, thresholds/weights, catalyst classification, scoring, and pipeline orchestration.

The domain logic should remain separable from email formatting and GitHub Actions configuration so it can be run locally/offline in tests.

### `refresh_universe.py`

Refreshes index membership. The workflow should retain a last-known-good universe when a transient source failure occurs rather than replacing it with an empty universe.

Universe membership has its own freshness lifecycle and should not be fetched on every per-stock operation.

### Fundamentals cache

`fundamentals_cache.json` reduces repeated expensive calls and supports sector medians. Cache records should retain enough age/source information to distinguish a normal cached value from stale/missing coverage.

### Pick/outcome log

`picks_log.json` is a first-class product dataset. It contains signals at selection time and later benchmark-relative outcomes.

Protect point-in-time integrity: once a pick is logged, do not silently backfill future-known information into its original signal snapshot.

### Reporting

`email_report.py` / related reporting code should consume pipeline results rather than recalculate investment logic. Presentation should never change which stock qualified.

### Evals/tests

`tests/` checks deterministic behavior/invariants. `evals/` evaluates the LLM-based catalyst classifier. These are different quality surfaces and should remain explicit.

## Provider boundaries

Market/fundamental and LLM providers should remain replaceable behind interfaces/adapters.

Provider changes must document:

- units and field mapping;
- freshness;
- rate limits/retry behavior;
- missing-data behavior;
- commercial/licensing constraints;
- any change to historical comparability.

## Funnel/cost architecture

The pipeline deliberately avoids doing expensive work on the full universe:

1. mature/log existing picks;
2. price/pullback scan cheaply;
3. enrich a much smaller survivor set;
4. use LLM classification/analysis only when needed.

Keep this ordering unless measurement demonstrates a better cost/quality trade-off.

## Scoring boundary

The weighted composite is deterministic once source signals are available. The LLM should not choose or modify the final numeric score.

Catalyst classification is a separate evidence signal/veto and must be evaluated for unsupported/incorrect classifications.

## Data integrity

Important invariants:

- missing data is not silently zero;
- sector medians use a minimum coverage threshold;
- a failed provider call should not corrupt unrelated cached records;
- outcome calculations use the correct pick-time price/date and benchmark period;
- repeat-pick exclusion uses logged state consistently;
- timestamps/time zones are explicit around weekly execution.

## CI / scheduled workflow

Separate concerns:

- `tests.yml` — code quality/regression gate;
- weekly screen — production-like scheduled batch;
- universe refresh — may be part of weekly flow but should fail safely;
- generated logs/cache commits — require minimal GitHub write permission.

Workflows should pin trusted Action versions, use secrets rather than committed credentials, and avoid granting broader repository permissions than necessary.

## Observability

A weekly run should make it possible to answer:

- how many universe members were scanned?
- how many failed price/fundamental enrichment?
- how many passed each funnel stage?
- how fresh is the fundamentals cache?
- how many LLM calls failed/fell back?
- what methodology/threshold version produced the picks?
- was the email successfully delivered?
- was the outcome log committed?

## Warren integration

Preferred boundary:

```text
Weekly strategy filters
      ↓
strategy survivors
      ↓
Warren Screen (optional comparative ranking)
      ↓
small finalist set
      ↓
Warren Deep (optional evidence-grounded research)
```

The weekly repo keeps strategy-specific pullback/catalyst/repeat-pick logic. Warren keeps reusable stock-intelligence logic. Do not duplicate either side's responsibilities.

## Scaling and commercialization

Before commercial use, verify data/news/model licensing. Free-provider availability is not the same as a right to redistribute or monetize derived research.

If scale grows, likely engineering priorities are caching/provider reliability and durable point-in-time storage—not adding more agents.

## Architecture decision rule

Protect reproducibility and point-in-time integrity. A weekly research system that cannot explain exactly why a stock qualified on that date cannot reliably learn from its own track record.
