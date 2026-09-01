# Product Definition — Weekly Value Screener

## Product statement

The Weekly Value Screener is a strategy-specific research workflow for finding **large, established companies that have sold off, appear reasonably valued, remain financially healthy, and may be down for a temporary rather than structural reason**.

It is designed to answer:

> Which blue-chip pullbacks are worth researching this week?

It is not a general stock-ranking engine, and it is not a personalized investment adviser.

## Primary user

A long-horizon, contrarian/value-oriented investor who wants a small weekly research queue rather than a continuous stream of alerts.

The user values:

- established/large-cap universes;
- pullback opportunities;
- valuation and financial health;
- avoiding obvious value traps;
- a documented reason for the decline;
- seeing how prior picks performed rather than trusting a black-box score indefinitely.

## Core jobs to be done

1. **Find the setup.** Identify large companies that have meaningfully corrected.
2. **Avoid obvious traps.** Check financial quality and veto clearly structural negative catalysts.
3. **Judge cheapness in context.** Use multiple valuation lenses, including sector-relative measures.
4. **Prioritize a few names.** Rank survivors rather than produce an unmanageable list.
5. **Track whether the strategy works.** Mature prior picks over defined horizons and compare against a benchmark.
6. **Deliver the research queue automatically.** Produce a concise weekly digest without manual screening.

## Strategy boundary

The Weekly Value Screener owns **strategy rules**, including concepts such as:

- correction/pullback eligibility;
- oversold context;
- open-pick exclusion;
- sector-relative valuation;
- value-trap/structural-catalyst vetoes;
- weekly cadence;
- outcome tracking.

A general reusable stock-intelligence module such as Warren should not inherit these assumptions. Warren can evaluate/rank or deeply research the strategy's survivors, while this repo remains responsible for *which situation qualifies for the weekly strategy*.

## Product principles

### Cheap is not enough

Valuation must be considered alongside quality, balance-sheet health, catalyst context, and the reason for the selloff.

### Strategy rules are explicit

Thresholds and weights live in code/configuration and should be testable. Avoid undocumented discretionary overrides.

### Outcomes are part of the product

The pick log is not merely audit data; it is the mechanism for learning whether the strategy and individual factors add value.

### Expensive work comes late

Use a funnel: broad/cheap price checks first, richer fundamentals/news/LLM work only on survivors.

### A weekly research queue, not constant trading

The cadence and re-selection window reduce churn and encourage deliberate follow-up.

## Current workflow

```text
~600 large-cap universe
        ↓
mature prior outcomes / exclude open picks
        ↓
refresh rolling fundamentals + sector medians
        ↓
paced price scan
        ↓
pullback / oversold candidates
        ↓
valuation + quality + catalyst enrichment
        ↓
structural-catalyst veto
        ↓
weighted score + rank
        ↓
log candidates / write top analyses
        ↓
weekly email + track record
```

## Success measures

### Strategy quality

- benchmark-relative returns at 1w/1m/3m/6m;
- return distribution by score decile/band;
- hit rate and downside distribution;
- factor-level predictive contribution;
- performance by sector/regime;
- value-trap/structural-veto precision.

### Product usefulness

- weekly run success rate;
- number of actionable research candidates per run;
- duplicate/repeat-pick rate;
- data coverage/freshness;
- digest delivery reliability;
- time saved versus manual weekly screening.

### Cost/reliability

- provider throttling/failure rate;
- LLM calls per run;
- fundamentals cache coverage/age;
- end-to-end runtime;
- GitHub Actions success rate.

## Non-goals

- trade execution;
- personalized portfolio sizing;
- intraday trading signals;
- guaranteeing a “buy” based on a score threshold;
- general-purpose stock intelligence for every product;
- letting an LLM generate the source-of-truth financial values.

## Key risks

- **Value traps:** deteriorating businesses can look statistically cheap.
- **Catalyst misclassification:** headlines may be incomplete or ambiguous.
- **Data-provider limitations:** free feeds can be stale, sparse, throttled, or unsuitable for commercial use.
- **Survivorship/look-ahead bias:** retrospective analysis can overstate strategy quality if point-in-time data is not preserved.
- **Benchmark mismatch:** S&P 500 comparison is imperfect for TSX or sector-heavy picks.
- **Threshold overfitting:** tuning to historical picks can create a strategy that looks good only in sample.

## Product decision rule

Prefer an explainable strategy that can be evaluated over time to a more complicated strategy that cannot be reproduced or falsified.
