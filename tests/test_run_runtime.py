"""Runtime regressions: repeated outcomes must not multiply live history requests."""
from datetime import date, timedelta
import json
from types import SimpleNamespace

import pytest
import reporting_integrity as integrity
import value_screener as core
import value_screener_v2 as v2
from run_diagnostics import RunDiagnostics


class History:
    def __len__(self):
        return 2

    def __getitem__(self, key):
        assert key == "Close"
        return SimpleNamespace(iloc=[100.0, 110.0])


def test_history_reuses_identical_windows_and_keeps_boundary_prices(monkeypatch):
    calls = []
    def history(**kwargs):
        calls.append(kwargs)
        return History()
    provider = object.__new__(core.YFinanceProvider)
    monkeypatch.setattr(provider, "_t", lambda ticker: SimpleNamespace(history=history))
    target = date(2026, 10, 12)
    for _ in range(285):
        assert v2._price_on_or_after(provider, "SPY", target) == 100
    assert len(calls) == 1
    assert calls[0] == {"start": "2026-10-12", "end": "2026-10-20", "timeout": 10}
    assert integrity._price_on_or_before(provider, "SPY", target) == 110
    assert len(calls) == 2
    v2._price_on_or_after(provider, "XIU.TO", target)
    v2._price_on_or_after(provider, "SPY", target + timedelta(days=1))
    assert len(calls) == 4
    # A new run/provider retries independently rather than keeping stale failures.
    second = object.__new__(core.YFinanceProvider)
    monkeypatch.setattr(second, "_t", lambda ticker: SimpleNamespace(history=history))
    v2._price_on_or_after(second, "SPY", target)
    assert len(calls) == 5


@pytest.mark.parametrize("fails", [True, False])
def test_failed_or_empty_history_is_not_repeated_within_run(monkeypatch, fails):
    calls = []
    def history(**kwargs):
        calls.append(kwargs)
        if fails:
            raise TimeoutError("unavailable")
        return None
    provider = object.__new__(core.YFinanceProvider)
    monkeypatch.setattr(provider, "_t", lambda ticker: SimpleNamespace(history=history))
    for _ in range(10):
        assert v2._price_on_or_after(provider, "SPY", date(2026, 10, 12)) is None
    assert len(calls) == 1


def test_diagnostics_persist_running_stage_before_call_and_report_completion(tmp_path):
    path = tmp_path / "progress.json"
    diagnostics = RunDiagnostics(path)
    def work():
        state = json.loads(path.read_text())
        assert state["stages"][-1]["status"] == "running"
        return 42
    assert diagnostics.run("history", work) == 42
    diagnostics.complete()
    state = json.loads(path.read_text())
    assert state["status"] == "completed"
    assert state["stages"][0]["status"] == "completed"
    assert state["stages"][0]["elapsed_seconds"] >= 0


def test_diagnostics_preserve_failure_type_without_exception_message(tmp_path):
    path = tmp_path / "progress.json"
    diagnostics = RunDiagnostics(path)
    def work():
        raise RuntimeError("potentially sensitive provider message")
    with pytest.raises(RuntimeError):
        diagnostics.run("provider", work)
    state = json.loads(path.read_text())
    assert state["status"] == "failed"
    assert state["stages"][0]["error_type"] == "RuntimeError"
    assert "sensitive" not in path.read_text()
