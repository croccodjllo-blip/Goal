"""FastAPI web smoke tests — no live network required."""

from __future__ import annotations

from fastapi.testclient import TestClient

from goal_xg.web.app import create_app


def test_health_ok(monkeypatch) -> None:
    monkeypatch.delenv("GOAL_API_KEY", raising=False)
    monkeypatch.delenv("FOOTBALL_DATA_API_KEY", raising=False)
    monkeypatch.delenv("FOOTBALL_DATA_TOKEN", raising=False)
    client = TestClient(create_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["service"] == "goal-xg"
    assert body["goal_api_key_configured"] is False
    assert body["football_data_configured"] is False


def test_health_reports_key(monkeypatch) -> None:
    monkeypatch.setenv("GOAL_API_KEY", "test-key-not-real")
    client = TestClient(create_app())
    body = client.get("/health").json()
    assert body["goal_api_key_configured"] is True


def test_index_without_key_shows_alert(monkeypatch) -> None:
    monkeypatch.delenv("GOAL_API_KEY", raising=False)
    client = TestClient(create_app())
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "GOAL_API_KEY" in resp.text
    assert "Live Big-5" in resp.text
    assert "league-group" in resp.text or "match-list" in resp.text or "empty" in resp.text
    assert "Goal" in resp.text and "xG" in resp.text


def test_api_live_fail_closed_without_key(monkeypatch) -> None:
    monkeypatch.delenv("GOAL_API_KEY", raising=False)
    client = TestClient(create_app())
    resp = client.get("/api/live")
    assert resp.status_code == 503


def test_static_css(monkeypatch) -> None:
    monkeypatch.delenv("GOAL_API_KEY", raising=False)
    client = TestClient(create_app())
    resp = client.get("/static/app.css")
    assert resp.status_code == 200
    assert "--bg" in resp.text
    assert ".match-row" in resp.text
    assert ".live-pill" in resp.text
    assert ".xg-ring" in resp.text


def test_fixture_without_key(monkeypatch) -> None:
    monkeypatch.delenv("GOAL_API_KEY", raising=False)
    client = TestClient(create_app())
    resp = client.get("/fixtures/123")
    assert resp.status_code == 200
    assert "GOAL_API_KEY" in resp.text
    assert "xg-panel" not in resp.text or "mancante" in resp.text


def test_index_and_api_live_share_one_fixtures_live(monkeypatch) -> None:
    """Quota fix: index / api.live must not double-hit /fixtures/live."""
    from typing import Any

    import httpx

    from goal_xg.clients.goal_api import GoalApiClient
    from goal_xg.web import app as webapp

    live_hits = {"n": 0}
    live_body = {
        "success": True,
        "data": [
            {
                "id": "fx-live",
                "matchElapsed": 30,
                "matchPeriod": "FIRST_HALF",
                "homeTeamScore": 0,
                "awayTeamScore": 0,
                "league": {"id": 135, "name": "Serie A"},
                "homeTeamName": "A",
                "awayTeamName": "B",
                "leagueName": "Serie A",
            }
        ],
    }
    leagues_body = {
        "success": True,
        "data": [
            {"id": 39, "name": "Premier League", "country": "England"},
            {"id": 140, "name": "La Liga", "country": "Spain"},
            {"id": 135, "name": "Serie A", "country": "Italy"},
            {"id": 78, "name": "Bundesliga", "country": "Germany"},
            {"id": 61, "name": "Ligue 1", "country": "France"},
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/fixtures/live"):
            live_hits["n"] += 1
            return httpx.Response(200, json=live_body)
        if path.endswith("/leagues"):
            return httpx.Response(200, json=leagues_body)
        return httpx.Response(404, json={"error": "nf"})

    transport = httpx.MockTransport(handler)
    http = httpx.Client(
        transport=transport,
        base_url="https://api.goal-api.com/v1",
        headers={"Authorization": "Bearer test"},
    )
    real_client = GoalApiClient(
        api_key="test-key-not-real", client=http, cache_dir="/tmp/goal-xg-web-cache"
    )

    monkeypatch.setenv("GOAL_API_KEY", "test-key-not-real")

    def fake_client_or_none() -> GoalApiClient:
        return real_client

    monkeypatch.setattr(webapp, "_client_or_none", fake_client_or_none)

    client = TestClient(create_app())
    live_hits["n"] = 0
    resp = client.get("/")
    assert resp.status_code == 200
    assert live_hits["n"] == 1

    live_hits["n"] = 0
    resp = client.get("/api/live")
    assert resp.status_code == 200
    body = resp.json()
    assert "live" in body and "live30_candidates" in body
    assert live_hits["n"] == 1
    http.close()
