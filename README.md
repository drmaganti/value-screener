# Value Stock Screener

A research tool that scans a large blue-chip universe (NYSE / NASDAQ / TSX) **once
a week**, finds stocks that have pulled back but look cheap, financially healthy,
and down for a *temporary* reason, scores them, **tracks how they actually do over
time**, and emails a clean HTML digest. Runs entirely in the cloud on GitHub
Actions for **$0/month, no credit card**.

> **Not investment advice.** This surfaces candidates with the evidence behind
> them; the buy decision is yours. The value-trap filter is deliberately
> conservative, but no screen replaces your own judgment. The track record is
> there so you can see whether the picks actually work before trusting them.

## How it works

Each weekly run is a funnel — cheap checks first, expensive checks only on the
survivors:

1. **Mature past picks.** Update the outcome log: for every earlier pick whose
   1-week / 1-month / 3-month / 6-month horizon has come due, record its return
   versus the S&P 500.
2. **Skip open bets.** Names picked within the last 30 days are excluded so the
   same stock doesn't surface week after week.
3. **Paced scan.** Pull a year of prices for the whole universe in small batches
   with sleeps and retries, so free data survives ~600 names without throttling.
4. **Funnel.** Keep names that have corrected (≥10% off the 52-week high or RSI
   oversold), then enrich only those with valuation, financial-health, and news
   signals.
5. **Classify the catalyst.** An LLM reads the recent headlines and decides *why*
   the stock is down. Structural reasons (guidance cut, lawsuit, regulatory
   action, governance blowup) are a hard veto.
6. **Score and rank.** A weighted composite (0–100). Only names scoring **≥ 60**
   count as value buys.
7. **Write and send.** The top 3 get a ~200-word written analysis; everything
   logged goes into the email — this week's picks up top, the maturing track
   record below.

## The free stack

| Piece | Service | Cost |
|-------|---------|------|
| Market data | yfinance | free, no key |
| Catalyst + analysis LLM | Groq (`llama-3.3-70b-versatile`) | free, no card |
| Email | Gmail SMTP | free |
| Scheduler / host | GitHub Actions | free (well under the limit) |
| Universe + pick log | JSON files committed back to the repo | free |

## Files

```
value_screener.py            the engine: providers, lenses, classifier, log, pipeline
email_report.py              builds the HTML + plain-text digest
refresh_universe.py          fetches index constituents -> universe.json
universe.json                the ~600-name universe (created by refresh_universe)
picks_log.json               the outcome log (created + committed each run)
conftest.py                  lets the tests import value_screener
requirements.txt             runtime deps (yfinance, requests, pandas, lxml)
requirements-dev.txt         test deps (pytest)
README.md / TESTING.md / ROADMAP.md

.github/workflows/
  weekly-screen.yml          runs the screener Sunday overnight
  tests.yml                  runs the test suite on every push

tests/                       unit + invariant + integration tests
evals/                       the catalyst classifier eval + labeled dataset
```

## Deploy

### 1. Repo + secrets
Push these files to a GitHub repo (private is fine). Then add four repository
secrets under **Settings → Secrets and variables → Actions**:

- `GROQ_API_KEY` — free key from console.groq.com
- `EMAIL_FROM` — your Gmail address
- `EMAIL_APP_PASSWORD` — a Gmail app password (Google account → Security → App
  passwords), *not* your login password
- `EMAIL_TO` — where the digest goes

### 2. Build the universe once
Run `python refresh_universe.py` locally (or just let the weekly workflow's
refresh step do it). This writes `universe.json` with the S&P 500 + NASDAQ-100 +
S&P/TSX 60 members. It scrapes Wikipedia, so it needs open network; the workflow
runs it with `continue-on-error` so a hiccup falls back to the last good file.

### 3. Test the run
**Actions → Weekly Value Screen → Run workflow.** The first email will have picks
but an empty Track Record — that section fills in over the following weeks as
picks mature. After that it runs itself every Sunday overnight.

## Run it locally

```bash
# Offline, synthetic data, no keys — writes a preview to email_preview.html
python value_screener.py

# Live data + real LLM, prints/sends for real
PROVIDER=yfinance CLASSIFIER=groq GROQ_API_KEY=... python value_screener.py
```

With the email variables unset, the digest is written to `email_preview.html`
instead of sent — handy for previewing the layout.

## Configuration

Everything tunable lives at the top of `value_screener.py`:

- `THRESHOLDS["min_composite"]` — the value-buy cutoff (default **60**; lower =
  more picks).
- `THRESHOLDS["featured"]` / `["max_picks_per_run"]` — how many get the written
  analysis (3) and how many are logged per run (10).
- `THRESHOLDS["open_window_days"]` — how long a pick blocks re-selection (30).
- `HORIZONS` — the outcome windows tracked (1w / 1m / 3m / 6m).
- `WEIGHTS` — how the five lenses combine into the score.
- `SCAN` — batch size and sleep timing for the paced scan.

## The outcome log

`picks_log.json` is the heart of the system and the thing that lets it improve.
Each pick records its signals at pick time (cheapness, F-score, pullback, days in
decline, catalyst category) and a return slot for each horizon, filled in later
versus the S&P. Over months this becomes a dataset you can mine: do higher scores
mean better outcomes? Do cheap picks beat healthy ones? Do fresh dips bounce
better than long slides? That analysis is Phase 2 (see ROADMAP.md).

## Honest limitations

- **Fundamentals fidelity.** yfinance gives clean prices and current ratios but
  not a clean 5-year P/E band, so the "cheap vs history" lens leans on
  dividend-yield-vs-5-year-average plus current multiples. A paid source (FMP,
  Polygon) is the upgrade; the provider abstraction makes it a one-class swap.
- **Large-cap quality.** The Piotroski-style score is most powerful in small/mid
  caps; on blue chips its edge is thinner (well documented). It's still a useful
  trap filter, not a strong return signal on its own.
- **The universe drifts.** Constituents change on rebalance; re-run
  `refresh_universe.py` periodically (the workflow does it weekly).
- **No backtest of the strategy itself.** The tests prove the code is correct,
  not that the picks make money. The track record answers that — give it time.
- **Free tiers can change.** You use a tiny fraction of Groq's allowance and
  GitHub's minutes, but providers adjust quotas.

## Compliance note

If you ever make the track record public (e.g. for marketing), keep it honest —
benchmark-relative, including the losers, not back-fit — and be aware that
publishing stock track records can edge toward regulated investment-advice
territory in both Canada and the US. This is a flag, not legal advice.
