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
3. **Refresh sector data.** Top up a rolling fundamentals cache (the most-stale
   slice of the universe each week, so all ~600 are covered over ~3 weeks) and
   compute each sector's median multiples — so a stock can be judged cheap *for
   its sector*, not against a one-size-fits-all band.
4. **Paced scan.** Pull a year of prices for the whole universe in small batches
   with sleeps and retries, so free data survives ~600 names without throttling.
5. **Funnel.** Keep names that have corrected (≥10% off the 52-week high or RSI
   oversold), then enrich only those with valuation, financial-health, and news
   signals.
6. **Classify the catalyst.** An LLM reads the recent headlines and decides *why*
   the stock is down. Structural reasons (guidance cut, lawsuit, regulatory
   action, governance blowup) are a hard veto.
7. **Score and rank.** A weighted composite (0–100). Only names scoring **≥ 60**
   count as value buys.
8. **Write and send.** The top 3 get a ~200-word written analysis; everything
   logged goes into the email — this week's picks up top, the maturing track
   record below.

### How valuation is scored

Valuation is the heaviest lens (30% of the score), and it's a **multi-metric
adaptive composite**, not a single ratio. Each stock is scored on whichever of
these it has data for, then blended:

- **Universal multiples** that work without a dividend: earnings yield (E/P),
  free-cash-flow yield, EV/EBITDA, P/B, P/S. This is what lets non-dividend-payers
  (much of tech) get a real score instead of a hollow one.
- **Sector-relative:** the stock's multiples versus its own sector's median, so a
  cheap-for-software name isn't unfairly beaten by a utility on raw P/E. Sectors
  with too few names fall back to absolute bands.
- **Analyst consensus upside:** room to the mean 12-month analyst price target
  (yfinance, unweighted, gated on at least 3 analysts). A deliberately small,
  known-weak signal that runs counter to a contrarian value thesis, so it nudges
  rather than drives — the leaderboard will show whether it earns its place.
- **History-relative** (when available): P/E versus its own 5-year band, dividend
  yield versus its own 5-year average.

Every sub-metric is logged per pick, so the factor leaderboard can later tell you
which ones actually predict returns — and you tune from there rather than
trusting all of them equally.

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
fundamentals_cache.json      rolling fundamentals store -> sector medians (created + committed each run)
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
- `THRESHOLDS["min_sector_count"]` — names a sector needs before its median is
  trusted for sector-relative scoring (5); below this, those stocks use absolute
  bands.
- `THRESHOLDS["fund_staleness_days"]` / `["max_fundamentals_refresh"]` — how the
  fundamentals cache rotates: refresh entries older than 21 days, up to 200 per
  run.
- `HORIZONS` — the outcome windows tracked (1w / 1m / 3m / 6m).
- `WEIGHTS` — how the five lenses combine into the score.
- `SCAN` — batch size and sleep timing for the paced scan.

## The outcome log

`picks_log.json` is the heart of the system and the thing that lets it improve.
Each pick records its signals at pick time (the overall lens scores, plus every
valuation sub-metric — earnings yield, FCF yield, EV/EBITDA, P/B, P/S, vs-sector,
analyst upside — and F-score, pullback, days in decline, catalyst category) and a
return slot for each horizon, filled in later versus the S&P. Over months this becomes a
dataset you can mine: do higher scores mean better outcomes? Which valuation
metric predicts? Do fresh dips bounce better than long slides? That analysis is
Phase 2 (see ROADMAP.md).

## Honest limitations

- **Valuation bands are heuristic and the history method is dormant.** The
  absolute cheapness bands (e.g. earnings yield 3%→10%) are reasonable starting
  points, not gospel, and the sector-relative comparison uses sector *medians*
  computed from the cache rather than a paid benchmark feed. The 5-year P/E-band
  method needs historical earnings yfinance doesn't provide, so it stays mostly
  dormant on live data. None of this is fatal — valuation is now multi-metric and
  sector-relative, and the leaderboard will show which metrics actually earn their
  weight — but a paid source (FMP, Polygon) would sharpen it. The provider
  abstraction makes that a one-class swap.
- **The sector cache warms up over ~3 weeks.** Until it fills, sector coverage is
  partial and more stocks fall back to absolute bands. It self-corrects; it just
  isn't instant.
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
