"""API behavior tests: validation, demo-mode data flow, positions, AI-disabled chat."""
from __future__ import annotations

from datetime import datetime, timezone


def _refresh(client, group="stations"):
    r = client.post(f"/api/satellites/refresh/{group}")
    assert r.status_code == 200, r.text
    return r.json()


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["demo_mode"] is True
    assert body["ai_enabled"] is False


def test_refresh_demo_mode_loads_labeled_data(client):
    meta = _refresh(client)
    assert meta["source"] == "demo"
    assert meta["count"] > 0
    assert "Demo mode" in (meta["notice"] or "")
    assert datetime.fromisoformat(meta["last_updated"]).tzinfo is not None


def test_list_search_filter_and_pagination(client):
    _refresh(client)
    r = client.get("/api/satellites", params={"group": "stations"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(s["norad_id"] == "25544" for s in items)

    r = client.get("/api/satellites", params={"q": "ISS"})
    assert r.json()["total"] >= 1
    assert all("iss" in s["name"].lower() or s["norad_id"] == "ISS" for s in r.json()["items"])

    r = client.get("/api/satellites", params={"limit": 1, "offset": 1})
    assert len(r.json()["items"]) <= 1


def test_list_validation_errors(client):
    assert client.get("/api/satellites", params={"limit": 0}).status_code == 422
    assert client.get("/api/satellites", params={"limit": 99999}).status_code == 422
    assert client.get("/api/satellites", params={"group": "bogus"}).status_code == 404
    assert client.post("/api/satellites/refresh/bogus").status_code == 404


def test_get_satellite_by_id(client):
    _refresh(client)
    r = client.get("/api/satellites/25544")
    assert r.status_code == 200
    body = r.json()
    assert body["name"].startswith("ISS")
    els = body["orbital_elements"]
    assert abs(els["inclination_deg"] - 51.6416) < 1e-3
    # Regression guard: inclination must NOT be the arg-of-perigee value.
    assert els["inclination_deg"] != els["arg_perigee_deg"]
    assert abs(els["arg_perigee_deg"] - 130.536) < 1e-3


def test_get_satellite_bad_ids(client):
    assert client.get("/api/satellites/notanumber").status_code == 422
    assert client.get("/api/satellites/99999999").status_code == 404


def test_position_endpoint_defaults_to_now(client):
    _refresh(client)
    r = client.get("/api/satellites/25544/position")
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "calculated_from_tle"
    p = body["position"]
    assert -90 <= p["latitude"] <= 90 and -180 <= p["longitude"] <= 180
    assert 200 < p["altitude_km"] < 800  # ISS-ish demo orbit
    assert 7.0 < p["velocity_km_s"] < 8.5
    assert abs(p["period_minutes"] - 92.9) < 2.0
    assert p["computed_at"]  # timestamp of calculation included


def test_position_at_requested_time(client):
    _refresh(client)
    r = client.get("/api/satellites/25544/position",
                   params={"time": "2024-10-06T12:00:00Z"})
    assert r.status_code == 200
    p = r.json()["position"]
    assert p["computed_at"].startswith("2024-10-06T12:00:00")
    # Same time twice -> deterministic result.
    r2 = client.get("/api/satellites/25544/position",
                    params={"time": "2024-10-06T12:00:00Z"})
    assert r2.json()["position"]["latitude"] == p["latitude"]


def test_position_invalid_time(client):
    _refresh(client)
    r = client.get("/api/satellites/25544/position", params={"time": "yesterday"})
    assert r.status_code == 422
    assert "ISO-8601" in r.json()["detail"]


def test_ground_track(client):
    _refresh(client)
    r = client.get("/api/satellites/25544/ground-track", params={"points": 24})
    assert r.status_code == 200
    pts = r.json()["points"]
    assert len(pts) == 25  # points + closing sample
    assert all(-90 <= pt["lat"] <= 90 for pt in pts)
    # SGP4 perturbations can push the sub-satellite latitude slightly beyond
    # the mean inclination, so use a generous physical bound.
    assert max(abs(pt["lat"]) for pt in pts) <= 60.0
    assert client.get("/api/satellites/25544/ground-track", params={"points": 1}).status_code == 422
    assert client.get("/api/satellites/99999999/ground-track").status_code == 404


def test_chat_disabled_without_key(client):
    r = client.get("/api/assistant/status")
    assert r.json() == {"enabled": False, "model": None}
    r = client.post("/api/chat", json={"question": "Where is the ISS?"})
    assert r.status_code == 200
    body = r.json()
    assert body["disabled"] is True
    assert "OPENAI_API_KEY" in body["answer"]
    assert body["tool_calls"] == []


def test_chat_validation(client):
    assert client.post("/api/chat", json={"question": ""}).status_code == 422
    assert client.post("/api/chat", json={"question": "x" * 1001}).status_code == 422
    assert client.post("/api/chat", json={
        "question": "ok", "history": [{"role": "system", "content": "nope"}]}).status_code == 422


def test_frontend_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "OrbitDesk" in r.text
    r = client.get("/static/js/app.js")
    assert r.status_code == 200
