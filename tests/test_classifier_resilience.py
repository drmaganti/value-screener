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
