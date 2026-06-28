"""
Integration tests for the Phase 1 pipeline and the outcome log.

Runs on the synthetic provider / mock classifier / synthetic analyst, so it's
fast, deterministic, and needs no network or keys -- but exercises the real
weekly code path: scan -> funnel -> cutoff -> top-N -> log -> mature outcomes.
"""
import datetime
import value_screener as vs


def _components():
    return vs.SyntheticProvider(), vs.MockClassifier(), vs.SyntheticAnalyst()


# ── Pipeline ────────────────────────────────────────────────────────────────

def test_pipeline_never_recommends_known_traps():
    p, clf, an = _components()
    qualifiers, featured, rejected = vs.run_screen(p, clf, an, exclude=set())
    quals = {c.fund.ticker for c in qualifiers}
    rejs = {c.fund.ticker for c in rejected}
    assert "INTC" not in quals and "VZ" not in quals
    assert "INTC" in rejs and "VZ" in rejs


def test_qualifiers_respect_cutoff_and_are_sorted():
    p, clf, an = _components()
    qualifiers, _, _ = vs.run_screen(p, clf, an, exclude=set())
    scores = [c.composite for c in qualifiers]
    assert scores == sorted(scores, reverse=True)
    assert all(s >= vs.THRESHOLDS["min_composite"] for s in scores)
    assert len(qualifiers) <= vs.THRESHOLDS["max_picks_per_run"]


def test_only_top_n_are_featured_with_analysis():
    p, clf, an = _components()
    qualifiers, featured, _ = vs.run_screen(p, clf, an, exclude=set())
    assert len(featured) <= vs.THRESHOLDS["featured"]
    assert featured == qualifiers[:len(featured)]
    for c in featured:
        assert len(c.analysis) > 200
    for c in qualifiers[len(featured):]:
        assert c.analysis == ""


def test_exclude_set_removes_names_from_scan():
    p, clf, an = _components()
    base, _, _ = vs.run_screen(p, clf, an, exclude=set())
    assert base, "expected qualifiers to test exclusion"
    drop = base[0].fund.ticker
    after, _, _ = vs.run_screen(p, clf, an, exclude={drop})
    assert drop not in {c.fund.ticker for c in after}


# ── Days-in-decline (reversal vs momentum) ──────────────────────────────────

def test_days_in_decline_distinguishes_fresh_dip_from_long_slide():
    fresh = [100.0 + i for i in range(250)] + [348.0, 344.0]
    slide = [100.0 + i for i in range(200)] + [299.0 - i for i in range(52)]
    assert vs._days_in_decline(fresh) < 10
    assert vs._days_in_decline(slide) > 40


# ── Outcome log ─────────────────────────────────────────────────────────────

def test_open_tickers_uses_the_open_window():
    today = datetime.date.today()
    log = {"picks": [
        {"ticker": "FRESH", "pick_date": (today - datetime.timedelta(days=5)).isoformat()},
        {"ticker": "OLD", "pick_date": (today - datetime.timedelta(days=60)).isoformat()},
    ]}
    openset = vs.open_tickers(log, today)
    assert "FRESH" in openset and "OLD" not in openset


def test_update_outcomes_fills_matured_horizons():
    today = datetime.date.today()
    log = {"picks": [{
        "ticker": "AAA", "pick_date": (today - datetime.timedelta(days=40)).isoformat(),
        "pick_price": 100.0, "bench_at_pick": 400.0,
        "outcomes": {k: None for k in vs.HORIZONS},
        "outcomes_bench": {k: None for k in vs.HORIZONS},
    }]}

    class StubProvider:
        def last_price(self, t): return 110.0 if t == "AAA" else 404.0

    filled = vs.update_outcomes(StubProvider(), log, today)
    o = log["picks"][0]["outcomes"]
    assert o["1w"] == 10.0 and o["1m"] == 10.0
    assert o["3m"] is None and o["6m"] is None
    assert log["picks"][0]["outcomes_bench"]["1m"] == 1.0
    assert filled == 2


def test_track_record_stats_counts_beats():
    log = {"picks": [
        {"ticker": "WIN", "pick_date": "2026-01-01",
         "outcomes": {"1m": 8.0}, "outcomes_bench": {"1m": 2.0}},
        {"ticker": "LOSE", "pick_date": "2026-01-01",
         "outcomes": {"1m": -3.0}, "outcomes_bench": {"1m": 1.0}},
        {"ticker": "PENDING", "pick_date": "2026-06-01",
         "outcomes": {"1m": None}, "outcomes_bench": {"1m": None}},
    ]}
    s = vs.track_record_stats(log)
    assert s["n"] == 2 and s["beats"] == 1


def test_log_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(vs, "STATE_DIR", str(tmp_path))
    vs.save_log({"picks": [{"ticker": "ZZZ", "pick_date": "2026-06-01",
                            "outcomes": {}, "outcomes_bench": {}}]})
    assert vs.load_log()["picks"][0]["ticker"] == "ZZZ"


def test_empty_log_is_handled(tmp_path, monkeypatch):
    monkeypatch.setattr(vs, "STATE_DIR", str(tmp_path))
    assert vs.load_log() == {"picks": []}
    assert vs.open_tickers({"picks": []}, datetime.date.today()) == set()
    assert vs.track_record_stats({"picks": []}) is None
