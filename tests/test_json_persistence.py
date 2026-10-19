"""Regressions for the October 19 candidate-log serialization failure."""
import json
import math
from datetime import date

import pytest

import json_persistence as persistence
import reporting_integrity as integrity
import value_screener as core
import value_screener_v2 as v2


def strict_load(path):
    def reject(value):
        raise AssertionError(f"Non-standard JSON constant: {value}")
    return json.loads(path.read_text(), parse_constant=reject)


@pytest.mark.parametrize("writer", [integrity.save_json_strict, v2.save_candidate_log])
def test_nested_candidate_non_finite_values_become_null(tmp_path, monkeypatch, writer):
    monkeypatch.setattr(core, "STATE_DIR", str(tmp_path))
    path = tmp_path / v2.CANDIDATES_LOG
    row = {
        "signals": {"quality": [float("nan"), {"ratio": float("inf")}],
                    "valuation": (float("-inf"), 12.5)},
        "outcomes": {"1w": 5.0, "1m": float("nan")},
        "outcomes_bench": {"1w": 2.0},
        "corrected_outcomes": {"1w": 4.75},
        "legacy_outcomes_snapshot": {"1w": 9.0},
        "zero": 0, "negative": -2.5, "selected": False,
        "missing": None, "label": "NaN", "date": date(2026, 10, 19),
    }
    payload = {"candidates": [row]}
    if writer is v2.save_candidate_log:
        writer(payload)
    else:
        writer(path, payload)
    saved = strict_load(path)["candidates"][0]
    assert saved["signals"] == {"quality": [None, {"ratio": None}],
                                 "valuation": [None, 12.5]}
    assert saved["outcomes"] == {"1w": 5.0, "1m": None}
    for key in ("outcomes_bench", "corrected_outcomes", "legacy_outcomes_snapshot",
                "zero", "negative", "selected", "missing", "label"):
        assert saved[key] == row[key]
    assert saved["date"] == "2026-10-19"
    assert math.isnan(row["outcomes"]["1m"])
    assert math.isinf(row["signals"]["valuation"][0])
    assert isinstance(row["signals"]["valuation"], tuple)


def test_serialization_failure_preserves_existing_log(tmp_path):
    path = tmp_path / "candidate_log.json"
    path.write_text('{"candidates": []}')
    with pytest.raises(TypeError):
        persistence.save_json_strict(path, {"invalid": {object(): 1}})
    assert path.read_text() == '{"candidates": []}'
    assert list(tmp_path.iterdir()) == [path]


def test_replace_failure_preserves_log_and_cleans_temp(tmp_path, monkeypatch):
    path = tmp_path / "candidate_log.json"
    path.write_text('{"candidates": []}')
    def fail(*args):
        raise OSError("replacement failed")
    monkeypatch.setattr(persistence.os, "replace", fail)
    with pytest.raises(OSError, match="replacement failed"):
        persistence.save_json_strict(path, {"candidates": [1]})
    assert strict_load(path) == {"candidates": []}
    assert list(tmp_path.iterdir()) == [path]


def test_matured_candidate_outcomes_survive_persistence(tmp_path):
    class Provider:
        def price_on_or_after(self, ticker, target):
            return {"ABC": 105.0, "SPY": 102.0}[ticker]
    row = {"ticker": "ABC", "pick_date": "2026-10-12", "pick_price": 100.0,
           "benchmark": "SPY", "bench_at_pick": 100.0,
           "outcomes": {}, "outcomes_bench": {},
           "signals": {"ratio": float("nan")}}
    payload = {"candidates": [row]}
    assert v2.update_candidate_outcomes(Provider(), payload, date(2026, 10, 19)) == 1
    path = tmp_path / "candidate_log.json"
    integrity.save_json_strict(path, payload)
    saved = strict_load(path)["candidates"][0]
    assert saved["outcomes"]["1w"] == 5.0
    assert saved["outcomes_bench"]["1w"] == 2.0
