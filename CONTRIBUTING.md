# Contributing

The Weekly Value Screener is both software and an evolving investment-research methodology. Changes should preserve reproducibility, point-in-time integrity, and the ability to evaluate whether the strategy actually improves.

## Local quality

Follow `README.md` and `TESTING.md` for setup and test commands.

Before merging:

- run the deterministic test suite;
- run relevant classifier evals for catalyst/prompt/model changes;
- verify generated report behavior when reporting code changes;
- update strategy/methodology docs when thresholds, factors, or veto logic change.

## Strategy changes

Changes to filters, weights, valuation bands, catalyst vetoes, cache behavior, or pick selection should include:

1. a hypothesis/rationale;
2. exact configuration/code change;
3. regression/invariant tests;
4. historical/out-of-sample evaluation where an improvement is claimed;
5. documentation update;
6. consideration of whether historical pick comparability is affected.

Do not tune the current system only to make past winners rank higher.

## Point-in-time integrity

Never modify a historical pick's original signal snapshot using information learned after the pick date. Outcome fields may mature over time; original selection evidence should remain historically faithful.

## Provider changes

Document:

- field/unit mapping;
- rate limits;
- missing/failure behavior;
- cache/freshness impact;
- licensing/commercial constraints;
- whether historical results remain comparable.

## LLM/classifier changes

Catalyst classification is evaluated behavior, not a free-form writing feature.

For model/prompt changes:

- run the labeled eval set;
- inspect structural-veto false positives/negatives;
- keep headlines/evidence grounded;
- do not let the LLM invent numerical source-of-truth data;
- measure cost/reliability if adding calls.

## Generated data files

Files such as the universe, fundamentals cache, and pick log may be updated by scheduled workflows. Avoid manual edits that destroy traceability. If a repair is necessary, document the reason and preserve point-in-time semantics.

## Pull-request checklist

- [ ] Tests/evals relevant to the change pass.
- [ ] Strategy behavior is reproducible.
- [ ] Historical pick evidence remains point-in-time correct.
- [ ] Missing/provider failures remain explicit.
- [ ] No credentials are committed.
- [ ] Data/model licensing implications are understood.
- [ ] Email/reporting changes do not change strategy logic accidentally.
- [ ] README/strategy/roadmap/product/architecture docs are updated if semantics changed.

## Review priority

1. credential/data integrity;
2. point-in-time correctness;
3. strategy/filter/scoring correctness;
4. classifier/eval regression;
5. scheduled-run reliability;
6. research usefulness;
7. report presentation.
