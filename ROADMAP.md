# Roadmap

The guiding idea: you can't improve what you can't measure. So the order is
**close the outcome loop → build the surface to read it → make the agent smarter
→ scale.** Everything sequences off that.

## Phase 0 — Baseline (done)

The screener itself (funnel, five lenses, weighted scoring, veto gates), the free
stack (yfinance, Groq, Gmail, GitHub Actions), deployed and emailing, with a test
suite and a catalyst eval.

## Phase 1 — Weekly, tracked, readable (done)

- ~600-name index universe (S&P 500 + NASDAQ-100 + TSX 60), refreshed on
  rebalance.
- Weekly Sunday-overnight run, paced in batches so free data survives the scale.
- Dedup against open bets (picked within 30 days).
- Outcome log: every pick's signals captured, returns filled in at 1w / 1m / 3m /
  6m versus the S&P as they mature.
- A value-buy cutoff (≥ 60/100); the top 3 get a ~200-word written analysis.
- A modern HTML email: this week's picks plus a maturing track record.

## Phase 2 — See whether it works (next) July - Aug

Built quickly, but the payoff is gated on having a month-plus of matured picks.

- Per-signal analysis: does a higher score mean a better outcome? Do cheap picks
  beat healthy ones? Do fresh dips bounce better than long slides (the
  reversal-vs-momentum question)? Do market-catalyst names rebound better than
  sector ones? This is a personal replication of the factor literature on the
  actual universe.
- A dashboard (GitHub Pages, static, free, auto-updating): headline stats, a
  score-vs-return scatter, win-rate-by-category bars, a sortable pick table.
- Weight tuning informed by the data — carefully, out-of-sample, with a human in
  the loop, never auto-tuning on a handful of points.

## Phase 3 — Make the agent smarter (after Phase 2)

Now that changes can be measured:

- Grow the catalyst eval set from real misses; push trap recall up.
- Add more validated signals (12-month momentum, gross profitability,
  book-to-market) and encode the reversal-vs-momentum horizon split.
- A more agentic run: extra scrutiny and corroboration on borderline names, a
  self-critique pass before sending.
- Human-in-the-loop ratings ("would I have bought?") feeding both the eval set
  and the tuning.

## Phase 4 — Scale and maybe share (horizon)

- A paid data API (FMP / Polygon) when free data strains or to fix the
  fundamentals-fidelity gaps.
- A broader universe (TSX Composite, Russell 1000).
- An interactive app (Streamlit) if the static dashboard is outgrown.
- A public track record — honest, benchmark-relative, includes the losers, and
  mindful that publishing track records can edge toward regulated-advice
  territory (a flag, not legal advice).
- Possible scope jump: portfolio-level view (sizing, sector exposure) — a
  deliberate decision, not drift.

Phases 1 and 2 are the planned path. Phases 3 and 4 are a backlog, and what Phase
2 reveals should reprioritize them — which is the whole point of measuring first.
