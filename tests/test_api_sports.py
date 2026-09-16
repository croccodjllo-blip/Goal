"""Tests for API-Sports client — mocks only; no real API key."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from goal_xg.clients.api_sports import (
    BIG5_LEAGUE_IDS,
    ApiSportsClient,
    ApiSportsError,
    api_sports_key_configured,
    maybe_client,
)
from goal_xg.live30.stats import LiveVolumeStats, merge_fill_shot_stats


def _response(
    status: int,
    json_body: Any = None,
    *,
    headers: dict[str, str] | None = None,
    text: str = "",
) -> httpx.Response:
    req = httpx.Request("GET", "https://v3.football.api-sports.io/test")
    content = b""
    hdrs = dict(headers or {})
    if json_body is not None:
        import json

        content = json.dumps(json_body).encode()
        hdrs.setdefault("content-type", "application/json")
    elif text:
        content = text.encode()
    return httpx.Response(status, content=content, headers=hdrs, request=req)


@pytest.fixture
def mock_http() -> MagicMock:
    return MagicMock(spec=httpx.Client)


def test_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("API_SPORTS_KEY", raising=False)
    monkeypatch.delenv("APISPORTS_KEY", raising=False)
    with pytest.raises(ApiSportsError, match="API_SPORTS_KEY"):
        ApiSportsClient(api_key="")


def test_apisports_key_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("API_SPORTS_KEY", raising=False)
    monkeypatch.setenv("APISPORTS_KEY", "alias-key")
    assert api_sports_key_configured() is True
    client = ApiSportsClient(client=MagicMock(spec=httpx.Client), min_interval_s=0)
    assert client is not None
    client.close()


def test_maybe_client_none_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("API_SPORTS_KEY", raising=False)
    monkeypatch.delenv("APISPORTS_KEY", raising=False)
    assert maybe_client() is None


def test_status_unwraps_response(mock_http: MagicMock) -> None:
    mock_http.request.return_value = _response(
        200,
        {
            "errors": [],
            "response": {
                "account": {"firstname": "A"},
                "subscription": {"plan": "Free", "active": True},
                "requests": {"current": 2, "limit_day": 100},
            },
        },
        headers={"x-ratelimit-requests-remaining": "98"},
    )
    client = ApiSportsClient(
        api_key="test-key-not-real",
        client=mock_http,
        min_interval_s=0,
        sleep_fn=lambda _s: None,
    )
    status = client.status()
    assert status["subscription"]["plan"] == "Free"
    assert status["requests"]["limit_day"] == 100
    assert client.last_rate_limit is not None
    assert client.last_rate_limit.remaining == 98
    args, _kwargs = mock_http.request.call_args
    assert args[0] == "GET"
    assert args[1] == "/status"


def test_plan_error_raises(mock_http: MagicMock) -> None:
    mock_http.request.return_value = _response(
        200,
        {"errors": {"plan": "Free plans do not have access"}, "response": []},
    )
    client = ApiSportsClient(
        api_key="test-key-not-real",
        client=mock_http,
        min_interval_s=0,
        sleep_fn=lambda _s: None,
    )
    with pytest.raises(ApiSportsError, match="Free plans"):
        client.fixtures(league=39, season=2026, date="2026-09-16")


def test_parse_statistics_half_true() -> None:
    payload = [
        {
            "team": {"id": 529, "name": "Barcelona"},
            "statistics": [
                {"type": "Total Shots", "value": 18},
                {"type": "Shots on Goal", "value": 6},
            ],
            "statistics_1h": [
                {"type": "Total Shots", "value": 10},
                {"type": "Shots on Goal", "value": 4},
                {"type": "Shots off Goal", "value": 3},
                {"type": "Blocked Shots", "value": 2},
                {"type": "Shots insidebox", "value": 7},
                {"type": "Shots outsidebox", "value": 3},
            ],
            "statistics_2h": [],
        },
        {
            "team": {"id": 731, "name": "Racing Santander"},
            "statistics": [
                {"type": "Total Shots", "value": 1},
                {"type": "Shots on Goal", "value": 1},
            ],
            "statistics_1h": [
                {"type": "Total Shots", "value": 1},
                {"type": "Shots on Goal", "value": 1},
                {"type": "Shots off Goal", "value": 0},
                {"type": "Blocked Shots", "value": 0},
                {"type": "Shots insidebox", "value": 1},
                {"type": "Shots outsidebox", "value": 0},
            ],
            "statistics_2h": [],
        },
    ]
    stats = ApiSportsClient.parse_statistics_payload(
        payload,
        home_team_name="Barcelona",
        away_team_name="Racing Santander",
        prefer_half=True,
    )
    assert stats.shots_total_home == 10
    assert stats.sot_home == 4
    assert stats.shots_off_home == 3
    assert stats.shots_blocked_home == 2
    assert stats.shots_inside_box_home == 7
    assert stats.shots_outside_box_home == 3
    assert stats.shots_total_away == 1
    assert stats.sot_away == 1
    assert stats.shot_xg_home is None  # not in free payload → omit
    assert stats.xgot_home is None
    assert stats.woodwork_home is None
    assert stats.source_half == "api_sports_1h"
    assert "enriched_api_sports" in stats.notes


def test_merge_fill_never_overwrites() -> None:
    primary = LiveVolumeStats(
        shots_total_home=5,
        sot_home=None,
        shot_xg_home=0.4,
        source_half="firstHalf",
    )
    enrich = LiveVolumeStats(
        shots_total_home=99,
        sot_home=3,
        shot_xg_home=9.9,
        shots_blocked_home=2,
        source_half="api_sports_1h",
        notes=("enriched_api_sports",),
    )
    merged = ApiSportsClient.merge_fill_shot_stats(primary, enrich)
    assert merged.shots_total_home == 5  # GOAL wins
    assert merged.sot_home == 3  # filled
    assert merged.shot_xg_home == 0.4  # GOAL wins
    assert merged.shots_blocked_home == 2
    assert "filled_from_api_sports" in merged.notes


def test_merge_fill_shot_stats_helper() -> None:
    primary = LiveVolumeStats(sot_home=None, shots_total_home=2)
    enrich = LiveVolumeStats(sot_home=1, shots_total_home=9)
    merged = merge_fill_shot_stats(primary, enrich)
    assert merged.sot_home == 1
    assert merged.shots_total_home == 2


def test_find_fixture_id_from_live_rows(mock_http: MagicMock) -> None:
    client = ApiSportsClient(
        api_key="test-key-not-real",
        client=mock_http,
        min_interval_s=0,
        sleep_fn=lambda _s: None,
    )
    rows = [
        {
            "fixture": {"id": 1570385},
            "teams": {
                "home": {"id": 529, "name": "Barcelona"},
                "away": {"id": 731, "name": "Racing Santander"},
            },
            "league": {"id": 140},
        }
    ]
    fid = client.find_fixture_id(
        home_name="FC Barcelona",
        away_name="Racing Santander",
        live_rows=rows,
    )
    assert fid == 1570385
    assert mock_http.request.call_count == 0  # used live_rows


def test_find_fixture_id_from_api_id(mock_http: MagicMock) -> None:
    client = ApiSportsClient(
        api_key="test-key-not-real",
        client=mock_http,
        min_interval_s=0,
        sleep_fn=lambda _s: None,
    )
    assert client.find_fixture_id(home_name=None, away_name=None, api_id="1570385") == 1570385


def test_enrich_live_volume_fills_gaps(mock_http: MagicMock) -> None:
    mock_http.request.return_value = _response(
        200,
        {
            "errors": [],
            "response": [
                {
                    "team": {"id": 1, "name": "Home FC"},
                    "statistics_1h": [
                        {"type": "Total Shots", "value": 8},
                        {"type": "Shots on Goal", "value": 3},
                        {"type": "Blocked Shots", "value": 1},
                    ],
                    "statistics": [],
                },
                {
                    "team": {"id": 2, "name": "Away FC"},
                    "statistics_1h": [
                        {"type": "Total Shots", "value": 4},
                        {"type": "Shots on Goal", "value": 2},
                        {"type": "Blocked Shots", "value": 0},
                    ],
                    "statistics": [],
                },
            ],
        },
    )
    client = ApiSportsClient(
        api_key="test-key-not-real",
        client=mock_http,
        min_interval_s=0,
        sleep_fn=lambda _s: None,
    )
    primary = LiveVolumeStats(shots_total_home=7, sot_home=None)
    merged = client.enrich_live_volume(
        primary,
        fixture_id=1001,
        home_name="Home FC",
        away_name="Away FC",
    )
    assert merged.shots_total_home == 7
    assert merged.sot_home == 3
    assert merged.shots_blocked_home == 1
    assert merged.sot_away == 2
    args, kwargs = mock_http.request.call_args
    assert args[1] == "/fixtures/statistics"
    assert kwargs["params"]["fixture"] == 1001
    assert kwargs["params"]["half"] == "true"


def test_flatten_fixture_statistics_all_types() -> None:
    from goal_xg.clients.api_sports import flatten_fixture_statistics, label_stat_type

    payload = [
        {
            "team": {"id": 1, "name": "Home FC"},
            "statistics": [
                {"type": "Ball Possession", "value": "60%"},
                {"type": "Total Shots", "value": 12},
                {"type": "Corner Kicks", "value": 5},
                {"type": "Goalkeeper Saves", "value": 2},
                {"type": "Fouls", "value": 8},
                {"type": "Yellow Cards", "value": 1},
                {"type": "Passes %", "value": "84%"},
            ],
            "statistics_1h": [
                {"type": "Total Shots", "value": 7},
                {"type": "Ball Possession", "value": "58%"},
            ],
        },
        {
            "team": {"id": 2, "name": "Away FC"},
            "statistics": [
                {"type": "Ball Possession", "value": "40%"},
                {"type": "Total Shots", "value": 4},
                {"type": "Corner Kicks", "value": 2},
                {"type": "Goalkeeper Saves", "value": 4},
                {"type": "Fouls", "value": 11},
                {"type": "Yellow Cards", "value": 2},
                {"type": "Passes %", "value": "72%"},
            ],
            "statistics_1h": [
                {"type": "Total Shots", "value": 2},
                {"type": "Ball Possession", "value": "42%"},
            ],
        },
    ]
    rows = flatten_fixture_statistics(
        payload, home_team_name="Home FC", away_team_name="Away FC"
    )
    by_type = {r.type: r for r in rows}
    assert "Ball Possession" in by_type
    assert "Corner Kicks" in by_type
    assert "Goalkeeper Saves" in by_type
    assert by_type["Total Shots"].in_index is True
    assert by_type["Ball Possession"].in_index is False
    assert by_type["Total Shots"].home == 12
    assert by_type["Total Shots"].home_1h == 7
    assert by_type["Corner Kicks"].label == "Calci d'angolo"
    assert label_stat_type("Unknown Stat XYZ") == "Unknown Stat XYZ"


def test_load_fixture_stat_dump(mock_http: MagicMock) -> None:
    mock_http.request.side_effect = [
        _response(
            200,
            {
                "errors": [],
                "response": [
                    {
                        "team": {"id": 1, "name": "Home FC"},
                        "statistics": [
                            {"type": "Total Shots", "value": 5},
                            {"type": "Offsides", "value": 2},
                        ],
                        "statistics_1h": [{"type": "Total Shots", "value": 3}],
                    },
                    {
                        "team": {"id": 2, "name": "Away FC"},
                        "statistics": [
                            {"type": "Total Shots", "value": 2},
                            {"type": "Offsides", "value": 1},
                        ],
                        "statistics_1h": [{"type": "Total Shots", "value": 1}],
                    },
                ],
            },
        ),
        _response(
            200,
            {
                "errors": [],
                "response": [
                    {
                        "time": {"elapsed": 12, "extra": None},
                        "team": {"name": "Home FC"},
                        "player": {"name": "Rossi"},
                        "type": "Card",
                        "detail": "Yellow Card",
                    }
                ],
            },
        ),
    ]
    client = ApiSportsClient(
        api_key="test-key-not-real",
        client=mock_http,
        min_interval_s=0,
        sleep_fn=lambda _s: None,
    )
    dump = client.load_fixture_stat_dump(
        home_name="Home FC",
        away_name="Away FC",
        fixture_id=4242,
        include_events=True,
    )
    assert dump["meta"]["fixture_id"] == 4242
    assert "Offsides" in dump["meta"]["stat_types"]
    assert dump["meta"]["events_n"] == 1
    assert len(dump["rows"]) == 2
    assert dump["events"][0]["detail"] == "Yellow Card"



def test_429_retries(mock_http: MagicMock) -> None:
    sleeps: list[float] = []
    mock_http.request.side_effect = [
        _response(429, text="slow down", headers={"x-ratelimit-requests-reset": "1"}),
        _response(
            200,
            {
                "errors": [],
                "response": {"account": {}, "subscription": {}, "requests": {}},
            },
        ),
    ]
    client = ApiSportsClient(
        api_key="test-key-not-real",
        client=mock_http,
        min_interval_s=0,
        max_retries=2,
        sleep_fn=lambda s: sleeps.append(s),
    )
    assert client.status()["requests"] == {}
    assert mock_http.request.call_count == 2
    assert sleeps
