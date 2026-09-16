"""Phase B unit tests — mocks only (no live network)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from goal_xg.clients.goal_api import GoalApiClient, GoalApiError
from goal_xg.clients.goal_ws import (
    GoalWsClient,
    auth_frame,
    parse_live_clock,
    parse_match_update,
    parse_ws_message,
    subscribe_frame,
)
from goal_xg.features.prematch import PrematchPriors
from goal_xg.live30 import (
    LIVE_WINDOW_MAX,
    LIVE_WINDOW_MIN,
    LiveVolumeStats,
    in_live30_window,
    parse_statistics_payload,
    score_live30,
)
from goal_xg.live30.service import list_live30_candidates, score_fixture_live30
from goal_xg.model.dynamic_weights import (
    LIVE_VOLUME_CAP_PP,
    SHIFT_CAP_PP,
    apply_event_shifts,
    event_shift_deltas,
)
from goal_xg.model.weights import MVP_OMIT_TERMS


def _priors(**kwargs: Any) -> PrematchPriors:
    base = dict(
        fixture_id=1,
        home_team_id=10,
        away_team_id=20,
        home_pct_over05=0.9,
        away_pct_over05=0.85,
        home_fts=0.15,
        away_fts=0.2,
        home_cs=0.3,
        away_cs=0.25,
        home_gf_avg=1.5,
        home_ga_avg=1.0,
        away_gf_avg=1.2,
        away_ga_avg=1.1,
        league_pct_over05=0.92,
        league_pct_00=0.08,
        p_over05_prior=0.88,
        p_00_prior=0.12,
        shrinkage=0.7,
        n_home=10,
        n_away=10,
    )
    base.update(kwargs)
    return PrematchPriors(**base)


def test_in_live30_window_bounds() -> None:
    assert in_live30_window(LIVE_WINDOW_MIN, period="1H")
    assert in_live30_window(LIVE_WINDOW_MAX, period="FIRST_HALF")
    assert not in_live30_window(27, period="1H")
    assert not in_live30_window(30, period="2H")
    assert not in_live30_window(None, period="1H")


def test_parse_match_update_provider_shape() -> None:
    frame = {
        "type": "match_update",
        "data": {
            "id": "fix1",
            "match_id": "750259",
            "match_status": "30",
            "match_hometeam_score": "0",
            "match_awayteam_score": "0",
            "match_hometeam_name": "Home FC",
            "match_awayteam_name": "Away United",
            "league_name": "Serie A",
            "goalscorer": [],
            "cards": [
                {
                    "time": "23",
                    "home_fault": "Defender",
                    "card": "yellow card",
                    "score_info_time": "1st Half",
                }
            ],
            "substitutions": {},
        },
    }
    state = parse_match_update(frame)
    assert state is not None
    assert state.fixture_id == "fix1"
    assert state.minute == 30
    assert state.period == "1H"
    assert state.is_00
    assert state.in_live30_window
    assert state.is_live30_candidate
    assert len(state.cards) == 1

    clock = parse_live_clock(frame)
    assert clock is not None
    assert clock["is_live30_candidate"] is True


def test_parse_match_update_with_nested_clock() -> None:
    frame = {
        "type": "match_update",
        "data": {
            "id": "fx2",
            "clock": {"minute": "45+2", "elapsed": 45, "extra": 2, "period": "FIRST_HALF"},
            "homeScore": 1,
            "awayScore": 0,
        },
    }
    state = parse_match_update(frame)
    assert state is not None
    assert state.minute == 47
    assert state.is_00 is False
    assert state.in_live30_window is False


def test_ws_auth_and_subscribe_frames_fail_closed() -> None:
    with pytest.raises(GoalApiError):
        auth_frame(api_key="")
    with pytest.raises(GoalApiError):
        GoalWsClient(api_key="")
    frame = auth_frame(api_key="test-key-not-real")
    assert frame == {"type": "auth", "apiKey": "test-key-not-real"}
    assert subscribe_frame(99)["matchId"] == "99"
    msg = parse_ws_message(json.dumps({"type": "pong"}))
    assert msg["type"] == "pong"


def test_parse_statistics_first_half() -> None:
    payload = {
        "success": True,
        "data": {
            "match": {
                "firstHalf": [
                    {"type": "Total Shots", "home": "7", "away": "4"},
                    {"type": "Shots on Goal", "home": "2", "away": "1"},
                    {"type": "Expected Goals", "home": "0.55", "away": "0.30"},
                    {"type": "Expected Goals on Target", "home": "0.40", "away": "0.20"},
                    {"type": "Hit Woodwork", "home": "1", "away": "0"},
                    {"type": "Shots off Goal", "home": "3", "away": "2"},
                    {"type": "Blocked Shots", "home": "2", "away": "1"},
                    {"type": "Shots Insidebox", "home": "4", "away": "2"},
                    {"type": "Shots Outsidebox", "home": "3", "away": "2"},
                ]
            }
        },
    }
    stats = parse_statistics_payload(payload)
    assert stats.source_half == "firstHalf"
    assert stats.shots_total == 11
    assert stats.sot_total == 3
    assert stats.shot_xg_total == pytest.approx(0.85)
    assert stats.xgot_total == pytest.approx(0.60)
    assert stats.woodwork_total == 1
    assert stats.shots_off_total == 5
    assert stats.shots_blocked_total == 3
    assert stats.shots_inside_box_total == 6
    assert stats.shots_outside_box_total == 5


def test_score_live30_primary_path() -> None:
    stats = LiveVolumeStats(
        shots_total_home=6,
        shots_total_away=4,
        sot_home=2,
        sot_away=1,
        shot_xg_home=0.5,
        shot_xg_away=0.3,
        xgot_home=0.35,
        xgot_away=0.2,
        woodwork_home=0,
        woodwork_away=0,
        shots_off_home=2,
        shots_off_away=2,
        shots_blocked_home=1,
        shots_blocked_away=1,
        shots_inside_box_home=3,
        shots_inside_box_away=2,
        shots_outside_box_home=3,
        shots_outside_box_away=2,
        source_half="firstHalf",
    )
    result = score_live30(
        fixture_id="fx",
        minute=30,
        period="1H",
        score_home=0,
        score_away=0,
        stats=stats,
        priors=_priors(),
    )
    assert not result.skipped
    assert result.is_00
    assert result.context == "live30"
    assert 0 <= result.xg_score <= 100
    assert result.xg_score == round(100 * result.p_over05_ft)
    assert "residual_time" not in result.weights_used
    assert "team_priors" not in result.weights_used
    assert "corners" not in result.weights_used
    assert "sot" in result.weights_used
    assert "shot_xg" in result.weights_used
    assert abs(sum(result.weights_used.values()) - 100.0) < 1e-6
    assert set(result.signals) <= set(result.weights_used) | set(result.signals)


def test_score_live30_settled_and_skip() -> None:
    settled = score_live30(
        fixture_id="fx",
        minute=30,
        period="1H",
        score_home=1,
        score_away=0,
    )
    assert settled.settled and settled.xg_score == 100

    skipped = score_live30(
        fixture_id="fx",
        minute=20,
        period="1H",
        score_home=0,
        score_away=0,
    )
    assert skipped.skipped and skipped.skip_reason == "outside_live30_window"

    missing = score_live30(
        fixture_id="fx",
        minute=None,
        period="1H",
        score_home=0,
        score_away=0,
    )
    assert missing.skipped and missing.skip_reason == "missing_minute_or_score"


def test_dynamic_weight_caps() -> None:
    deltas = event_shift_deltas(
        sot_total=6, shots_total=12, shot_xg_total=1.1, xgot_total=0.8
    )
    assert all(abs(v) <= SHIFT_CAP_PP for v in deltas.values())
    assert "residual_time" not in deltas
    w = apply_event_shifts(
        deltas=deltas,
        omit=MVP_OMIT_TERMS,
        available={"sot", "shot_xg", "xgot", "shots_total", "shots_inside_box"},
    )
    assert abs(sum(w.values()) - 100.0) < 1e-6
    assert "residual_time" not in w
    capped = apply_event_shifts(
        base={"sot": 30.0, "shots_total": 14.0},
        deltas={"sot": 5.0},
        available={"sot", "shots_total"},
    )
    assert capped["sot"] == pytest.approx(18 / 32 * 100)
    assert LIVE_VOLUME_CAP_PP == 18.0


def test_score_live30_no_stats_fail_closed() -> None:
    result = score_live30(
        fixture_id="fx",
        minute=30,
        period="1H",
        score_home=0,
        score_away=0,
    )
    assert result.skipped
    assert result.skip_reason == "no_usable_shot_stats"
    assert "residual_time" not in result.signals
    assert result.weights_used == {}
    sterile = event_shift_deltas(sot_total=0, shots_total=1, shot_xg_total=0.05)
    assert "residual_time" not in sterile
    assert sterile.get("shots_total", 0) >= 0


def _mock_transport(handlers: dict[str, Any]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        # Prefer longest path suffix match (avoid /fixtures stealing /fixtures/live).
        matches = [
            (key, body)
            for key, body in handlers.items()
            if path.endswith(key) or path.rstrip("/").endswith(key.rstrip("/"))
        ]
        if matches:
            key, body = max(matches, key=lambda kb: len(kb[0]))
            if callable(body):
                return body(request)
            return httpx.Response(200, json=body)
        return httpx.Response(404, json={"success": False, "error": "not found", "code": "NF"})

    transport = httpx.MockTransport(handler)
    return httpx.Client(
        transport=transport,
        base_url="https://api.goal-api.com/v1",
        headers={"Authorization": "Bearer test"},
    )


def test_list_candidates_and_score_fixture_mocked() -> None:
    live_body = {
        "success": True,
        "data": [
            {
                "id": "fx-live",
                "matchElapsed": 30,
                "matchPeriod": "FIRST_HALF",
                "homeScore": 0,
                "awayScore": 0,
                "league": {"id": 135, "name": "Serie A"},
                "teams": {"home": {"id": 1, "name": "A"}, "away": {"id": 2, "name": "B"}},
            },
            {
                "id": "fx-other",
                "matchElapsed": 70,
                "matchPeriod": "SECOND_HALF",
                "homeScore": 0,
                "awayScore": 0,
                "league": {"id": 135, "name": "Serie A"},
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
    fixture_body = {
        "success": True,
        "data": {
            "id": "fx-live",
            "matchElapsed": 30,
            "matchPeriod": "FIRST_HALF",
            "homeScore": 0,
            "awayScore": 0,
            "league": {"id": 135, "name": "Serie A"},
            "teams": {"home": {"id": 1, "name": "A"}, "away": {"id": 2, "name": "B"}},
        },
    }
    stats_body = {
        "success": True,
        "data": {
            "match": {
                "firstHalf": [
                    {"type": "Total Shots", "home": "6", "away": "4"},
                    {"type": "Shots on Goal", "home": "3", "away": "1"},
                    {"type": "Expected Goals", "home": "0.6", "away": "0.3"},
                    {"type": "Expected Goals on Target", "home": "0.4", "away": "0.2"},
                    {"type": "Shots off Goal", "home": "2", "away": "2"},
                    {"type": "Blocked Shots", "home": "1", "away": "1"},
                    {"type": "Shots Insidebox", "home": "3", "away": "2"},
                    {"type": "Shots Outsidebox", "home": "3", "away": "2"},
                ]
            }
        },
    }
    hist_body = {"success": True, "data": []}

    http = _mock_transport(
        {
            "/leagues": leagues_body,
            "/fixtures/live": live_body,
            "/fixtures/fx-live/statistics": stats_body,
            "/fixtures/fx-live/cards": {"success": True, "data": []},
            "/fixtures/fx-live/substitutions": {"success": True, "data": {}},
            "/fixtures/fx-live/lineups": {"success": True, "data": {}},
            "/fixtures/fx-live": fixture_body,
            "/fixtures": hist_body,
        }
    )
    client = GoalApiClient(api_key="test-key-not-real", client=http, cache_dir="/tmp/goal-xg-test-cache")
    try:
        cands = list_live30_candidates(client, big5_only=True, require_00=True, in_window_only=True)
        assert len(cands) == 1
        assert cands[0]["fixture_id"] == "fx-live"

        scored = score_fixture_live30(client, "fx-live", fetch_history=False)
        assert not scored.skipped
        assert scored.xg_score == round(100 * scored.p_over05_ft)
        assert "sot" in scored.weights_used
        assert "residual_time" not in scored.weights_used
        assert "team_priors" not in scored.weights_used
    finally:
        client.close()
        http.close()


def test_goal_api_fail_closed_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOAL_API_KEY", raising=False)
    with pytest.raises(GoalApiError):
        GoalApiClient(api_key=None)
