"""Resilience tests for the production Groq catalyst classifier."""
import os

import requests

import value_screener as vs


class _Response:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.HTTPError(f"HTTP {self.status_code}")
            err.response = self
            raise err

    def json(self):
        return self._payload


def _classifier(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("GROQ_MAX_RETRIES", "0")
    return vs.GroqClassifier()


def test_http_auth_failure_uses_non_structural_fallback_for_neutral_news(monkeypatch):
    clf = _classifier(monkeypatch)
    monkeypatch.setattr(
        requests,
        "post",
        lambda *args, **kwargs: _Response(401),
    )

    verdict = clf.classify("AAA", ["Shares dip with the broader market"])

    assert verdict.classification_source == "deterministic_fallback"
    assert verdict.classifier_error == "HTTP 401"
    assert verdict.category == "market"
    assert verdict.veto is False


def test_http_failure_fallback_still_vetoes_known_structural_trap(monkeypatch):
    clf = _classifier(monkeypatch)
    monkeypatch.setattr(
        requests,
        "post",
        lambda *args, **kwargs: _Response(403),
    )

    verdict = clf.classify("BBB", ["Company cuts guidance after accounting probe"])

    assert verdict.classification_source == "deterministic_fallback"
    assert verdict.category == "structural"
    assert verdict.veto is True


def test_successful_groq_response_is_marked_as_groq(monkeypatch):
    clf = _classifier(monkeypatch)
    payload = {
        "choices": [{
            "message": {
                "content": '{"category":"sector","transient":true,"reason":"Sector pressure.","source":"headline"}'
            }
        }]
    }
    monkeypatch.setattr(
        requests,
        "post",
        lambda *args, **kwargs: _Response(200, payload=payload),
    )

    verdict = clf.classify("CCC", ["Sector rotation weighs on shares"])

    assert verdict.classification_source == "groq"
    assert verdict.classifier_error is None
    assert verdict.category == "sector"
    assert verdict.veto is False


def test_missing_headlines_never_calls_groq_or_vetoes(monkeypatch):
    clf = _classifier(monkeypatch)
    monkeypatch.setattr(requests, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected API call")))
    for news in ([], ["No recent headlines retrieved"], vs.NewsResult([])):
        verdict = clf.classify("AAA", news)
        assert verdict.category == "unknown"
        assert not verdict.veto
        assert verdict.news_status == "no_headlines"


def test_provider_error_preserved(monkeypatch):
    clf = _classifier(monkeypatch)
    verdict = clf.classify("AAA", vs.NewsResult([], "provider_error", "TimeoutError"))
    assert (verdict.category, verdict.veto) == ("unknown", False)
    assert verdict.news_status == "provider_error"
    assert verdict.news_error == "TimeoutError"


def test_yfinance_distinguishes_empty_and_failed_news(monkeypatch):
    from types import SimpleNamespace
    provider = object.__new__(vs.YFinanceProvider)
    monkeypatch.setattr(provider, "_t", lambda ticker: SimpleNamespace(news=[]))
    assert provider.recent_news("AAA").status == "no_headlines"
    monkeypatch.setattr(provider, "_t", lambda ticker: (_ for _ in ()).throw(TimeoutError()))
    news = provider.recent_news("AAA")
    assert news.status == "provider_error"
    assert news.error == "TimeoutError"


def test_structural_groq_requires_real_negative_source(monkeypatch):
    clf = _classifier(monkeypatch)
    for headline, source, expected in [
        ("Company cuts guidance after accounting probe", "Company cuts guidance after accounting probe", "structural"),
        ("Shares trade lower", "Company cuts guidance", "unknown"),
        ("Company denies accounting fraud", "Company denies accounting fraud", "unknown"),
        ("Company reaffirms guidance", "Company reaffirms guidance", "unknown"),
        ("Company may cut guidance", "Company may cut guidance", "unknown"),
        ("Company announces no guidance cut", "Company announces no guidance cut", "unknown"),
    ]:
        import json
        payload = {"choices": [{"message": {"content": json.dumps(dict(category="structural", transient=False, reason="Risk", source=source))}}]}
        monkeypatch.setattr(requests, "post", lambda *a, **k: _Response(200, payload))
        verdict = clf.classify("AAA", [headline])
        assert verdict.category == expected
        assert verdict.veto == (expected == "structural")
        assert verdict.classification_source == "groq"


def test_fallback_checks_all_headlines_without_inventing_market_evidence(monkeypatch):
    clf = _classifier(monkeypatch)
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Response(401))
    assert clf.classify("AAA", ["Company announces event"]).category == "unknown"
    verdict = clf.classify("AAA", ["Company announces event", "Company cuts guidance"])
    assert verdict.veto
    assert verdict.source == "Company cuts guidance"


def test_unknown_groq_response_and_strict_types(monkeypatch):
    clf = _classifier(monkeypatch)
    payload = {"choices": [{"message": {"content": '{"category":"unknown","transient":false,"reason":"Unclear","source":""}'}}]}
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Response(200, payload))
    verdict = clf.classify("AAA", ["Company announces event"])
    assert verdict.category == "unknown" and not verdict.veto
    import pytest
    with pytest.raises(ValueError):
        vs._verdict('{"category":"structural","transient":"false"}')


def test_monitoring_and_candidate_signals_preserve_unknown():
    from types import SimpleNamespace
    import value_screener_v2 as v2
    cats = [vs.unknown_catalyst(vs.NewsResult([], "provider_error", "TimeoutError")),
            vs.unknown_catalyst(vs.NewsResult([])),
            vs.CatalystVerdict("structural", False, "Risk", "Company cuts guidance", veto=True)]
    cats[-1].classification_source = "deterministic_fallback"
    candidates = [SimpleNamespace(cat=cat) for cat in cats]
    metrics = v2.catalyst_metrics(candidates)
    assert metrics["evaluated"] == 3
    assert metrics["news_retrieval_failures"] == metrics["structural_vetoes"] == metrics["fallbacks"] == 1
    assert metrics["unknown_classifications"] == 2
    assert metrics["rates"]["unknown_classifications"] == 2 / 3
    assert v2.catalyst_metrics([])["rates"]["fallbacks"] is None
