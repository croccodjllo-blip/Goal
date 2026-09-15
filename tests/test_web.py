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
    assert "Goal" in resp.text or "--bg" in resp.text
