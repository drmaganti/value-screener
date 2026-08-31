# Value Screener V2

V2 is the live strategy layer used by the weekly workflow. It keeps the existing
data providers, sector cache, catalyst classifier, analyst writer, and email
report, while changing the selection policy based on the first live cohorts.

## Why V2 exists

The first tracked cohorts showed that the original composite could allow a stock
with very weak valuation to qualify when technical/quality scores were high.
They also showed weak early results from very fresh declines, while older
dislocations that had begun to stabilize performed better. V2 converts those
observations into explicit eligibility and ranking rules without fitting dozens
of new parameters to a small sample.

## V2 funnel

1. Detect a dislocation using the existing pullback/RSI candidate generator.
2. Require sufficient, usable valuation evidence.
3. Require a blended valuation score of at least 60/100.
4. Require normalized financial-health coverage and the existing quality floor.
5. Keep the structural-catalyst veto, market-cap floor, and earnings blackout.
6. Rank eligible names with the existing composite weights, but use a technical
   score that rewards dislocation plus stabilization rather than extreme
   oversold conditions alone.
7. Treat 70+ as a strong candidate, 62-69.9 as a candidate, and below 62 as a
   near-miss/non-selection.
8. Log every post-pullback candidate, not only selected picks.

## Technical score

The V2 technical score is 0-100 and contains four components:

- Dislocation (30): grows from the 10% pullback floor and caps near 35%.
- Decline maturity (25): fresh declines under 30 sessions receive no maturity
  credit; the score ramps up through roughly 120 sessions.
- RSI stability (25): rewards the 40-65 recovery/neutral zone rather than making
  ever-lower RSI automatically better.
- Stabilization (20): rewards positive trailing-20-session movement first,
  then price holding above the 50-day average.

The pullback/RSI screen still creates the opportunity set. The V2 score decides
whether that dislocation looks investable rather than simply severe.

## Valuation eligibility

A stock must have:

- value score >= 60/100; and
- at least three usable value-evidence metrics from earnings yield, FCF yield,
  EV/EBITDA, P/B, P/S, sector-relative valuation, or P/E vs history.

Analyst upside can still inform the blended valuation score, but it does not
count toward the minimum evidence coverage.

## Quality handling

V2 normalizes quality over numeric observations that are actually available.
This avoids treating an unavailable metric such as yfinance interest coverage as
an automatic failure. The familiar 0-9 health score is retained for display.

## Outcome measurement

New outcomes use the first trading close on or after each target horizon
(7/30/90/180 days), rather than using the price on whatever later weekly run
happened to notice that a horizon was due. Non-finite outcomes are repaired when
possible and ignored in summary statistics if they cannot be repaired.

US picks use SPY. New TSX picks use XIU.TO. Legacy V1 records retain their
original SPY benchmark so old `bench_at_pick` values are never reinterpreted.

## Learning / V1 shadow

`candidate_log.json` records every post-pullback V2 candidate, including
near-misses and hard rejects, plus subsequent benchmark-relative outcomes.
Each candidate also records the legacy V1 score/qualification result calculated
on the same stock and date. This creates a contemporaneous V1-vs-V2 shadow
comparison without continuing to email V1 picks.

Do not tune weights from a handful of observations. Prefer stable cohort
evidence across multiple one-month outcomes, then confirm with 3- and 6-month
results before making larger strategy changes.
