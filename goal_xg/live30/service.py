"""Phase B orchestration: list live Big-5 candidates + score with budgeted REST.

WS-first for clock/score; REST statistics / lineups only when a fixture is
0-0 inside the 28–32′ window (GOAL ~1000 req/day).
"""

from __future__ import annotations

from typing import Any, Mapping

from goal_xg.clients.goal_api import GoalApiClient, _as_league_id
from goal_xg.clients.goal_ws import LiveMatchState, parse_match_update
from goal_xg.features.prematch import PrematchPriors, compute_prematch_priors
from goal_xg.live30.score import Live30Score, score_live30
from goal_xg.live30.stats import merge_events_into_stats, parse_statistics_payload
from goal_xg.live30.window import (
    in_live30_window,
    is_score_00,
    normalize_period,
    parse_minute,
)


def _unwrap_list(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("data", "response", "fixtures", "results"):
            val = payload.get(key)
            if isinstance(val, list):
                return [r for r in val if isinstance(r, dict)]
        return [payload]
    return []


def _unwrap_obj(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        for key in ("data", "response", "fixture"):
            val = payload.get(key)
            if isinstance(val, dict):
                return val
            if isinstance(val, list) and val and isinstance(val[0], dict):
                return val[0]
        return payload
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return payload[0]
    return {}


def live_row_to_state(row: Mapping[str, Any]) -> LiveMatchState | None:
    """Normalize a REST ``/fixtures/live`` row or WS provider payload."""
    # Try provider / WS shape first.
    state = parse_match_update(dict(row))
    if state is not None and (
        state.minute is not None or state.home_score is not None
    ):
        return state

    # REST fixture shape.
    clock = row.get("clock") if isinstance(row.get("clock"), dict) else {}
    minute = parse_minute(
        clock.get("minute")
        or row.get("matchElapsed")
        or row.get("matchMinute")
        or row.get("minute")
    )
    period = normalize_period(
        str(clock.get("period") or row.get("matchPeriod") or row.get("period") or "")
        or None
    )
    if period == "OTHER" and minute is not None and minute <= 45:
        period = "1H"
    goals = row.get("goals") if isinstance(row.get("goals"), dict) else {}
    score = row.get("score") if isinstance(row.get("score"), dict) else {}
    home = None
    for key in (
        "homeTeamScore",
        "homeScore",
        "home_score",
        "score_home",
        "match_hometeam_score",
    ):
        if key in row and row[key] is not None and row[key] != "":
            home = row[key]
            break
    if home is None and goals:
        home = goals.get("home")
    if home is None and isinstance(score.get("fulltime"), dict):
        home = score["fulltime"].get("home")
    away = None
    for key in (
        "awayTeamScore",
        "awayScore",
        "away_score",
        "score_away",
        "match_awayteam_score",
    ):
        if key in row and row[key] is not None and row[key] != "":
            away = row[key]
            break
    if away is None and goals:
        away = goals.get("away")
    if away is None and isinstance(score.get("fulltime"), dict):
        away = score["fulltime"].get("away")
    try:
        home_i = int(home) if home is not None else None
    except (TypeError, ValueError):
        home_i = None
    try:
        away_i = int(away) if away is not None else None
    except (TypeError, ValueError):
        away_i = None

    fid = row.get("id") or row.get("fixtureId")
    teams = row.get("teams") if isinstance(row.get("teams"), dict) else {}
    home_t = teams.get("home") if isinstance(teams.get("home"), dict) else {}
    away_t = teams.get("away") if isinstance(teams.get("away"), dict) else {}
    league = row.get("league") if isinstance(row.get("league"), dict) else {}

    return LiveMatchState(
        fixture_id=str(fid) if fid is not None else None,
        provider_match_id=str(row["apiId"]) if row.get("apiId") is not None else None,
        minute=minute,
        elapsed=minute,
        extra=None,
        period=period,
        home_score=home_i,
        away_score=away_i,
        status_raw=str(row.get("matchStatus") or row.get("status") or "") or None,
        home_name=home_t.get("name") or row.get("homeTeamName"),
        away_name=away_t.get("name") or row.get("awayTeamName"),
        league_name=league.get("name") or row.get("leagueName") or row.get("league_name"),
        country_name=row.get("countryName") or row.get("country_name"),
        raw=dict(row),
    )


def list_live30_candidates(
    client: GoalApiClient,
    *,
    big5_only: bool = True,
    require_00: bool = True,
    in_window_only: bool = True,
) -> list[dict[str, Any]]:
    """One REST ``/fixtures/live`` call; filter Big-5 / 0-0 / 28–32′.

    Does **not** fetch statistics (budget). Use :func:`score_fixture_live30`
    per candidate when ready to densify.
    """
    big5_ids: set[str] = set()
    if big5_only:
        big5_ids = {lg.league_id for lg in client.discover_big5_leagues()}

    payload = client.fixtures_live()
    rows = _unwrap_list(payload)
    out: list[dict[str, Any]] = []
    for row in rows:
        if big5_only and big5_ids:
            league = row.get("league") if isinstance(row.get("league"), dict) else {}
            lid = _as_league_id(
                league.get("id") or row.get("leagueId") or row.get("league_id")
            )
            if lid is None or lid not in big5_ids:
                continue
        state = live_row_to_state(row)
        if state is None or state.fixture_id is None:
            continue
        if require_00 and not state.is_00:
            continue
        if in_window_only and not state.in_live30_window:
            continue
        out.append(
            {
                "fixture_id": state.fixture_id,
                "minute": state.minute,
                "period": state.period,
                "score_home": state.home_score,
                "score_away": state.away_score,
                "is_00": state.is_00,
                "in_window": state.in_live30_window,
                "is_live30_candidate": state.is_live30_candidate,
                "home_name": state.home_name,
                "away_name": state.away_name,
                "league_name": state.league_name,
            }
        )
    return out


def _extract_team_ids(fixture_row: Mapping[str, Any]) -> tuple[str | None, str | None]:
    teams = fixture_row.get("teams") if isinstance(fixture_row.get("teams"), dict) else {}
    home = teams.get("home") if isinstance(teams.get("home"), dict) else {}
    away = teams.get("away") if isinstance(teams.get("away"), dict) else {}
    hid = home.get("id") or fixture_row.get("homeTeamId") or fixture_row.get("home_team_id")
    aid = away.get("id") or fixture_row.get("awayTeamId") or fixture_row.get("away_team_id")
    return (
        str(hid).strip() if hid is not None and str(hid).strip() else None,
        str(aid).strip() if aid is not None and str(aid).strip() else None,
    )


def score_fixture_live30(
    client: GoalApiClient,
    fixture_id: int | str,
    *,
    clock_override: Mapping[str, Any] | LiveMatchState | None = None,
    fetch_history: bool = True,
    season: int | None = None,
    fetch_lineups: bool = True,
    weather_signal: float | None = None,
) -> Live30Score:
    """Score one fixture. REST stats only if gate says 0-0 in window (or settled).

    Clock source preference:
    1. ``clock_override`` (from WS ``match_update`` — preferred, no REST)
    2. ``GET /fixtures/:id`` (1 REST)
    """
    state: LiveMatchState | None = None
    if isinstance(clock_override, LiveMatchState):
        state = clock_override
    elif isinstance(clock_override, Mapping):
        state = parse_match_update(dict(clock_override)) or live_row_to_state(clock_override)

    detail = client.fixture_by_id(fixture_id)
    fixture_row = _unwrap_obj(detail)
    if state is None:
        state = live_row_to_state(fixture_row)

    if state is None:
        return score_live30(
            fixture_id=fixture_id,
            minute=None,
            period=None,
            score_home=None,
            score_away=None,
        )

    minute = state.minute
    period = state.period
    home = state.home_score
    away = state.away_score

    # Outside window and not needing settled path → skip without stats REST.
    if not in_live30_window(minute, period=period):
        return score_live30(
            fixture_id=fixture_id,
            minute=minute,
            period=period,
            score_home=home,
            score_away=away,
        )

    # Settled inside window → no stats needed.
    if not is_score_00(home, away):
        return score_live30(
            fixture_id=fixture_id,
            minute=minute,
            period=period,
            score_home=home,
            score_away=away,
        )

    # Densify: statistics (prefer firstHalf) + optional cards/subs/lineups.
    stats_payload = client.fixture_statistics(fixture_id, half="1half")
    stats = parse_statistics_payload(stats_payload)

    cards: list[Any] | None = None
    subs: Any = None
    lineups: dict[str, Any] | None = None
    # Prefer events already on WS state.
    if state.cards:
        cards = list(state.cards)
    else:
        try:
            cards_payload = client.fixture_cards(fixture_id)
            cards = _unwrap_list(cards_payload)
        except Exception:
            cards = None
    if state.substitutions is not None:
        subs = state.substitutions
    else:
        try:
            subs = _unwrap_obj(client.fixture_substitutions(fixture_id)) or client.fixture_substitutions(
                fixture_id
            )
        except Exception:
            subs = None
    if fetch_lineups:
        try:
            lineups = _unwrap_obj(client.fixture_lineups(fixture_id))
        except Exception:
            lineups = None

    stats = merge_events_into_stats(
        stats,
        cards=cards,
        substitutions=subs,
        lineups=lineups,
        goalscorer=list(state.goals) if state.goals else None,
    )

    priors: PrematchPriors | None = None
    if fetch_history:
        hid, aid = _extract_team_ids(fixture_row)
        league = fixture_row.get("league") if isinstance(fixture_row.get("league"), dict) else {}
        league_id = league.get("id") or fixture_row.get("leagueId")
        if hid is not None and aid is not None and league_id is not None:
            try:
                hist = client.fixtures_by_league(league_id, season=season, status="FT")
                priors = compute_prematch_priors(
                    home_team_id=hid,
                    away_team_id=aid,
                    finished=_unwrap_list(hist),
                    fixture_id=fixture_id,
                )
            except Exception:
                priors = None

    return score_live30(
        fixture_id=fixture_id,
        minute=minute,
        period=period,
        score_home=home,
        score_away=away,
        stats=stats,
        priors=priors,
        weather_signal=weather_signal,
    )


def live30_score_to_dict(result: Live30Score) -> dict[str, Any]:
    return {
        "fixture_id": result.fixture_id,
        "context": result.context,
        "minute": result.minute,
        "period": result.period,
        "score_home": result.score_home,
        "score_away": result.score_away,
        "is_00": result.is_00,
        "in_window": result.in_window,
        "settled": result.settled,
        "skipped": result.skipped,
        "skip_reason": result.skip_reason,
        "p_over05_ft": result.p_over05_ft,
        "p_00_ft": result.p_00_ft,
        "xg_score": result.xg_score,
        "weights_used": dict(result.weights_used),
        "signals": dict(result.signals),
        "features": dict(result.features),
        "notes": list(result.notes),
    }
