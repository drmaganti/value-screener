# Value Stock Screener

A research tool that emails you a daily shortlist of blue-chip stocks (NYSE /
NASDAQ / TSX) that have pulled back, look cheap versus their own history, are
financially healthy, and are down for a **transient** reason rather than a
structural one. Runs in the cloud on GitHub Actions for **$0/month, no credit
card**.

> **Not investment advice.** This surfaces candidates and the evidence behind
> them. The buy decision is yours. The catalyst filter is deliberately
> conservative, but no screen replaces your own judgment. Backtest before you
> trust it with money.

## How it works

A funnel, cheapest checks first:

1. **Technical screen** runs on the whole universe: pullback off the 52-week
   high, RSI, position vs the 50- and 200-day moving averages.
2. **Enrich the survivors only** (keeps API usage tiny): valuation vs the
   stock's own history, a Piotroski-style financial-health score, recent news.
3. **Catalyst classification** (the value-trap filter): an LLM reads the recent
   headlines and decides *why* the stock is down. Anything structural (guidance
   cut, lawsuit, regulatory action, governance blowup) is a hard veto.
4. **Score, rank, dedupe, email.** A weighted composite; only names above the
   threshold go out, and a cooldown stops the same name emailing you daily.

## The free stack

| Piece | Service | Cost |
|-------|---------|------|
| Market data | yfinance | free, no key |
| Catalyst LLM | Groq (`llama-3.3-70b-versatile`) | free, no card |
| Email | Gmail SMTP | free |
| Scheduler / host | GitHub Actions | free (well under the limit) |
| Cooldown state | `state.json` committed back to the repo | free |

## Deploy (about 15 minutes)

### 1. Put this in a GitHub repo
Create a new repo (private is fine) and push these files to it.

### 2. Get a free Groq API key
Sign up at **console.groq.com** (no credit card), create an API key, copy it.

### 3. Get a Gmail app password
With 2-factor auth on your Google account, go to **myaccount.google.com →
Security → App passwords**, generate one for "Mail", and copy the 16-character
value. This is *not* your normal Gmail password.

### 4. Add four repository secrets
In the repo: **Settings → Secrets and variables → Actions → New repository
secret**. Add each of:

- `GROQ_API_KEY` — the Groq key from step 2
- `EMAIL_FROM` — your Gmail address
- `EMAIL_APP_PASSWORD` — the app password from step 3
- `EMAIL_TO` — where you want the digest sent

### 5. Test it before trusting the schedule
Open the **Actions** tab, pick **Daily Value Screen**, click **Run workflow**.
This runs it immediately so you can confirm the email arrives and looks right.
After that it runs itself every weekday morning.

## Run it locally first (recommended)

Confirm the logic with offline synthetic data — no keys, no network:

```bash
python value_screener.py
```

This prints a sample digest instead of emailing. Then try live data:

```bash
PROVIDER=yfinance CLASSIFIER=groq GROQ_API_KEY=your_key python value_screener.py
```

Leave the email variables unset and it prints the real digest to your terminal
rather than sending it — handy while you tune things.

## Configuration

Behaviour is driven by environment variables (the workflow sets these from your
secrets) and by two dictionaries at the top of `value_screener.py`:

- `UNIVERSE` — the tickers it watches. Add your own; TSX names use `.TO`.
- `WEIGHTS` — how the five lenses combine into the score. Tune to taste.
- `THRESHOLDS` — pullback size, quality floor, cooldown length, max picks, etc.

## Scheduling note

The cron is `0 13 * * 1-5` = **13:00 UTC, weekdays**. That's 9:00am Eastern
during daylight saving (EDT) and 8:00am during standard time (EST), because
cron doesn't follow DST. Adjust the hour in the workflow if you want to pin it.
At ~9am ET the latest available price is the **previous close** (markets open
9:30am ET), which is all a daily screen needs, and the run also catches any
overnight or pre-market news before you'd act at the open.

## Honest limitations

- **Fundamentals fidelity.** yfinance gives clean prices and current ratios but
  not a clean 5-year P/E band, so the "cheap vs history" lens leans on
  dividend-yield-vs-5-year-average (which yfinance *does* provide) plus current
  multiples. For full P/E-percentile fidelity, swap the data provider for
  Financial Modeling Prep or Polygon — the code is built so that's one class.
- **The Piotroski score is a documented subset**, not the full nine signals,
  because several require year-over-year statement deltas.
- **No backtest yet.** Log picks with their price and date, then measure whether
  the weighting actually works. An agent you can't evaluate is just vibes.
- **Free tiers can change or rate-limit.** You're using a tiny fraction of
  Groq's daily allowance, but providers adjust quotas; verify if it ever stops.

## Going paid later (optional)

If you outgrow the free tier, the catalyst step is the only real cost, and
running it as an **overnight batch job** is roughly half price on both Groq and
Anthropic. Flip `CLASSIFIER` and point it at a batch endpoint. An
`AnthropicClassifier` is already in the code, unused by default.
