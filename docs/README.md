# Value Screener Documentation Index

Use the root `README.md` for setup, current strategy behavior, deployment, and configuration.

## Product and strategy

- [`PRODUCT.md`](./PRODUCT.md) — weekly strategy product definition, users, jobs, success measures, risks
- [`v2-strategy.md`](./v2-strategy.md) — current/next strategy design work
- [`../ROADMAP.md`](../ROADMAP.md) — implementation/evaluation roadmap

## Engineering

- [`ARCHITECTURE.md`](./ARCHITECTURE.md) — scheduled-pipeline boundaries, caches, point-in-time integrity, Warren integration
- [`../TESTING.md`](../TESTING.md) — test strategy and commands
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — strategy/code/provider change controls
- [`security-and-credentials.md`](./security-and-credentials.md) — credentials and security guidance

## Evaluation and learning

- `../evals/` — catalyst-classifier evaluation assets
- `../tests/` — deterministic/unit/invariant/integration tests
- `../picks_log.json` — point-in-time pick evidence plus matured outcomes; preserve original pick-time signals

## Ownership boundaries

- This repository owns the **weekly contrarian/value strategy** and its scheduled outcome-tracking workflow.
- Warren (`value-screener-agent`) owns reusable general Screen/Deep stock intelligence.
- Strategy rules should not be copied into Warren, and Warren's deep-research implementation should not be forked here.
