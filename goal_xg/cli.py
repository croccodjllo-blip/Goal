"""CLI: leagues, fixtures, score-prematch, coach, watch-live, score-live30, daily-refresh."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

import typer
from dotenv import load_dotenv

from goal_xg.clients.football_data import FootballDataClient, FootballDataError
from goal_xg.clients.goal_api import GoalApiClient, GoalApiError, _as_league_id
from goal_xg.clients.goal_ws import GoalWsClient
from goal_xg.features.extractors import build_extra_signals
from goal_xg.features.prematch import FinishedFixture, compute_prematch_priors
from goal_xg.jobs.daily_refresh import run_daily_refresh
from goal_xg.jobs.store import DailyStore, default_daily_cache_dir
from goal_xg.live30.service import (
    list_live30_candidates,
    live30_score_to_dict,
    live_row_to_state,
    score_fixture_live30,
)
from goal_xg.model.over05 import score_prematch

app = typer.Typer(
    name="goal-xg",
    help="Over 0.5 FT product-xG (Big-5) — GOAL REST/WS + fd.org coaches",
    add_completion=False,
)


def _load_env() -> None:
    # Local .env only; never commit secrets.
    load_dotenv(Path.cwd() / ".env", override=False)


def _client() -> GoalApiClient:
    _load_env()
    return GoalApiClient()


def _fd_client() -> FootballDataClient:
    _load_env()
    return FootballDataClient()


def _dump(obj: Any) -> None:
    typer.echo(json.dumps(obj, indent=2, default=str))


def _extract_fixture_teams_scores(payload: Any) -> tuple[int | str, int | str, list[FinishedFixture]]:
    """Best-effort parse of a fixture detail + related finished list if present."""
    data = payload
    if isinstance(payload, dict):
        for key in ("data", "response", "fixture", "fixtures"):
            if key in payload:
                data = payload[key]
                break

    fixture = data[0] if isinstance(data, list) and data else data
    if not isinstance(fixture, dict):
        raise GoalApiError("Unexpected fixture payload shape")

    teams = fixture.get("teams") if isinstance(fixture.get("teams"), dict) else {}
    home = teams.get("home") if isinstance(teams.get("home"), dict) else {}
    away = teams.get("away") if isinstance(teams.get("away"), dict) else {}
    hid = home.get("id") or fixture.get("homeTeamId") or fixture.get("home_team_id")
    aid = away.get("id") or fixture.get("awayTeamId") or fixture.get("away_team_id")
    if hid is None or aid is None:
        raise GoalApiError("Could not resolve home/away team ids from fixture")

    return str(hid), str(aid), []


def _normalize_fixtures_list(payload: Any) -> list[dict[str, Any]]:
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


@app.command("leagues")
def leagues_cmd(
    refresh: bool = typer.Option(False, "--refresh", help="Bypass disk cache"),
) -> None:
    """Discover and cache Big-5 league IDs from GOAL API."""
    try:
        with _client() as client:
            leagues = client.discover_big5_leagues(force_refresh=refresh)
            rl = client.last_rate_limit
            _dump(
                {
                    "big5": [
                        {
                            "code": lg.code,
                            "league_id": lg.league_id,
                            "name": lg.name,
                            "country": lg.country,
                            "season": lg.season,
                        }
                        for lg in leagues
                    ],
                    "rate_limit": None
                    if rl is None
                    else {
                        "limit": rl.limit,
                        "remaining": rl.remaining,
                        "type": rl.limit_type,
                    },
                    "cache": str(client._league_cache_path()),
                }
            )
    except GoalApiError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


@app.command("fixtures")
def fixtures_cmd(
    date: str = typer.Option(..., "--date", help="Kickoff date YYYY-MM-DD"),
    big5_only: bool = typer.Option(True, "--big5-only/--all", help="Filter to Big-5"),
) -> None:
    """List fixtures for a date (Big-5 filter by default)."""
    try:
        with _client() as client:
            big5_ids: set[int] = set()
            if big5_only:
                big5_ids = {lg.league_id for lg in client.discover_big5_leagues()}
            payload = client.fixtures_by_date(date)
            rows = _normalize_fixtures_list(payload)

            def _league_id(row: dict[str, Any]) -> str | None:
                league = row.get("league")
                if isinstance(league, dict) and league.get("id") is not None:
                    return _as_league_id(league["id"])
                return _as_league_id(
                    row.get("league_id")
                    if row.get("league_id") is not None
                    else row.get("leagueId")
                )

            if big5_only and big5_ids:
                rows = [r for r in rows if _league_id(r) in big5_ids]

            _dump(
                {
                    "date": date,
                    "count": len(rows),
                    "fixtures": rows,
                    "rate_limit": None
                    if client.last_rate_limit is None
                    else {
                        "limit": client.last_rate_limit.limit,
                        "remaining": client.last_rate_limit.remaining,
                        "type": client.last_rate_limit.limit_type,
                    },
                }
            )
    except GoalApiError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


@app.command("score-prematch")
def score_prematch_cmd(
    fixture_id: str = typer.Option(..., "--fixture-id", help="GOAL fixture id"),
    season: Optional[int] = typer.Option(
        None, "--season", help="Season year for history fetch (e.g. 2025)"
    ),
    shrink_k: float = typer.Option(8.0, "--shrink-k", help="Shrinkage pseudo-counts"),
) -> None:
    """Compute Phase A pre-match p_over05 + product xG for a fixture."""
    try:
        with _client() as client:
            detail = client.fixture_by_id(fixture_id)
            home_id, away_id, _ = _extract_fixture_teams_scores(detail)

            fixture_row = detail
            if isinstance(detail, dict):
                for key in ("data", "response", "fixture"):
                    val = detail.get(key)
                    if isinstance(val, dict):
                        fixture_row = val
                        break
                    if isinstance(val, list) and val and isinstance(val[0], dict):
                        fixture_row = val[0]
                        break

            league_id: str | None = None
            if isinstance(fixture_row, dict):
                league = fixture_row.get("league")
                if isinstance(league, dict) and league.get("id") is not None:
                    league_id = _as_league_id(league["id"])
                else:
                    league_id = _as_league_id(
                        fixture_row.get("league_id")
                        if fixture_row.get("league_id") is not None
                        else fixture_row.get("leagueId")
                    )

            finished_raw: list[Any] = []
            if league_id is not None:
                hist = client.fixtures_by_league(league_id, season=season, status="FINISHED")
                finished_raw = _normalize_fixtures_list(hist)

            standings_payload: Any = None
            h2h_rows: list[Any] = []
            kickoff = None
            if isinstance(fixture_row, dict):
                kickoff = (
                    fixture_row.get("starting_at")
                    or fixture_row.get("startingAt")
                    or fixture_row.get("date")
                    or fixture_row.get("kickoff")
                )
            if league_id is not None:
                try:
                    standings_payload = client.league_standings(league_id, season=season)
                except GoalApiError:
                    standings_payload = None
            try:
                h2h_rows = _normalize_fixtures_list(client.h2h(home_id, away_id))
            except GoalApiError:
                h2h_rows = []

            priors = compute_prematch_priors(
                home_team_id=home_id,
                away_team_id=away_id,
                finished=finished_raw,
                fixture_id=fixture_id,
                shrink_k=shrink_k,
            )
            extras = build_extra_signals(
                home_team_id=home_id,
                away_team_id=away_id,
                finished=finished_raw,
                h2h_rows=h2h_rows,
                standings_payload=standings_payload,
                kickoff=kickoff,
                league_baseline=priors.league_pct_over05,
            )
            result = score_prematch(
                priors,
                fixture_id=fixture_id,
                extra_signals=extras.signals or None,
            )
            _dump(
                {
                    "fixture_id": result.fixture_id,
                    "context": result.context,
                    "p_over05": result.p_over05,
                    "p_00": result.p_00,
                    "xg_score": result.xg_score,
                    "weights_used": dict(result.weights_used),
                    "features": dict(result.features),
                    "extra_signals": dict(extras.signals),
                    "extra_omit_notes": list(extras.notes),
                    "notes": list(result.notes),
                    "history_n": len(finished_raw),
                    "rate_limit": None
                    if client.last_rate_limit is None
                    else {
                        "limit": client.last_rate_limit.limit,
                        "remaining": client.last_rate_limit.remaining,
                        "type": client.last_rate_limit.limit_type,
                    },
                }
            )
    except GoalApiError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


@app.command("watch-live")
def watch_live_cmd(
    list_only: bool = typer.Option(
        True,
        "--list-only/--subscribe",
        help="List Big-5 0-0 candidates (1 REST). --subscribe opens WS.",
    ),
    score: bool = typer.Option(
        False,
        "--score/--no-score",
        help="Densify REST stats + emit xG for in-window 0-0 candidates",
    ),
    max_events: int = typer.Option(
        20,
        "--max-events",
        help="With --subscribe: stop after N match_update frames",
    ),
) -> None:
    """Phase B: list / watch live Big-5 for the ≈30′ 0-0 trigger.

    Budget: default is one ``/fixtures/live`` REST call. Statistics REST only
    when ``--score`` and a fixture is in the 28–32′ 0-0 window.
    """
    try:
        with _client() as client:
            if list_only:
                candidates = list_live30_candidates(
                    client,
                    big5_only=True,
                    require_00=True,
                    in_window_only=False,
                )
                in_window = [c for c in candidates if c.get("in_window")]
                scored: list[dict[str, Any]] = []
                if score:
                    for c in in_window:
                        result = score_fixture_live30(client, c["fixture_id"])
                        scored.append(live30_score_to_dict(result))
                _dump(
                    {
                        "mode": "list",
                        "candidates_00": candidates,
                        "in_window_00": in_window,
                        "scored": scored,
                        "rate_limit": None
                        if client.last_rate_limit is None
                        else {
                            "limit": client.last_rate_limit.limit,
                            "remaining": client.last_rate_limit.remaining,
                            "type": client.last_rate_limit.limit_type,
                        },
                        "budget_note": (
                            "WS-first for clock; REST stats only on --score "
                            "for in-window 0-0 (GOAL ~1000 req/day)"
                        ),
                    }
                )
                return

            live_payload = client.fixtures_live()
            big5_ids = {lg.league_id for lg in client.discover_big5_leagues()}
            match_ids: list[str] = []
            for row in _normalize_fixtures_list(live_payload):
                league = row.get("league") if isinstance(row.get("league"), dict) else {}
                lid = _as_league_id(
                    league.get("id") or row.get("leagueId") or row.get("league_id")
                )
                if lid is None or lid not in big5_ids:
                    continue
                state = live_row_to_state(row)
                if state and state.fixture_id:
                    match_ids.append(str(state.fixture_id))
            match_ids = list(dict.fromkeys(match_ids))[:50]
            if not match_ids:
                _dump(
                    {
                        "mode": "subscribe",
                        "subscribed": [],
                        "events": [],
                        "note": "no live Big-5",
                    }
                )
                return

            async def _run() -> list[dict[str, Any]]:
                events: list[dict[str, Any]] = []
                with GoalWsClient() as ws_client:
                    sock = await ws_client.connect_and_subscribe(match_ids)
                    n = 0
                    async for st in ws_client.iter_match_states(sock):
                        row_out: dict[str, Any] = {
                            "fixture_id": st.fixture_id,
                            "minute": st.minute,
                            "period": st.period,
                            "score_home": st.home_score,
                            "score_away": st.away_score,
                            "is_00": st.is_00,
                            "in_window": st.in_live30_window,
                            "is_live30_candidate": st.is_live30_candidate,
                            "home_name": st.home_name,
                            "away_name": st.away_name,
                        }
                        if st.is_live30_candidate and score and st.fixture_id:
                            scored = score_fixture_live30(
                                client,
                                st.fixture_id,
                                clock_override=st,
                            )
                            row_out["score"] = live30_score_to_dict(scored)
                        events.append(row_out)
                        n += 1
                        if n >= max_events:
                            break
                    try:
                        await sock.close()
                    except Exception:
                        pass
                return events

            events = asyncio.run(_run())
            _dump(
                {
                    "mode": "subscribe",
                    "subscribed": match_ids,
                    "events": events,
                }
            )
    except GoalApiError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


@app.command("score-live30")
def score_live30_cmd(
    fixture_id: str = typer.Option(..., "--fixture-id", help="GOAL fixture id"),
    season: Optional[int] = typer.Option(
        None, "--season", help="Season year for history fetch"
    ),
    no_history: bool = typer.Option(
        False, "--no-history", help="Skip league history REST (save quota)"
    ),
) -> None:
    """Phase B: product xG for one fixture if 0-0 @ ≈30′ 1H (else skip/settled).

    Fail-closed without ``GOAL_API_KEY``. REST stats densified only in-window 0-0.
    """
    try:
        with _client() as client:
            result = score_fixture_live30(
                client,
                fixture_id,
                fetch_history=not no_history,
                season=season,
            )
            out = live30_score_to_dict(result)
            out["rate_limit"] = (
                None
                if client.last_rate_limit is None
                else {
                    "limit": client.last_rate_limit.limit,
                    "remaining": client.last_rate_limit.remaining,
                    "type": client.last_rate_limit.limit_type,
                }
            )
            _dump(out)
            if result.skipped:
                raise typer.Exit(code=2)
    except GoalApiError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


@app.command("daily-refresh")
def daily_refresh_cmd(
    day: Optional[str] = typer.Option(
        None,
        "--day",
        help="Europe/Rome calendar day YYYY-MM-DD (default: oggi)",
    ),
    no_tomorrow: bool = typer.Option(
        False,
        "--no-tomorrow",
        help="Skip tomorrow's programme fetch (saves ~5 REST calls)",
    ),
    season: Optional[int] = typer.Option(
        None, "--season", help="Override season year for history/standings"
    ),
    cache_dir: Optional[str] = typer.Option(
        None,
        "--cache-dir",
        help="Daily store root (default: GOAL_API_CACHE_DIR/daily)",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Log per-league warnings"),
) -> None:
    """Daily job: refresh Big-5 fixtures + season-to-date team stats to disk.

    Persists under ``.cache/goal_api/daily/`` (or ``--cache-dir``): today's
    programme, FT history, standings, and per-team HA rates / U5 GF / ranks.
    Intended for systemd timer / cron (~05:00 UTC). GOAL primary; ~16–21 REST.
    """
    if verbose:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        with _client() as client:
            root = Path(cache_dir) if cache_dir else default_daily_cache_dir()
            store = DailyStore(root)
            result = run_daily_refresh(
                client,
                store=store,
                day=day,
                include_tomorrow=not no_tomorrow,
                season=season,
            )
            _dump(result.to_dict())
            if not result.ok:
                raise typer.Exit(code=1)
    except GoalApiError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


@app.command("coach")
def coach_cmd(
    team_id: Optional[int] = typer.Option(
        None, "--team-id", help="football-data.org team id"
    ),
    person_id: Optional[int] = typer.Option(
        None, "--person-id", help="football-data.org person id (section Coach)"
    ),
    competition: Optional[str] = typer.Option(
        None,
        "--competition",
        help="Big-5 code PL|PD|SA|BL1|FL1 — list coaches for all teams",
    ),
) -> None:
    """Resolve coach identity via football-data.org (soft feature; not H2H).

    Requires ``FOOTBALL_DATA_API_KEY``. True coach-vs-coach H2H is unavailable
    on this API — scoring still omits ``coach_h2h`` + renorm.
    """
    if team_id is None and person_id is None and competition is None:
        typer.secho(
            "Provide --team-id and/or --person-id, or --competition CODE",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    try:
        with _fd_client() as client:
            out: dict[str, Any] = {
                "feature": "coach_identity",
                "h2h_available": False,
                "note": "soft identity only; coach_h2h stays omit+renorm",
            }
            if competition is not None:
                code = competition.upper().strip()
                coaches = client.coaches_for_competition(code)
                out["competition"] = code
                out["coaches"] = [
                    {
                        "person_id": c.person_id,
                        "name": c.name,
                        "nationality": c.nationality,
                        "team_id": c.team_id,
                        "team_name": c.team_name,
                    }
                    for c in coaches
                ]
                out["count"] = len(coaches)
            if team_id is not None:
                ident = client.coach_for_team(team_id)
                out["team_id"] = team_id
                out["coach"] = None
                if ident is not None:
                    out["coach"] = {
                        "person_id": ident.person_id,
                        "name": ident.name,
                        "nationality": ident.nationality,
                        "date_of_birth": ident.date_of_birth,
                        "team_id": ident.team_id,
                        "team_name": ident.team_name,
                        "section": ident.section,
                    }
            if person_id is not None:
                ident = client.coach_person(person_id)
                out["person_id"] = person_id
                out["person"] = None
                if ident is not None:
                    out["person"] = {
                        "person_id": ident.person_id,
                        "name": ident.name,
                        "nationality": ident.nationality,
                        "team_id": ident.team_id,
                        "team_name": ident.team_name,
                        "section": ident.section,
                    }
            rl = client.last_rate_limit
            out["rate_limit"] = (
                None
                if rl is None
                else {
                    "remaining_minute": rl.remaining_minute,
                    "remaining_day": rl.remaining_day,
                    "retry_after": rl.retry_after,
                }
            )
            _dump(out)
    except FootballDataError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


def main() -> None:
    if "GOAL_API_KEY" not in os.environ or "FOOTBALL_DATA_API_KEY" not in os.environ:
        _load_env()
    app()


if __name__ == "__main__":
    main()
    sys.exit(0)
