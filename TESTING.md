# Testing & Evals

This project is tested in three layers, because it has two different kinds of
thing that can break: **deterministic code** (math, gates, the log) and a
**non-deterministic model** (the catalyst classifier). They need different checks.

## Quick start

```bash
pip install -r requirements-dev.txt

python -m pytest -q                    # all unit + integration tests (34)
python evals/eval_catalyst.py          # catalyst eval, free mock baseline
python evals/eval_catalyst.py --groq   # catalyst eval, the real model (needs GROQ_API_KEY)
```

On GitHub, the **Tests** workflow runs the first two automatically on every push.

## Layer 1 — Unit tests (`tests/test_indicators.py`, `tests/test_scoring_and_gates.py`)

The deterministic backbone: RSI, moving averages, pullback detection, the
days-in-decline signal, and the valuation and quality scores. Known inputs, known
outputs. These **gate the build**.

## Layer 2 — Invariant tests (the veto gates + pipeline)

These lock in the safety properties that must hold no matter what data arrives:

- A **structural catalyst never produces a buy** (the core value-trap guarantee).
- A **low-quality** or **sub-blue-chip** or **about-to-report** stock never passes.
- The known synthetic traps (INTC, VZ) **never qualify** — they always land in
  the rejected pile.
- Qualifiers come out **sorted, above the cutoff, and capped**; only the **top N
  are featured** with a written analysis.

`tests/test_pipeline_and_state.py` covers the full weekly path and the outcome
log: the dedup window (a 60-day-old pick is reusable, a 5-day-old one isn't),
outcome maturation (a 40-day-old pick fills its 1w/1m slots but not 3m/6m, with
the S&P tracked alongside), the track-record beat count, and log round-tripping.
All of these **gate the build**.

## Layer 3 — The catalyst eval (`evals/`)

You can't unit-test an LLM with exact matches, so the classifier gets an **eval**:
a labeled set of headlines (`catalyst_cases.jsonl`) with known-correct answers,
and a runner that measures how often the model is right.

The headline metric is **trap recall** — of all the real value traps in the set,
how many did we correctly veto? The errors are asymmetric:

| Error | Meaning | Cost |
|-------|---------|------|
| **Missed trap** | We said "buy", it was a trap | You lose money. **Expensive.** |
| **False alarm** | We vetoed a fine stock | You miss a gain. Cheap. |

So the eval treats a missed trap as the serious failure and prints exactly which
ones slipped through.

### Mock baseline vs the real model

The **mock** keyword classifier scores ~67% trap recall — it catches the obvious
traps (lawsuit, guidance cut, probe) but misses the ones with no keyword (a quiet
CEO resignation, a credit downgrade, soft language that means a structural
problem). **That gap is why production uses an LLM.** The mock is the floor the
real model has to beat. CI runs the mock eval as a non-gating report; the real
quality check is on demand:

```bash
GROQ_API_KEY=your_key python evals/eval_catalyst.py --groq --min-trap-recall 0.9
```

Aim for **trap recall ≥ 0.9** on the real model, and run it whenever you change
the prompt, swap the model, or grow the dataset.

### Extending the eval set

`catalyst_cases.jsonl` is the most valuable thing to grow. Whenever the screener
mishandles a real headline, add it as a labeled case:

```json
{"id": "c23", "ticker": "ACME", "headlines": ["..."], "expected_category": "structural", "expected_veto": true, "note": "why"}
```

Categories: `market`, `sector`, `one_off_operational` (all buyable) and
`structural` (always a veto).

## What's deliberately *not* in CI

The live-data path (yfinance), the live-model eval (`--groq`), and the Wikipedia
constituent fetch aren't in the push-triggered CI, to keep it free, fast, and
deterministic. Run those on demand, or on the weekly schedule.
