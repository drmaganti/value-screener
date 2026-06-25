"""
Integration tests: the whole funnel end to end, plus the cooldown state that
has to survive between cloud runs.

These run on the synthetic provider and the mock classifier, so they're fast,
deterministic, and need no network or keys — but they exercise the real
pipeline code path that runs in production.
"""
import json
import value_screener as vs


# ── Pipeline ────────────────────────────────────────────────────────────────

def test_pipeline_never_recommends_known_traps():
    # INTC (guidance cut) and VZ (lawsuit) are synthetic value traps. They may
    # pass the technical screen, but the catalyst filter must keep them out of
    # the buy list every single time.
    picks, rejected = vs.run_screen(vs.SyntheticProvider(), vs.MockClassifier(), set())
    pick_tickers = {c.fund.ticker for c in picks}
    rej_tickers = {c.fund.ticker for c in rejected}
    assert "INTC" not in pick_tickers and "VZ" not in pick_tickers
    assert "INTC" in rej_tickers and "VZ" in rej_tickers


def test_picks_are_sorted_capped_and_above_threshold():
    picks, _ = vs.run_screen(vs.SyntheticProvider(), vs.MockClassifier(), set())
    scores = [c.composite for c in picks]
    assert scores == sorted(scores, reverse=True)               # best first
    assert len(picks) <= vs.THRESHOLDS["max_picks"]             # capped
    assert all(s >= vs.THRESHOLDS["min_composite"] for s in scores)  # cleared the bar


def test_every_pick_carries_full_evidence():
    # The email promises a reason and a source for each name; make sure the
    # pipeline actually populates them.
    picks, _ = vs.run_screen(vs.SyntheticProvider(), vs.MockClassifier(), set())
    for c in picks:
        assert c.val is not None and c.qual is not None and c.cat is not None
        assert c.cat.reason and c.cat.category


def test_cooldown_suppresses_recently_sent_names():
    picks1, _ = vs.run_screen(vs.SyntheticProvider(), vs.MockClassifier(), set())
    assert picks1, "expected at least one pick to exercise the cooldown"
    already_sent = {picks1[0].fund.ticker}
    picks2, _ = vs.run_screen(vs.SyntheticProvider(), vs.MockClassifier(), already_sent)
    assert picks1[0].fund.ticker not in {c.fund.ticker for c in picks2}


# ── Cooldown state (state.json) ─────────────────────────────────────────────

def test_state_load_respects_the_cooldown_window(tmp_path, monkeypatch):
    sf = tmp_path / "state.json"
    monkeypatch.setattr(vs, "STATE_FILE", str(sf))
    today = vs.date.today()
    sf.write_text(json.dumps({"sent": {
        "RECENT": (today - vs.timedelta(days=1)).isoformat(),
        "OLD":    (today - vs.timedelta(days=30)).isoformat(),
    }}))
    within, _ = vs.load_recently_sent(cooldown_days=5)
    assert "RECENT" in within and "OLD" not in within


def test_state_write_prunes_stale_entries(tmp_path, monkeypatch):
    sf = tmp_path / "state.json"
    monkeypatch.setattr(vs, "STATE_FILE", str(sf))
    today = vs.date.today()
    sf.write_text(json.dumps({"sent": {"OLD": (today - vs.timedelta(days=30)).isoformat()}}))
    _, data = vs.load_recently_sent(cooldown_days=5)
    vs.record_sent(data, ["NEW"], cooldown_days=5)
    saved = json.loads(sf.read_text())["sent"]
    assert "NEW" in saved and "OLD" not in saved      # stale entry pruned on write


def test_state_missing_file_is_handled(tmp_path, monkeypatch):
    sf = tmp_path / "does_not_exist.json"
    monkeypatch.setattr(vs, "STATE_FILE", str(sf))
    within, data = vs.load_recently_sent(cooldown_days=5)
    assert within == set() and data == {"sent": {}}   # clean start, no crash
