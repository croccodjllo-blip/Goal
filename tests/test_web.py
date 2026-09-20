"""FastAPI web smoke tests — no live network required."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from goal_xg.web.app import create_app


def test_health_ok(monkeypatch) -> None:
    monkeypatch.delenv("GOAL_API_KEY", raising=False)
    monkeypatch.delenv("FOOTBALL_DATA_API_KEY", raising=False)
    monkeypatch.delenv("FOOTBALL_DATA_TOKEN", raising=False)
    monkeypatch.delenv("API_SPORTS_KEY", raising=False)
    monkeypatch.delenv("APISPORTS_KEY", raising=False)
    client = TestClient(create_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["service"] == "goal-xg"
    assert body["goal_api_key_configured"] is False
    assert body["football_data_configured"] is False
    assert body["api_sports_configured"] is False


def test_health_reports_key(monkeypatch) -> None:
    monkeypatch.setenv("GOAL_API_KEY", "test-key-not-real")
    monkeypatch.setenv("API_SPORTS_KEY", "test-apisports-not-real")
    client = TestClient(create_app())
    body = client.get("/health").json()
    assert body["goal_api_key_configured"] is True
    assert body["api_sports_configured"] is True


def test_index_without_key_shows_alert(monkeypatch) -> None:
    monkeypatch.delenv("GOAL_API_KEY", raising=False)
    client = TestClient(create_app())
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "GOAL_API_KEY" in resp.text
    assert "Partite di oggi" in resp.text
    assert "day-list" in resp.text or "coupon-list" in resp.text or "empty" in resp.text
    assert "Goal" in resp.text and "xG" in resp.text
    assert "day-filters" in resp.text or "Tutte" in resp.text
    assert 'data-filter="focus"' in resp.text


def test_static_css(monkeypatch) -> None:
    monkeypatch.delenv("GOAL_API_KEY", raising=False)
    client = TestClient(create_app())
    resp = client.get("/static/app.css")
    assert resp.status_code == 200
    assert "--bg" in resp.text
    assert ".match-row" in resp.text
    assert ".live-pill" in resp.text
    assert ".sched-pill" in resp.text
    assert ".match-link--sched" in resp.text
    assert ".coupon-row" in resp.text
    assert ".day-filters" in resp.text
    assert ".coupon-link" in resp.text
    assert "min-height: 1.85rem" in resp.text
    assert ".board-head--compact" in resp.text
    assert ".xg-ring" in resp.text
    assert ":focus-visible" in resp.text
    assert "--accent" in resp.text or "--brand-accent" in resp.text
    assert ".tag-short" in resp.text
    assert ".xg-badge.high" in resp.text
    assert ".components" in resp.text
    assert ".comp-row" in resp.text
    assert ".comp-head" in resp.text
    assert ".comp-dato" in resp.text
    assert ".settled-banner" in resp.text
    assert "prefers-reduced-motion" in resp.text
    assert "IBM Plex Mono" in resp.text or "IBM Plex Sans" in resp.text
    assert "Barlow Condensed" in resp.text or "--display" in resp.text
    assert "@keyframes rise-in" not in resp.text
    assert ".api-sports" in resp.text
    assert ".api-sports-row" in resp.text
    assert ".tag-index" in resp.text
    assert ".brand-mark" in resp.text
    assert "conic-gradient" in resp.text or ".xg-ring" in resp.text


def test_api_sports_stat_rows_for_display() -> None:
    from goal_xg.web.app import _api_sports_stat_rows_for_display

    rows = _api_sports_stat_rows_for_display(
        {
            "rows": [
                {
                    "type": "Ball Possession",
                    "label": "Possesso palla",
                    "home": "68%",
                    "away": "32%",
                    "home_1h": "70%",
                    "away_1h": "30%",
                    "in_index": False,
                },
                {
                    "type": "Total Shots",
                    "label": "Tiri totali",
                    "home": 10,
                    "away": 1,
                    "home_1h": 8,
                    "away_1h": 1,
                    "in_index": True,
                },
            ]
        }
    )
    assert len(rows) == 2
    assert rows[0]["label"] == "Possesso palla"
    assert rows[0]["in_index"] is False
    assert rows[1]["in_index"] is True
    assert rows[1]["home"] == "10"


def test_fixture_without_key(monkeypatch) -> None:
    monkeypatch.delenv("GOAL_API_KEY", raising=False)
    client = TestClient(create_app())
    resp = client.get("/fixtures/123")
    assert resp.status_code == 200
    assert "GOAL_API_KEY" in resp.text
    assert "xg-panel" not in resp.text or "mancante" in resp.text
    assert 'style="margin:0' not in resp.text
    assert "fixture-fallback" in resp.text


def test_component_rows_only_available_shot_criteria() -> None:
    from goal_xg.model.weights import BASE_WEIGHTS, COMPONENT_LABELS_IT
    from goal_xg.web.app import _component_rows, _settled_snapshot_rows

    rows = _component_rows(
        {
            "signals": {
                "sot": 0.55,
                "shot_xg": 0.70,
                "shots_total": 0.60,
                "goals_scored_last5_ha": 0.62,
                "standings": 0.58,
            },
            "weights_used": {
                "sot": 26.0,
                "shot_xg": 34.0,
                "shots_total": 20.0,
                "goals_scored_last5_ha": 10.0,
                "standings": 10.0,
            },
            "features": {
                "shots_total": 10,
                "sot_total": 5,
                "shot_xg_total": 0.87,
                "goals_scored_last5_ha_home_avg": 1.6,
                "goals_scored_last5_ha_away_avg": 1.2,
                "goals_scored_last5_ha_avg": 1.4,
                "standings_label": "3ª–12ª",
                "standings_home_rank": 3,
                "standings_away_rank": 12,
            },
        }
    )
    by_id = {r["id"]: r for r in rows}
    assert set(by_id) == {
        "sot",
        "shot_xg",
        "shots_total",
        "goals_scored_last5_ha",
        "standings",
    }
    assert all(r["status"] == "active" for r in rows)
    assert by_id["sot"]["label"] == COMPONENT_LABELS_IT["sot"]
    assert by_id["shot_xg"]["label"] == "Goal attesi (xG)"
    assert by_id["goals_scored_last5_ha"]["label"] == (
        "Media gol ultime 5 (casa/trasferta)"
    )
    assert by_id["standings"]["label"] == "Classifica"
    assert "34.0" in by_id["shot_xg"]["weight_display"]
    # Raw live/prematch inputs surface clearly.
    assert by_id["shots_total"]["raw_display"] == "10"
    assert by_id["sot"]["raw_display"] == "5"
    assert by_id["shot_xg"]["raw_display"] == "0.87"
    assert by_id["goals_scored_last5_ha"]["raw_display"] == "1.4"
    assert "casa" in (by_id["goals_scored_last5_ha"]["raw_detail"] or "")
    assert by_id["standings"]["raw_display"] == "3ª–12ª"
    assert by_id["shot_xg"]["contrib"] == pytest.approx(0.70 * 0.34)
    # Missing among the eleven → not listed.
    assert "xgot" not in by_id
    assert "woodwork" not in by_id
    # Old / removed criteria never listed.
    assert "residual_time" not in by_id
    assert "team_priors" not in by_id
    assert "live_ratings" not in by_id
    assert "corners" not in by_id

    leaked = _component_rows(
        {
            "signals": {"residual_time": 0.78, "form": 0.5, "sot": 0.5},
            "weights_used": {"residual_time": 50.0, "sot": 50.0},
        }
    )
    assert [r["id"] for r in leaked] == ["sot"]

    empty = _component_rows(None)
    assert empty == []
    assert len(BASE_WEIGHTS) == 11

    snap = _settled_snapshot_rows(
        {
            "settled": True,
            "signals": {},
            "features": {
                "shots_total": 8,
                "sot_total": 3,
                "settled": True,
            },
        }
    )
    labels = {r["label"] for r in snap}
    assert "Tiri totali" in labels
    assert "Tiri in porta" in labels


def test_api_live_fail_closed_without_key(monkeypatch) -> None:
    monkeypatch.delenv("GOAL_API_KEY", raising=False)
    client = TestClient(create_app())
    resp = client.get("/api/live")
    assert resp.status_code == 503


def test_filter_live_00_drops_scored() -> None:
    from goal_xg.web.app import _filter_live_00, _sort_live_watch

    cards = [
        {"fixture_id": "a", "is_00": True, "in_window": False, "minute": 12},
        {"fixture_id": "b", "is_00": False, "in_window": True, "minute": 30},
        {"fixture_id": "c", "is_00": True, "in_window": True, "minute": 29},
        {"fixture_id": "d", "is_00": True, "in_window": False, "minute": 40},
    ]
    kept = _filter_live_00(cards)
    assert [c["fixture_id"] for c in kept] == ["a", "c", "d"]
    ordered = _sort_live_watch(kept)
    # Window first, then closer to 30′ (40 is nearer than 12).
    assert [c["fixture_id"] for c in ordered] == ["c", "d", "a"]


def test_list_scheduled_big5_orders_and_fail_closed(monkeypatch) -> None:
    import httpx
    import tempfile

    from goal_xg.clients.goal_api import GoalApiClient
    from goal_xg.web.app import clear_schedule_cache, list_scheduled_big5

    clear_schedule_cache()
    leagues_body = {
        "success": True,
        "data": [
            {"id": "pl1", "name": "Premier League", "country": "England"},
            {"id": "pd1", "name": "La Liga", "country": "Spain"},
            {"id": "sa1", "name": "Serie A", "country": "Italy"},
            {"id": "bl1", "name": "Bundesliga", "country": "Germany"},
            {"id": "fl1", "name": "Ligue 1", "country": "France"},
        ],
    }
    by_league = {
        "pd1": [
            {
                "id": "fx-live",
                "matchStatus": "LIVE",
                "matchLive": "1",
                "matchDate": "2026-09-16",
                "matchTime": "17:00",
                "kickoffUtc": "2026-09-16T15:00:00.000Z",
                "homeTeamName": "Atl. Madrid",
                "awayTeamName": "Osasuna",
                "leagueName": "La Liga",
                "leagueId": "pd1",
                "homeTeamScore": 4,
                "awayTeamScore": 0,
                "matchElapsed": 90,
            },
            {
                "id": "fx-liga",
                "matchStatus": "SCHEDULED",
                "matchDate": "2026-09-16",
                "matchTime": "19:30",
                "kickoffUtc": "2026-09-16T17:30:00.000Z",
                "homeTeamName": "Levante",
                "awayTeamName": "Ath Bilbao",
                "leagueName": "La Liga",
                "leagueId": "pd1",
            },
        ],
        "pl1": [],
        "sa1": [],
        "bl1": [],
        "fl1": [],
    }
    hits: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/leagues"):
            return httpx.Response(200, json=leagues_body)
        if path.endswith("/fixtures"):
            q = dict(request.url.params)
            hits.append(q)
            lid = q.get("leagueId") or q.get("league")
            return httpx.Response(
                200, json={"success": True, "data": by_league.get(str(lid), [])}
            )
        return httpx.Response(404, json={"error": "nf"})

    transport = httpx.MockTransport(handler)
    http = httpx.Client(
        transport=transport,
        base_url="https://api.goal-api.com/v1",
        headers={"Authorization": "Bearer test"},
    )
    client = GoalApiClient(
        api_key="test-key-not-real",
        client=http,
        cache_dir=tempfile.mkdtemp(prefix="goal-xg-sched-"),
    )

    cards = list_scheduled_big5(
        client,
        day="2026-09-16",
        use_cache=False,
    )
    assert len(hits) == 5
    assert all(h.get("from") == "2026-09-16" and h.get("to") == "2026-09-16" for h in hits)
    by_id = {c["fixture_id"]: c for c in cards}
    assert set(by_id) == {"fx-live", "fx-liga"}
    assert by_id["fx-liga"]["status"] == "scheduled"
    assert by_id["fx-liga"]["kickoff_time"] == "19:30"
    assert by_id["fx-live"]["status"] == "live"
    assert by_id["fx-live"]["is_00"] is False
    http.close()
    clear_schedule_cache()


def test_index_and_api_live_share_one_fixtures_live(monkeypatch) -> None:
    """Quota fix: index / api.live must not double-hit /fixtures/live."""
    import httpx

    from goal_xg.clients.goal_api import GoalApiClient
    from goal_xg.web import app as webapp
    from goal_xg.web.app import clear_schedule_cache

    clear_schedule_cache()
    live_hits = {"n": 0}
    fixtures_hits = {"n": 0}
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
            },
            {
                "id": "fx-scored",
                "matchElapsed": 40,
                "matchPeriod": "FIRST_HALF",
                "homeTeamScore": 1,
                "awayTeamScore": 0,
                "league": {"id": 135, "name": "Serie A"},
                "homeTeamName": "E",
                "awayTeamName": "F",
                "leagueName": "Serie A",
            },
            {
                "id": "fx-early",
                "matchElapsed": 12,
                "matchPeriod": "FIRST_HALF",
                "homeTeamScore": 0,
                "awayTeamScore": 0,
                "league": {"id": 135, "name": "Serie A"},
                "homeTeamName": "C",
                "awayTeamName": "D",
                "leagueName": "Serie A",
            },
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
        if path.endswith("/fixtures"):
            fixtures_hits["n"] += 1
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": [
                        {
                            "id": "fx-sched",
                            "matchStatus": "SCHEDULED",
                            "kickoffUtc": "2026-09-18T18:00:00.000Z",
                            "homeTeamName": "Home",
                            "awayTeamName": "Away",
                            "leagueName": "Serie A",
                        }
                    ],
                },
            )
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
    fixtures_hits["n"] = 0
    clear_schedule_cache()
    resp = client.get("/")
    assert resp.status_code == 200
    assert live_hits["n"] == 1
    assert fixtures_hits["n"] == 5  # one from/to fetch per Big-5 league
    # Focus/window still surfaces the in-window 0-0; day list shows ALL today's matches.
    assert resp.text.count("fx-live") >= 1
    assert "fx-scored" in resp.text  # compact day list keeps settled live rows
    assert "Partite di oggi" in resp.text
    assert "day-filters" in resp.text
    assert 'data-filter="focus"' in resp.text
    assert "fx-sched" in resp.text
    assert "coupon-row" in resp.text or "day-list" in resp.text
    assert "brand-xg" in resp.text
    assert "tag-short" in resp.text

    live_hits["n"] = 0
    fixtures_hits["n"] = 0
    resp = client.get("/api/live")
    assert resp.status_code == 200
    body = resp.json()
    assert "live" in body and "live30_candidates" in body and "scheduled" in body
    assert "focus" in body and body["focus"]["fixture_id"] == "fx-live"
    assert live_hits["n"] == 1
    # Schedule cache should avoid a second wave of 5 fixtures calls.
    assert fixtures_hits["n"] == 0
    live_by_id = {str(r["fixture_id"]): r for r in body["live"]}
    assert "fx-scored" not in live_by_id
    assert set(live_by_id) == {"fx-live", "fx-early"}
    # Fail-closed without shot stats.
    assert live_by_id["fx-live"]["xg_score"] is None
    assert live_by_id["fx-early"]["xg_score"] is None
    assert body["live30_candidates"][0]["xg_score"] is None
    assert body["scheduled"][0]["fixture_id"] == "fx-sched"
    http.close()
    clear_schedule_cache()


def test_board_xg_helper() -> None:
    from goal_xg.web.app import _board_xg, _xg_tone

    # Clock-only 0-0 → no shot stats → None (fail-closed).
    assert (
        _board_xg(
            fixture_id="1", minute=30, period="1H", score_home=0, score_away=0
        )
        is None
    )
    assert _xg_tone(78) == "high"
    assert (
        _board_xg(
            fixture_id="1", minute=12, period="1H", score_home=0, score_away=0
        )
        is None
    )
    assert (
        _board_xg(
            fixture_id="1", minute=30, period="1H", score_home=1, score_away=0
        )
        == 100
    )
