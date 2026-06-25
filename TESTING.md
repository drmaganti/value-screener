# Testing & Evals

This project is tested in three layers, because it has two different kinds of
thing that can break: **deterministic code** (math, gates, state) and a
**non-deterministic model** (the catalyst classifier). They need different
checks.

## Quick start

```bash
pip install -r requirements-dev.txt

python -m pytest -q                 # all unit + integration tests
python evals/eval_catalyst.py       # catalyst eval, free mock baseline
python evals/eval_catalyst.py --groq   # catalyst eval, the real model (needs GROQ_API_KEY)
```

On GitHub, the **Tests** workflow runs the first two automatically on every
push and shows the results in the Actions tab.

## Layer 1 — Unit tests (`tests/test_indicators.py`, `tests/test_scoring_and_gates.py`)

The deterministic backbone: RSI, moving averages, pullback detection, the
valuation and quality scores. Known inputs, known outputs. If these break, the
signals are silently wrong, so they're exact-value assertions. These **gate the
build** — they must pass.

## Layer 2 — Invariant tests (the veto gates, in `test_scoring_and_gates.py`)

These lock in the safety properties that must hold no matter what data arrives:

- A **structural catalyst never produces a buy** (the core value-trap guarantee).
- A **low-quality stock never passes** the quality floor.
- Nothing **below the blue-chip cap** appears.
- Nothing **inside the earnings blackout** appears.

`test_pipeline_and_state.py` extends this to the full pipeline: the known
synthetic traps (INTC, VZ) must never reach the buy list, picks come out sorted
and capped, and the cooldown actually suppresses recently-sent names. These also
**gate the build**.

## Layer 3 — The catalyst eval (`evals/`)

You can't unit-test an LLM with exact matches, so the classifier gets an **eval**
instead: a labeled set of headlines (`catalyst_cases.jsonl`) with known-correct
answers, and a runner that measures how often the model is right.

The headline metric is **trap recall** — of all the real value traps in the set,
how many did we correctly veto? This is the one that protects your money,
because the errors are asymmetric:

| Error | Meaning | Cost |
|-------|---------|------|
| **Missed trap** | We said "buy", it was a trap | You lose money. **Expensive.** |
| **False alarm** | We vetoed a fine stock | You miss a gain. Cheap. |

So the eval treats a missed trap as the serious failure and prints exactly which
ones slipped through.

### The mock baseline vs the real model

Running the eval against the **mock** keyword classifier scores around 67% trap
recall — it catches the obvious traps (lawsuit, guidance cut, probe) but misses
the ones with no keyword (a quiet CEO resignation, a credit downgrade, soft
language that means a structural problem). It even false-alarms on a headline
that merely contains the word "guidance" in a positive sentence. **That gap is
the whole reason production uses an LLM**, and the mock baseline is the floor it
has to beat.

To measure the real model, set your key and run:

```bash
GROQ_API_KEY=your_key python evals/eval_catalyst.py --groq --min-trap-recall 0.9
```

Aim for **trap recall ≥ 0.9** on the real model. Run this whenever you change
the classifier prompt, swap the model, or adjust the universe — it tells you
immediately whether the change made the trap filter better or worse.

### Extending the eval set

`catalyst_cases.jsonl` is the most valuable thing to grow. Whenever you spot a
real-world headline the screener mishandled, add it as a labeled case:

```json
{"id": "c23", "ticker": "ACME", "headlines": ["..."], "expected_category": "structural", "expected_veto": true, "note": "why"}
```

Categories: `market`, `sector`, `one_off_operational` (all buyable) and
`structural` (always a veto). A few dozen well-chosen cases — especially
adversarial ones where the real reason is buried under a market headline — are
worth more than a hundred easy ones.

## What's deliberately *not* in CI

The live-data path (yfinance) and the live-model eval (`--groq`) aren't in the
push-triggered CI, to keep it free, fast, and deterministic — CI shouldn't go
red because Yahoo had a hiccup or the model phrased something differently. Run
those on demand, or add a separate scheduled workflow if you want a periodic
live check.
