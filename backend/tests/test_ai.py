"""AI module tests with mocked external services (no real network calls)."""
from __future__ import annotations

import json

import pytest


@pytest.fixture()
def ai_mod(app_env):
    from backend.app import ai, db
    # Seed demo data so tools have something to read.
    from backend.app import celestrak
    celestrak.fetch_group("stations")
    return ai, db


def test_tools_read_backend_data(ai_mod):
    ai, db = ai_mod
    res = json.loads(ai._execute_tool("search_satellites", {"name": "ISS"}))
    assert res["count"] >= 1
    norad = res["results"][0]["norad_id"]

    details = json.loads(ai._execute_tool("satellite_details", {"norad_id": norad}))
    assert details["name"].startswith("ISS")
    assert abs(details["orbital_elements"]["inclination_deg"] - 51.6416) < 1e-3

    pos = json.loads(ai._execute_tool("satellite_position", {"norad_id": norad}))
    assert "latitude" in pos["position"]
    assert pos["source"] == "demo"  # grounded in labeled demo cache

    missing = json.loads(ai._execute_tool("satellite_details", {"norad_id": "99999999"}))
    assert "error" in missing and "in the cache" in missing["error"]

    bad_time = json.loads(ai._execute_tool("satellite_position", {"norad_id": norad, "time_utc": "nope"}))
    assert "error" in bad_time

    unknown = json.loads(ai._execute_tool("frobnicate", {}))
    assert "Unknown tool" in unknown["error"]


def test_chat_disabled_returns_helpful_message(monkeypatch, ai_mod):
    ai, _ = ai_mod
    monkeypatch.setattr(ai.settings, "openai_api_key", "")
    out = ai.chat("Where is the ISS?")
    assert out["disabled"] is True
    assert "OPENAI_API_KEY" in out["answer"]
    assert out["tool_calls"] == []


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            request = httpx.Request("POST", "https://api.example.test")
            resp = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("boom", request=request, response=resp)


class FakeClient:
    """Stands in for httpx.Client; records requests, replays queued responses."""
    responses: list[FakeResponse] = []
    calls: list[dict] = []

    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, headers=None, json=None):
        FakeClient.calls.append({"url": url, "headers": headers, "json": json})
        return FakeClient.responses.pop(0)


def test_chat_uses_tools_then_answers_grounded(monkeypatch, ai_mod):
    import httpx
    ai, _ = ai_mod
    monkeypatch.setattr(ai.settings, "openai_api_key", "sk-test-key-not-real")

    tool_call_msg = {
        "choices": [{"message": {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_1",
                "function": {
                    "name": "satellite_position",
                    "arguments": json.dumps({"norad_id": "25544"}),
                },
            }],
        }}]
    }
    final_msg = {
        "choices": [{"message": {"role": "assistant",
                                 "content": "The ISS is at the coordinates returned by our SGP4 tool."}}]
    }
    FakeClient.responses = [FakeResponse(tool_call_msg), FakeResponse(final_msg)]
    FakeClient.calls = []
    monkeypatch.setattr(httpx, "Client", FakeClient)

    out = ai.chat("Where is the ISS right now?")
    assert out["disabled"] is False
    assert out["grounded"] is True
    assert [t["tool"] for t in out["tool_calls"]] == ["satellite_position"]
    # Key stays server-side: sent as auth header only, never in a body field.
    auth = FakeClient.calls[0]["headers"]["Authorization"]
    assert auth == "Bearer sk-test-key-not-real"
    body = json.dumps(FakeClient.calls[0]["json"])
    assert "sk-test" not in body


def test_chat_network_error_is_graceful(monkeypatch, ai_mod):
    import httpx
    ai, _ = ai_mod
    monkeypatch.setattr(ai.settings, "openai_api_key", "sk-test")

    class BoomClient(FakeClient):
        def post(self, *a, **k):
            raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "Client", BoomClient)
    out = ai.chat("hello")
    assert out["error"] is True
    assert "couldn't reach the AI service" in out["answer"]
