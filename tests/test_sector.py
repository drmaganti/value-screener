"""
Tests for the fundamentals cache and sector-relative valuation.

The point of this layer is to judge a stock cheap FOR ITS SECTOR rather than
against a one-size-fits-all band, so these verify the sector medians are computed
sensibly (median, min-count guard) and that the vs-sector sub-metric behaves.
"""
import datetime
import value_screener as vs

SYN = vs.SyntheticProvider()


def _fund(**over):
    f = SYN.fundamentals("JNJ")
    for k, v in over.items():
        setattr(f, k, v)
    return f


# ── Sector medians ──────────────────────────────────────────────────────────

def _cache_of(sector, pes):
    return {f"T{i}": {"sector": sector, "pe": pe, "pb": 3.0, "ev_ebitda": 11.0, "ps": 3.0}
            for i, pe in enumerate(pes)}


def test_sector_median_is_the_median_not_the_mean():
    cache = _cache_of("Tech", [10, 12, 14, 16, 100])   # mean skewed by 100; median = 14
    med = vs.compute_sector_medians(cache)
    assert med["Tech"]["pe"] == 14


def test_thin_sector_is_dropped():
    cache = _cache_of("Utilities", [11, 12])           # below min_sector_count
    med = vs.compute_sector_medians(cache)
    assert "Utilities" not in med                      # too few names to trust


def test_sector_with_enough_names_is_kept():
    cache = _cache_of("Financials", list(range(10, 10 + vs.THRESHOLDS["min_sector_count"])))
    med = vs.compute_sector_medians(cache)
    assert "Financials" in med and med["Financials"]["count"] >= vs.THRESHOLDS["min_sector_count"]


# ── Sector-relative scoring ─────────────────────────────────────────────────

def test_vs_sector_added_only_when_medians_available():
    f = _fund()
    assert "vs_sector" not in vs.compute_valuation(f).parts          # no medians passed
    medians = {f.sector: {"pe": 20.0, "pb": 3.0, "ev_ebitda": 11.0, "ps": 3.0, "count": 10}}
    assert "vs_sector" in vs.compute_valuation(f, medians).parts     # medians passed


def test_cheaper_than_sector_scores_higher_than_pricier():
    sector = "Technology"
    medians = {sector: {"pe": 20.0, "pb": 4.0, "ev_ebitda": 12.0, "ps": 4.0, "count": 10}}
    cheap = _fund(sector=sector, pe=10.0, pb=2.0, ev_ebitda=6.0, ps=2.0)   # half the sector
    rich = _fund(sector=sector, pe=28.0, pb=6.0, ev_ebitda=18.0, ps=6.0)   # well above sector
    cs = vs.compute_valuation(cheap, medians).parts["vs_sector"]
    rs = vs.compute_valuation(rich, medians).parts["vs_sector"]
    assert cs > rs and cs >= 60 and rs <= 40


def test_at_sector_median_is_neutral():
    sector = "Healthcare"
    medians = {sector: {"pe": 18.0, "pb": 3.0, "ev_ebitda": 10.0, "ps": 3.0, "count": 10}}
    f = _fund(sector=sector, pe=18.0, pb=3.0, ev_ebitda=10.0, ps=3.0)      # exactly the median
    assert abs(vs.compute_valuation(f, medians).parts["vs_sector"] - 50.0) < 1.0


def test_low_pe_utility_not_unfairly_favoured_over_software():
    # The motivating case: a 14x utility vs a 26x software name. Absolute P/E says
    # the utility is "cheaper", but sector-relative should NOT favour it when the
    # utility trades ABOVE its sector and the software name trades BELOW its sector.
    medians = {"Utilities": {"pe": 11.0, "pb": 1.6, "ev_ebitda": 9.0, "ps": 2.0, "count": 10},
               "Technology": {"pe": 32.0, "pb": 7.0, "ev_ebitda": 18.0, "ps": 8.0, "count": 10}}
    utility = _fund(sector="Utilities", pe=14.0, pb=2.0, ev_ebitda=11.0, ps=2.5)   # above its sector
    software = _fund(sector="Technology", pe=26.0, pb=5.0, ev_ebitda=14.0, ps=6.0) # below its sector
    u = vs.compute_valuation(utility, medians).parts["vs_sector"]
    s = vs.compute_valuation(software, medians).parts["vs_sector"]
    assert s > u                                       # software is the better value FOR ITS SECTOR


# ── Cache ───────────────────────────────────────────────────────────────────

def test_cache_put_and_sector_medians_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(vs, "STATE_DIR", str(tmp_path))
    cache = {}
    today = datetime.date.today()
    for i in range(vs.THRESHOLDS["min_sector_count"]):
        f = _fund(sector="Financials", pe=10.0 + i)
        f.ticker = f"BANK{i}"
        vs.cache_put(cache, f, today)
    vs.save_fund_cache(cache)
    assert "_cached" in next(iter(cache.values()))
    reloaded = vs.load_fund_cache()
    assert "Financials" in vs.compute_sector_medians(reloaded)


def test_refresh_skips_fresh_entries():
    today = datetime.date.today()
    cache = {"FRESH": {"sector": "Tech", "pe": 12, "_cached": today.isoformat()}}

    class CountingProvider:
        def __init__(self): self.calls = 0
        def fundamentals(self, tk):
            self.calls += 1
            return _fund()

    p = CountingProvider()
    # FRESH is current; STALE_NEW has no entry -> only the missing one is fetched.
    vs.refresh_fundamentals_cache(p, cache, ["FRESH", "STALE_NEW"], today)
    assert p.calls == 1
