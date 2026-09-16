"""Tests for football-data.org client — mocks only; no real API key."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from goal_xg.clients.football_data import (
    BIG5_COMPETITION_CODES,
    CoachIdentity,
    FootballDataClient,
    FootballDataError,
)
from goal_xg.features.coach_identity import (
    MatchCoachIdentities,
    identity_signal_pair,
    resolve_match_coaches,
)
from goal_xg.model.weights import (
    BASE_WEIGHTS,
    COACH_IDENTITY_FEATURE,
    COACH_H2H_TERM,
    MVP_OMIT_TERMS,
    mvp_renorm,
)


def _response(
    status: int,
    json_body: Any = None,
    *,
    headers: dict[str, str] | None = None,
    text: str = "",
) -> httpx.Response:
    req = httpx.Request("GET", "https://api.football-data.org/v4/test")
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
    monkeypatch.delenv("FOOTBALL_DATA_API_KEY", raising=False)
    with pytest.raises(FootballDataError, match="FOOTBALL_DATA_API_KEY"):
        FootballDataClient(api_key="")


def test_auth_header_set(mock_http: MagicMock) -> None:
    mock_http.request.return_value = _response(200, {"id": 1, "name": "Test"})
    client = FootballDataClient(
        api_key="test-token-not-real",
        client=mock_http,
        min_interval_s=0,
        sleep_fn=lambda _s: None,
    )
    # Injected client already has headers from constructor path — we pass client
    # so we only check request path works.
    assert client.team(86)["id"] == 1
    mock_http.request.assert_called()
    args, kwargs = mock_http.request.call_args
    assert args[0] == "GET"
    assert args[1] == "/teams/86"


def test_parse_coach_from_team_filled() -> None:
    team = {
        "id": 86,
        "name": "Real Madrid CF",
        "coach": {
            "id": 44,
            "name": "Carlo Ancelotti",
            "nationality": "Italy",
            "dateOfBirth": "1959-06-10",
        },
    }
    ident = FootballDataClient.parse_coach_from_team(team, competition_code="PD")
    assert ident is not None
    assert ident.is_complete
    assert ident.person_id == 44
    assert ident.name == "Carlo Ancelotti"
    assert ident.team_id == 86
    assert ident.competition_code == "PD"


def test_parse_coach_from_team_null() -> None:
    assert FootballDataClient.parse_coach_from_team({"id": 57, "name": "Arsenal", "coach": None}) is None
    assert FootballDataClient.parse_coach_from_team({"id": 57, "name": "Arsenal"}) is None
    assert FootballDataClient.parse_coach_from_team({"id": 57, "coach": {}}) is None


def test_parse_coach_from_person() -> None:
    person = {
        "id": 44,
        "name": "Carlo Ancelotti",
        "section": "Coach",
        "nationality": "Italy",
        "currentTeam": {"id": 86, "name": "Real Madrid CF"},
    }
    ident = FootballDataClient.parse_coach_from_person(person)
    assert ident is not None
    assert ident.section == "Coach"
    assert ident.team_id == 86
    assert ident.team_name == "Real Madrid CF"


def test_parse_person_non_coach_fail_closed() -> None:
    assert (
        FootballDataClient.parse_coach_from_person(
            {"id": 1, "name": "Player X", "section": "Attack"}
        )
        is None
    )


def test_429_retries_then_ok(mock_http: MagicMock) -> None:
    sleeps: list[float] = []
    mock_http.request.side_effect = [
        _response(429, text="too many", headers={"Retry-After": "1"}),
        _response(200, {"id": 86, "name": "Real Madrid CF", "coach": None}),
    ]
    client = FootballDataClient(
        api_key="test-token-not-real",
        client=mock_http,
        min_interval_s=0,
        max_retries=2,
        sleep_fn=lambda s: sleeps.append(s),
    )
    payload = client.team(86)
    assert payload["id"] == 86
    assert sleeps == [1.0]  # Retry-After from the 429 response
    assert mock_http.request.call_count == 2
    assert client.last_rate_limit is not None  # from successful final response


def test_429_exhausted_raises(mock_http: MagicMock) -> None:
    mock_http.request.return_value = _response(429, text="nope", headers={"Retry-After": "1"})
    client = FootballDataClient(
        api_key="test-token-not-real",
        client=mock_http,
        min_interval_s=0,
        max_retries=1,
        sleep_fn=lambda _s: None,
    )
    with pytest.raises(FootballDataError, match="429"):
        client.team(1)


def test_coaches_for_competition_skips_null(mock_http: MagicMock) -> None:
    mock_http.request.return_value = _response(
        200,
        {
            "teams": [
                {
                    "id": 1,
                    "name": "A",
                    "coach": {"id": 10, "name": "Coach A", "nationality": "ES"},
                },
                {"id": 2, "name": "B", "coach": None},
            ]
        },
    )
    client = FootballDataClient(
        api_key="test-token-not-real",
        client=mock_http,
        min_interval_s=0,
        sleep_fn=lambda _s: None,
    )
    coaches = client.coaches_for_competition("PD")
    assert len(coaches) == 1
    assert coaches[0].name == "Coach A"
    assert coaches[0].competition_code == "PD"


def test_big5_codes_locked() -> None:
    assert BIG5_COMPETITION_CODES == ("PL", "PD", "SA", "BL1", "FL1")


def test_coach_h2h_not_in_shot_index() -> None:
    assert COACH_H2H_TERM not in BASE_WEIGHTS
    assert COACH_IDENTITY_FEATURE not in BASE_WEIGHTS
    assert COACH_IDENTITY_FEATURE not in MVP_OMIT_TERMS
    w = mvp_renorm()
    assert "coach_h2h" not in w
    assert COACH_IDENTITY_FEATURE not in w
    assert set(w) == set(BASE_WEIGHTS)


def test_identity_signal_pair_does_not_inject_h2h() -> None:
    pair = MatchCoachIdentities(
        home=CoachIdentity(person_id=1, name="A", team_id=10),
        away=CoachIdentity(person_id=2, name="B", team_id=20),
        h2h_available=False,
    )
    assert identity_signal_pair(pair) == {}
    assert pair.as_dict()["h2h_available"] is False


def test_resolve_match_coaches(mock_http: MagicMock) -> None:
    def _side_effect(method: str, path: str, **kwargs: Any) -> httpx.Response:
        if path.endswith("/teams/86"):
            return _response(
                200,
                {
                    "id": 86,
                    "name": "Real Madrid CF",
                    "coach": {"id": 44, "name": "Carlo Ancelotti"},
                },
            )
        if path.endswith("/teams/81"):
            return _response(
                200,
                {
                    "id": 81,
                    "name": "FC Barcelona",
                    "coach": {"id": 99, "name": "Hansi Flick"},
                },
            )
        return _response(404, text="missing")

    mock_http.request.side_effect = _side_effect
    client = FootballDataClient(
        api_key="test-token-not-real",
        client=mock_http,
        min_interval_s=0,
        sleep_fn=lambda _s: None,
    )
    pair = resolve_match_coaches(client, home_team_id=86, away_team_id=81)
    assert pair.home is not None and pair.home.name == "Carlo Ancelotti"
    assert pair.away is not None and pair.away.name == "Hansi Flick"
    assert pair.h2h_available is False
