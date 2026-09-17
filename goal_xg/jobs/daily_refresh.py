"""Daily fixtures refresh + season-to-date team statistics (GOAL primary).

Runs once per day (systemd timer / cron). Persists:

1. Today's (+ optional tomorrow) Big-5 programme
2. Per-league FT history + standings
3. Per-team side stats accumulated up to the run moment

Quota (GOAL): ~1 league discovery (cached) + 5 fixtures/day + 5 history +
5 standings (+ optional 5 tomorrow) ≈ 16–21 REST calls. Fail-closed on
missing key / hard API errors; per-league soft failures are logged and
skipped so other leagues still persist.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from goal_xg.clients.goal_api import Big5League, GoalApiClient, GoalApiError
from goal_xg.features.extractors import _standings_table, _team_rank
from goal_xg.features.prematch import FinishedFixture, _as_finished, team_side_stats
from goal_xg.jobs.store import DailyStore, default_daily_cache_dir
from goal_xg.live30.board import schedule_row_card, unwrap_fixture_rows

_ROME = ZoneInfo("Europe/Rome")
logger = logging.getLogger(__name__)


@dataclass
class DailyRefreshResult:
    """Summary of one daily-refresh run (JSON-serializable via ``to_dict``)."""

    ok: bool
    as_of_day: str  # Europe/Rome calendar day
    ran_at_utc: str
    fixtures_count: int = 0
    fixtures_tomorrow_count: int = 0
    teams_count: int = 0
    leagues_ok: list[str] = field(default_factory=list)
    leagues_failed: list[dict[str, str]] = field(default_factory=list)
    store_root: str = ""
    rate_limit: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "as_of_day": self.as_of_day,
            "ran_at_utc": self.ran_at_utc,
            "fixtures_count": self.fixtures_count,
            "fixtures_tomorrow_count": self.fixtures_tomorrow_count,
            "teams_count": self.teams_count,
            "leagues_ok": list(self.leagues_ok),
            "leagues_failed": list(self.leagues_failed),
            "store_root": self.store_root,
            "rate_limit": self.rate_limit,
            "notes": list(self.notes),
            "error": self.error,
        }


def _rome_today() -> date:
    return datetime.now(tz=_ROME).date()


def _rate_limit_dict(client: GoalApiClient) -> dict[str, Any] | None:
    rl = client.last_rate_limit
    if rl is None:
        return None
    return {
        "limit": rl.limit,
        "remaining": rl.remaining,
        "type": rl.limit_type,
    }


def _team_name_from_row(row: dict[str, Any], *, side: str) -> str | None:
    teams = row.get("teams") if isinstance(row.get("teams"), dict) else {}
    side_obj = teams.get(side) if isinstance(teams.get(side), dict) else {}
    name = (
        side_obj.get("name")
        or row.get(f"{side}TeamName")
        or row.get(f"{side}_team_name")
        or row.get(f"{side}Name")
    )
    return str(name) if name else None


def _team_id_from_row(row: dict[str, Any], *, side: str) -> str | None:
    teams = row.get("teams") if isinstance(row.get("teams"), dict) else {}
    side_obj = teams.get(side) if isinstance(teams.get(side), dict) else {}
    tid = (
        side_obj.get("id")
        or row.get(f"{side}TeamId")
        or row.get(f"{side}_team_id")
    )
    if tid is None or str(tid).strip() == "":
        return None
    return str(tid).strip()


def _standing_extras(row: dict[str, Any]) -> dict[str, Any]:
    """Best-effort points / GF / GA from a standings row (provider shapes vary)."""
    team = row.get("team") if isinstance(row.get("team"), dict) else {}
    name = team.get("name") or row.get("team_name") or row.get("name")
    pts = row.get("points") or row.get("pts") or row.get("overall_league_PTS")
    all_block = row.get("all") if isinstance(row.get("all"), dict) else {}
    if pts is None:
        pts = all_block.get("points")
    goals_for = (
        row.get("goalsFor")
        or row.get("goals_for")
        or row.get("gf")
        or row.get("overall_league_GF")
    )
    goals_against = (
        row.get("goalsAgainst")
        or row.get("goals_against")
        or row.get("ga")
        or row.get("overall_league_GA")
    )
    goals = all_block.get("goals") if isinstance(all_block.get("goals"), dict) else {}
    if goals_for is None:
        goals_for = goals.get("for")
    if goals_against is None:
        goals_against = goals.get("against")
    out: dict[str, Any] = {}
    if name:
        out["team_name"] = str(name)
    try:
        if pts is not None:
            out["points"] = int(pts)
    except (TypeError, ValueError):
        pass
    try:
        if goals_for is not None:
            out["goals_for"] = int(goals_for)
    except (TypeError, ValueError):
        pass
    try:
        if goals_against is not None:
            out["goals_against"] = int(goals_against)
    except (TypeError, ValueError):
        pass
    return out


def _gf_last_n_side(
    fixtures: list[FinishedFixture],
    team_id: str,
    *,
    side: str,
    last_n: int = 5,
) -> tuple[float | None, int]:
    """Mean goals scored in last_n home or away matches (chronological order as given)."""
    rows: list[int] = []
    for f in fixtures:
        if side == "home" and str(f.home_team_id) == team_id:
            rows.append(f.goals_home)
        elif side == "away" and str(f.away_team_id) == team_id:
            rows.append(f.goals_away)
    # History from API is typically newest-last or mixed; take last_n of side filter order.
    tail = rows[-last_n:] if last_n > 0 else rows
    if not tail:
        return None, 0
    return sum(tail) / len(tail), len(tail)


def compute_team_stats_for_league(
    *,
    league: Big5League,
    finished_rows: list[dict[str, Any]],
    standings_payload: Any,
    as_of_day: str,
) -> list[dict[str, Any]]:
    """Build season-to-date (up to now) per-team stats for one Big-5 league."""
    finished = _as_finished(finished_rows)
    names: dict[str, str] = {}
    for row in finished_rows:
        for side in ("home", "away"):
            tid = _team_id_from_row(row, side=side)
            if tid is None:
                continue
            nm = _team_name_from_row(row, side=side)
            if nm:
                names[tid] = nm

    standings_by_id: dict[str, dict[str, Any]] = {}
    for srow in _standings_table(standings_payload):
        tid, rank, played = _team_rank(srow)
        if tid is None:
            continue
        extras = _standing_extras(srow)
        standings_by_id[tid] = {
            "rank": rank,
            "played": played,
            **extras,
        }
        if extras.get("team_name"):
            names[tid] = str(extras["team_name"])

    team_ids = sorted(
        set(str(f.home_team_id) for f in finished)
        | set(str(f.away_team_id) for f in finished)
        | set(standings_by_id.keys())
    )

    out: list[dict[str, Any]] = []
    for tid in team_ids:
        home = team_side_stats(finished, tid, "home")
        away = team_side_stats(finished, tid, "away")
        gf5_h, n5_h = _gf_last_n_side(finished, tid, side="home", last_n=5)
        gf5_a, n5_a = _gf_last_n_side(finished, tid, side="away", last_n=5)
        st = standings_by_id.get(tid, {})
        out.append(
            {
                "team_id": tid,
                "team_name": names.get(tid),
                "league_id": league.league_id,
                "league_code": league.code,
                "league_name": league.name,
                "as_of": as_of_day,
                "n_home": home.n,
                "n_away": away.n,
                "home": {
                    "pct_over05": home.pct_over05,
                    "pct_fts": home.pct_fts,
                    "pct_cs": home.pct_cs,
                    "gf_avg": home.gf_avg,
                    "ga_avg": home.ga_avg,
                    "gf_last5": gf5_h,
                    "gf_last5_n": n5_h,
                },
                "away": {
                    "pct_over05": away.pct_over05,
                    "pct_fts": away.pct_fts,
                    "pct_cs": away.pct_cs,
                    "gf_avg": away.gf_avg,
                    "ga_avg": away.ga_avg,
                    "gf_last5": gf5_a,
                    "gf_last5_n": n5_a,
                },
                "standing_rank": st.get("rank"),
                "standing_played": st.get("played"),
                "standing_points": st.get("points"),
                "standing_goals_for": st.get("goals_for"),
                "standing_goals_against": st.get("goals_against"),
            }
        )
    return out


def _fetch_day_cards(
    client: GoalApiClient,
    leagues: list[Big5League],
    day_s: str,
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    seen: set[str] = set()
    for lg in leagues:
        try:
            payload = client.fixtures_by_date(day_s, league_id=lg.league_id)
        except GoalApiError as exc:
            logger.warning("fixtures %s %s failed: %s", lg.code, day_s, exc)
            continue
        for row in unwrap_fixture_rows(payload):
            card = schedule_row_card(row)
            fid = str(card.get("fixture_id") or "")
            if fid and fid in seen:
                continue
            if fid:
                seen.add(fid)
            if not card.get("league_name"):
                card["league_name"] = lg.name
            card["league_code"] = lg.code
            card["league_id"] = lg.league_id
            cards.append(card)
    cards.sort(
        key=lambda c: (
            str(c.get("kickoff_utc") or "9999"),
            str(c.get("kickoff_time") or ""),
            str(c.get("league_name") or ""),
            str(c.get("home_name") or ""),
        )
    )
    return cards


def run_daily_refresh(
    client: GoalApiClient,
    *,
    store: DailyStore | None = None,
    day: date | str | None = None,
    include_tomorrow: bool = True,
    season: int | None = None,
) -> DailyRefreshResult:
    """Refresh Big-5 fixtures + team stats; persist under the daily store.

    ``day`` defaults to Europe/Rome "oggi". Season defaults to each league's
    discovered season when present.
    """
    if isinstance(day, date):
        as_of = day
    elif isinstance(day, str) and day.strip():
        as_of = date.fromisoformat(day.strip()[:10])
    else:
        as_of = _rome_today()
    as_of_s = as_of.isoformat()
    ran_at = datetime.now(tz=timezone.utc).isoformat()
    store = store or DailyStore(default_daily_cache_dir())
    store.ensure()

    result = DailyRefreshResult(
        ok=False,
        as_of_day=as_of_s,
        ran_at_utc=ran_at,
        store_root=str(store.root),
    )

    try:
        leagues = client.discover_big5_leagues()
    except GoalApiError as exc:
        result.error = str(exc)
        result.notes.append("fail:discover_big5")
        store.save_meta(result.to_dict())
        return result

    # --- Fixtures (today + optional tomorrow) -----------------------------
    today_cards = _fetch_day_cards(client, leagues, as_of_s)
    result.fixtures_count = len(today_cards)
    store.save_fixtures(
        as_of_s,
        {
            "day": as_of_s,
            "ran_at_utc": ran_at,
            "source": "goal_api",
            "count": len(today_cards),
            "fixtures": today_cards,
        },
    )

    tomorrow_cards: list[dict[str, Any]] = []
    if include_tomorrow:
        tom_s = (as_of + timedelta(days=1)).isoformat()
        tomorrow_cards = _fetch_day_cards(client, leagues, tom_s)
        result.fixtures_tomorrow_count = len(tomorrow_cards)
        store.save_fixtures(
            tom_s,
            {
                "day": tom_s,
                "ran_at_utc": ran_at,
                "source": "goal_api",
                "count": len(tomorrow_cards),
                "fixtures": tomorrow_cards,
            },
        )

    # --- Per-league history + standings + team stats ----------------------
    all_team_stats: list[dict[str, Any]] = []
    league_meta: list[dict[str, Any]] = []

    for lg in leagues:
        league_meta.append(
            {
                "code": lg.code,
                "league_id": lg.league_id,
                "name": lg.name,
                "season": lg.season if season is None else season,
            }
        )
        use_season = season if season is not None else lg.season
        try:
            hist_payload = client.fixtures_by_league(
                lg.league_id, season=use_season, status="FT"
            )
            hist_rows = unwrap_fixture_rows(hist_payload)
        except GoalApiError as exc:
            logger.warning("history %s failed: %s", lg.code, exc)
            result.leagues_failed.append({"code": lg.code, "step": "history", "error": str(exc)})
            continue

        standings_payload: Any = None
        try:
            standings_payload = client.league_standings(lg.league_id, season=use_season)
        except GoalApiError as exc:
            logger.warning("standings %s failed: %s", lg.code, exc)
            result.leagues_failed.append(
                {"code": lg.code, "step": "standings", "error": str(exc)}
            )
            # Continue with history-only team stats (ranks omit).

        store.save_history(
            lg.code,
            {
                "league_code": lg.code,
                "league_id": lg.league_id,
                "league_name": lg.name,
                "season": use_season,
                "as_of": as_of_s,
                "ran_at_utc": ran_at,
                "count": len(hist_rows),
                "fixtures": hist_rows,
            },
        )
        store.save_standings(
            lg.code,
            {
                "league_code": lg.code,
                "league_id": lg.league_id,
                "season": use_season,
                "as_of": as_of_s,
                "ran_at_utc": ran_at,
                "payload": standings_payload,
            },
        )

        team_rows = compute_team_stats_for_league(
            league=lg,
            finished_rows=hist_rows,
            standings_payload=standings_payload,
            as_of_day=as_of_s,
        )
        all_team_stats.extend(team_rows)
        result.leagues_ok.append(lg.code)

    store.save_team_stats(
        as_of_s,
        {
            "as_of": as_of_s,
            "ran_at_utc": ran_at,
            "source": "goal_api",
            "count": len(all_team_stats),
            "teams": all_team_stats,
            "fields": [
                "n_home",
                "n_away",
                "home.pct_over05",
                "home.pct_fts",
                "home.pct_cs",
                "home.gf_avg",
                "home.ga_avg",
                "home.gf_last5",
                "away.*",
                "standing_rank",
                "standing_played",
                "standing_points",
                "standing_goals_for",
                "standing_goals_against",
            ],
        },
    )

    result.teams_count = len(all_team_stats)
    result.rate_limit = _rate_limit_dict(client)
    result.ok = len(result.leagues_ok) > 0
    if not result.ok:
        result.error = result.error or "no leagues refreshed"
        result.notes.append("fail:all_leagues")
    else:
        result.notes.append(
            f"persisted fixtures={result.fixtures_count} "
            f"tomorrow={result.fixtures_tomorrow_count} "
            f"teams={result.teams_count} leagues={result.leagues_ok}"
        )

    meta = result.to_dict()
    meta["leagues"] = league_meta
    store.save_meta(meta)
    return result
