"""The tiny JSON-over-HTTPS helper: params, retries on transient statuses,
and clean errors. urlopen is mocked — no test touches the network."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

import watchman.data.http as http
from watchman.data.http import HttpError, get_json


class FakeResponse:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_get_json_parses_and_passes_params(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["ua"] = request.headers.get("User-agent")
        return FakeResponse({"ok": True, "n": 3})

    monkeypatch.setattr(http.urllib.request, "urlopen", fake_urlopen)
    out = get_json("https://api.example.com/v1/data", {"symbol": "AAPL", "token": "k"})
    assert out == {"ok": True, "n": 3}
    assert "symbol=AAPL" in seen["url"] and "token=k" in seen["url"]
    assert "watchman" in seen["ua"]


def test_none_params_are_dropped(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        return FakeResponse({})

    monkeypatch.setattr(http.urllib.request, "urlopen", fake_urlopen)
    get_json("https://x.test/y", {"a": 1, "b": None})
    assert "a=1" in seen["url"]
    assert "b=" not in seen["url"]


def test_retries_then_succeeds_on_transient_500(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(request, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(request.full_url, 503, "busy", {}, io.BytesIO(b""))
        return FakeResponse({"recovered": True})

    monkeypatch.setattr(http.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(http.time, "sleep", lambda _s: None)  # no real backoff
    assert get_json("https://x.test/y", retries=2) == {"recovered": True}
    assert calls["n"] == 2


def test_non_retryable_status_raises_http_error(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 401, "unauthorized", {}, io.BytesIO(b"bad key")
        )

    monkeypatch.setattr(http.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(HttpError) as exc:
        get_json("https://x.test/y")
    assert exc.value.status == 401
    assert "401" in str(exc.value)


def test_rate_limit_exhausts_retries_then_raises(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 429, "slow down", {}, io.BytesIO(b""))

    monkeypatch.setattr(http.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(http.time, "sleep", lambda _s: None)
    with pytest.raises(HttpError) as exc:
        get_json("https://x.test/y", retries=1)
    assert exc.value.status == 429


def test_network_failure_is_wrapped(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("name resolution failed")

    monkeypatch.setattr(http.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(http.time, "sleep", lambda _s: None)
    with pytest.raises(HttpError, match="failed"):
        get_json("https://x.test/y", retries=1)
