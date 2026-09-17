"""Tests for daily fixtures + team-stats refresh job."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from goal_xg.clients.goal_api import Big5League, GoalApiClient
from goal_xg.jobs.daily_refresh import (
    compute_team_stats_for_league,
    run_daily_refresh,
)
from goal_xg.jobs.store import DailyStore
from goal_xg.live30.service import _cached_history_for_league, _cached_standings_for_league


def _league(code: str = "PL", lid: str = "lg-pl") -> Big5League:
    return Big5League(
        code=code,
        league_id=lid,
        name=f"League {code}",
        country="Test",
        season=2025,
    )


def _ft_row(
    *,
    hid: str,
    aid: str,
    gh: int,
    ga: int,
    home_name: str = "Home",
    away_name: str = "Away",
) -> dict:
    return {
        "id": f"{hid}-{aid}-{gh}-{ga}",
        "teams": {
            "home": {"id": hid, "name": home_name},
            "away": {"id": aid, "name": away_name},
        },
        "goals": {"home": gh, "away": ga},
        "matchStatus": "FT",
    }


def test_as_finished_parses_goal_flat_scores() -> None:
    from goal_xg.features.prematch import _as_finished

    rows = _as_finished(
        [
            {
                "homeTeamId": "t-home",
                "awayTeamId": "t-away",
                "homeTeamFtScore": "2",
                "awayTeamFtScore": "1",
                "leagueId": "lg1",
            }
        ]
    )
    assert len(rows) == 1
    assert rows[0].home_team_id == "t-home"
    assert rows[0].away_team_id == "t-away"
    assert rows[0].goals_home == 2
    assert rows[0].goals_away == 1

    lg = _league()
    hist = [
        _ft_row(hid="1", aid="99", gh=2, ga=0, home_name="Alpha"),
        _ft_row(hid="1", aid="98", gh=1, ga=1, home_name="Alpha"),
        _ft_row(hid="97", aid="2", gh=0, ga=3, away_name="Beta"),
        _ft_row(hid="96", aid="2", gh=1, ga=0, away_name="Beta"),
    ]
    standings = {
        "data": [
            {"team": {"id": "1", "name": "Alpha"}, "rank": 2, "played": 8, "points": 18},
            {"team": {"id": "2", "name": "Beta"}, "rank": 10, "played": 8, "points": 9},
        ]
    }
    rows = compute_team_stats_for_league(
        league=lg,
        finished_rows=hist,
        standings_payload=standings,
        as_of_day="2026-09-17",
    )
    by_id = {r["team_id"]: r for r in rows}
    assert "1" in by_id and "2" in by_id
    assert by_id["1"]["n_home"] == 2
    assert by_id["1"]["home"]["gf_avg"] == 1.5
    assert by_id["1"]["standing_rank"] == 2
    assert by_id["1"]["standing_points"] == 18
    assert by_id["2"]["n_away"] == 2
    assert by_id["2"]["away"]["gf_avg"] == 1.5  # 3 then 0
    assert by_id["2"]["home"]["gf_last5"] is None
    assert by_id["1"]["team_name"] == "Alpha"


def test_daily_store_roundtrip(tmp_path: Path) -> None:
    store = DailyStore(tmp_path)
    store.save_fixtures(
        "2026-09-17",
        {"day": "2026-09-17", "count": 1, "fixtures": [{"fixture_id": "fx1"}]},
    )
    store.save_history(
        "PL",
        {
            "league_code": "PL",
            "league_id": "lg-pl",
            "fixtures": [_ft_row(hid="1", aid="2", gh=1, ga=0)],
        },
    )
    store.save_standings(
        "PL",
        {"league_code": "PL", "league_id": "lg-pl", "payload": {"data": []}},
    )
    store.save_meta(
        {
            "ok": True,
            "leagues": [{"code": "PL", "league_id": "lg-pl"}],
        }
    )
    assert store.load_fixtures("2026-09-17")["count"] == 1
    assert len(store.load_history_rows("PL")) == 1
    assert store.find_league_code_for_id("lg-pl") == "PL"
    assert store.load_standings_payload("PL") == {"data": []}


def test_run_daily_refresh_persists_and_quota_bounded(tmp_path: Path, monkeypatch) -> None:
    """Mock GOAL: 5 leagues × (fixtures today + tomorrow + hist + standings)."""
    leagues = [
        _league("PL", "lg-pl"),
        _league("PD", "lg-pd"),
        _league("SA", "lg-sa"),
        _league("BL1", "lg-bl1"),
        _league("FL1", "lg-fl1"),
    ]
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(path)
        if path.endswith("/leagues"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": lg.league_id,
                            "name": {
                                "PL": "Premier League",
                                "PD": "La Liga",
                                "SA": "Serie A",
                                "BL1": "Bundesliga",
                                "FL1": "Ligue 1",
                            }[lg.code],
                            "country": {
                                "PL": "England",
                                "PD": "Spain",
                                "SA": "Italy",
                                "BL1": "Germany",
                                "FL1": "France",
                            }[lg.code],
                            "season": 2025,
                        }
                        for lg in leagues
                    ]
                },
            )
        # fixtures by date / league
        if path.endswith("/fixtures"):
            params = dict(request.url.params)
            status = params.get("status")
            lid = params.get("leagueId") or params.get("league")
            if status in ("FT", "FINISHED"):
                tid_h = f"h-{lid}"
                tid_a = f"a-{lid}"
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            _ft_row(
                                hid=tid_h,
                                aid=tid_a,
                                gh=2,
                                ga=1,
                                home_name=f"H-{lid}",
                                away_name=f"A-{lid}",
                            )
                        ]
                    },
                )
            # scheduled day list
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": f"fx-{lid}-{params.get('date')}",
                            "apiId": None,
                            "matchStatus": "SCHEDULED",
                            "starting_at": f"{params.get('date')}T15:00:00Z",
                            "teams": {
                                "home": {"id": f"h-{lid}", "name": "Home FC"},
                                "away": {"id": f"a-{lid}", "name": "Away FC"},
                            },
                            "goals": {"home": None, "away": None},
                            "league": {"id": lid, "name": "L"},
                        }
                    ]
                },
            )
        if "/standings/" in path:
            lid = path.rstrip("/").split("/")[-1]
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "team": {"id": f"h-{lid}", "name": "Home FC"},
                            "overallLeaguePosition": "1",
                            "overallLeaguePlayed": "5",
                            "overallLeaguePTS": "12",
                            "overallLeagueGF": "10",
                            "overallLeagueGA": "3",
                        },
                        {
                            "team": {"id": f"a-{lid}", "name": "Away FC"},
                            "overallLeaguePosition": "8",
                            "overallLeaguePlayed": "5",
                            "overallLeaguePTS": "6",
                            "overallLeagueGF": "4",
                            "overallLeagueGA": "7",
                        },
                    ]
                },
            )
        if path.endswith("/standings"):
            return httpx.Response(404, json={"error": "use /standings/{id}"})
        return httpx.Response(404, json={"error": path})

    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="https://api.goal-api.com/v1")
    # Seed league cache so discover does not need name match quirks twice
    cache = tmp_path / "goal_api"
    cache.mkdir()
    (cache / "big5_leagues.json").write_text(
        json.dumps(
            {
                "leagues": [
                    {
                        "code": lg.code,
                        "league_id": lg.league_id,
                        "name": lg.name,
                        "country": lg.country,
                        "season": lg.season,
                    }
                    for lg in leagues
                ]
            }
        ),
        encoding="utf-8",
    )
    client = GoalApiClient(
        api_key="test-key-not-real",
        client=http,
        cache_dir=cache,
    )
    store = DailyStore(tmp_path / "daily")
    monkeypatch.setenv("GOAL_API_CACHE_DIR", str(cache))

    result = run_daily_refresh(
        client,
        store=store,
        day="2026-09-17",
        include_tomorrow=True,
    )
    assert result.ok
    assert result.fixtures_count == 5
    assert result.fixtures_tomorrow_count == 5
    assert result.teams_count >= 10  # 2 teams × 5 leagues
    assert set(result.leagues_ok) == {"PL", "PD", "SA", "BL1", "FL1"}
    assert store.load_fixtures("2026-09-17")["count"] == 5
    assert store.load_team_stats("2026-09-17")["count"] == result.teams_count
    assert store.load_history_rows("PL")
    assert store.load_standings_payload("SA") is not None

    fixture_calls = sum(1 for c in calls if c.endswith("/fixtures") or "/fixtures?" in c)
    # MockTransport records path without query — fixtures path ends with /fixtures
    fixture_calls = sum(1 for c in calls if c.rstrip("/").endswith("fixtures"))
    standings_calls = sum(1 for c in calls if "/standings/" in c)
    assert fixture_calls == 15  # 5+5+5
    assert standings_calls == 5
    assert fixture_calls + standings_calls <= 25

    # Service-layer cache helpers resolve via meta league_id map
    monkeypatch.chdir(tmp_path)
    # Point DailyStore default via env already set (cache/daily not used — helpers use default_daily_cache_dir)
    # Re-point: helpers construct DailyStore() → GOAL_API_CACHE_DIR/daily
    # Our store was tmp_path/daily but env GOAL_API_CACHE_DIR=cache → looks at cache/daily
    # Copy artifacts into the env path:
    env_daily = cache / "daily"
    env_daily.mkdir(parents=True, exist_ok=True)
    for src in store.root.iterdir():
        (env_daily / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    hist = _cached_history_for_league("lg-pl")
    assert len(hist) == 1
    stand = _cached_standings_for_league("lg-fl1")
    assert stand is not None
