"""Live-board helpers: today's Big-5 schedule + watch-focus selection.

Focus rules (product-xG Over 0.5):
1. Prefer live30 Finestra candidates (still 0-0, 28–32′ 1H).
2. Else nearest live Big-5 still 0-0 (approaching the window).
3. Else next upcoming scheduled Big-5 from today's programme.
Settled (≥1 goal) never stay as primary watch-0-0 focus.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from goal_xg.clients.goal_api import GoalApiClient
from goal_xg.live30.service import live_row_to_state
from goal_xg.live30.window import is_score_00

_ROME = ZoneInfo("Europe/Rome")
_SCHEDULE_TTL_SEC = 120.0
_schedule_cache: dict[str, Any] = {"key": None, "expires": 0.0, "rows": []}

_LIVE_STATUS = frozenset(
    {
        "live",
        "inplay",
        "in_play",
        "1h",
        "2h",
        "ht",
        "half_time",
        "halftime",
        "first_half",
        "second_half",
        "extra_time",
        "et",
        "penalty",
        "pen",
        "break",
    }
)
_FINISHED_STATUS = frozenset(
    {
        "ft",
        "finished",
        "aet",
        "pen_ft",
        "after_pen",
        "after_et",
        "awarded",
        "wo",
        "cancl",
        "cancelled",
        "canceled",
        "postponed",
        "abandoned",
        "abd",
    }
)
_SCHEDULED_STATUS = frozenset(
    {
        "scheduled",
        "ns",
        "not_started",
        "notstarted",
        "tbd",
        "fixture",
        "upcoming",
        "timed",
        "nsy",
    }
)


def unwrap_fixture_rows(payload: Any) -> list[dict[str, Any]]:
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


def classify_match_status(row: Mapping[str, Any]) -> str:
    """Map provider status → ``scheduled`` | ``live`` | ``finished``."""
    raw = str(
        row.get("matchStatus")
        or row.get("status")
        or row.get("match_status")
        or ""
    ).strip()
    token = raw.lower().replace(" ", "_").replace("-", "_")
    live_flag = str(row.get("matchLive") or "").strip()
    if live_flag in ("1", "true", "True", "yes"):
        return "live"
    if token in _FINISHED_STATUS or token.startswith("ft"):
        return "finished"
    if token in _LIVE_STATUS or token == "live":
        return "live"
    if token in _SCHEDULED_STATUS or token == "":
        elapsed = row.get("matchElapsed") or row.get("matchMinute") or row.get("minute")
        if elapsed is not None and str(elapsed).strip() not in ("", "0"):
            try:
                if int(str(elapsed).rstrip("'")) > 0:
                    return "live"
            except (TypeError, ValueError):
                pass
        return "scheduled"
    if row.get("matchElapsed") is not None:
        return "live"
    return "scheduled"


def parse_kickoff_utc(row: Mapping[str, Any]) -> datetime | None:
    raw = (
        row.get("kickoffUtc")
        or row.get("kickoff_utc")
        or row.get("starting_at")
        or row.get("startingAt")
        or row.get("kickoff")
    )
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        try:
            ts = float(raw)
            if ts > 1e12:
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(raw).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def display_kickoff_time(row: Mapping[str, Any]) -> str:
    mt = row.get("matchTime") or row.get("time")
    if mt:
        return str(mt)[:5]
    kickoff = parse_kickoff_utc(row)
    if kickoff is not None:
        return kickoff.astimezone(_ROME).strftime("%H:%M")
    return "—"


def schedule_row_card(row: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize a GOAL fixture row for the today's-programme list."""
    state = live_row_to_state(dict(row))
    status = classify_match_status(row)
    home_score = state.home_score if state else None
    away_score = state.away_score if state else None
    if home_score is None:
        for key in ("homeTeamScore", "homeTeamFtScore", "homeScore"):
            if row.get(key) is not None and str(row.get(key)).strip() != "":
                try:
                    home_score = int(row[key])
                except (TypeError, ValueError):
                    pass
                break
    if away_score is None:
        for key in ("awayTeamScore", "awayTeamFtScore", "awayScore"):
            if row.get(key) is not None and str(row.get(key)).strip() != "":
                try:
                    away_score = int(row[key])
                except (TypeError, ValueError):
                    pass
                break

    league = row.get("league") if isinstance(row.get("league"), dict) else {}
    fid = row.get("id") or row.get("fixtureId")
    kickoff = parse_kickoff_utc(row)
    is_00: bool
    if home_score is not None and away_score is not None:
        is_00 = is_score_00(home_score, away_score)
    else:
        is_00 = status == "scheduled"

    return {
        "fixture_id": str(fid) if fid is not None else "",
        "home_name": (state.home_name if state else None)
        or row.get("homeTeamName")
        or "?",
        "away_name": (state.away_name if state else None)
        or row.get("awayTeamName")
        or "?",
        "league_name": (state.league_name if state else None)
        or league.get("name")
        or row.get("leagueName")
        or "",
        "kickoff_time": display_kickoff_time(row),
        "kickoff_label": (
            kickoff.astimezone(_ROME).strftime("%H:%M") if kickoff else display_kickoff_time(row)
        ),
        "kickoff_utc": kickoff.isoformat() if kickoff else None,
        "match_date": row.get("matchDate") or row.get("date"),
        "status": status,
        "status_label": {
            "scheduled": "Programmata",
            "live": "Live",
            "finished": "Finita",
        }.get(status, status),
        "minute": state.minute if state else None,
        "score_home": home_score,
        "score_away": away_score,
        "is_00": is_00,
    }


def list_todays_big5_fixtures(
    client: GoalApiClient,
    *,
    day: date | str | None = None,
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    """Today's Big-5 fixtures via GOAL ``from``/``to`` + leagueId.

    One REST call per Big-5 league (≤5). Cached briefly for index refresh.
    """
    if isinstance(day, date):
        day_s = day.isoformat()
    elif isinstance(day, str) and day.strip():
        day_s = day.strip()[:10]
    else:
        # Europe/Rome "oggi" for the board (kickoffs shown in Rome time).
        day_s = datetime.now(tz=_ROME).date().isoformat()

    now = time.monotonic()
    if (
        use_cache
        and _schedule_cache.get("key") == day_s
        and float(_schedule_cache.get("expires") or 0) > now
    ):
        return list(_schedule_cache["rows"])

    try:
        leagues = client.discover_big5_leagues()
    except Exception:
        return []

    cards: list[dict[str, Any]] = []
    seen: set[str] = set()
    for lg in leagues:
        try:
            payload = client.fixtures_by_date(day_s, league_id=lg.league_id)
        except Exception:
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
            cards.append(card)

    cards.sort(
        key=lambda c: (
            str(c.get("kickoff_utc") or "9999"),
            str(c.get("kickoff_time") or ""),
            str(c.get("league_name") or ""),
            str(c.get("home_name") or ""),
        )
    )
    _schedule_cache["key"] = day_s
    _schedule_cache["expires"] = now + _SCHEDULE_TTL_SEC
    _schedule_cache["rows"] = list(cards)
    return cards


def clear_schedule_cache() -> None:
    """Test helper — drop the in-process schedule TTL cache."""
    _schedule_cache["key"] = None
    _schedule_cache["expires"] = 0.0
    _schedule_cache["rows"] = []


def filter_watch_00(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only still-0-0 rows for focus / Finestra lists."""
    out: list[dict[str, Any]] = []
    for card in cards:
        sh, sa = card.get("score_home"), card.get("score_away")
        if sh is not None and sa is not None:
            if not is_score_00(sh, sa):
                continue
            out.append(card)
            continue
        if card.get("is_00") is True:
            out.append(card)
    return out


def select_watch_focus(
    *,
    candidates: list[dict[str, Any]],
    live_cards: list[dict[str, Any]],
    schedule_cards: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Pick the next primary watch target after dropping non-0-0.

    Order:
    1. Finestra live30 candidates (0-0 @ 28–32′) — closest to 30′.
    2. Other live Big-5 still 0-0 with minute < 32 (highest minute).
    3. Next upcoming scheduled Big-5 from today's programme.
    """
    cand_00 = filter_watch_00(candidates)
    if cand_00:
        return max(
            cand_00,
            key=lambda c: (
                0 if c.get("minute") is None else -abs(int(c["minute"]) - 30),
                int(c["minute"]) if c.get("minute") is not None else -1,
            ),
        )

    live_00 = filter_watch_00(live_cards)
    approaching = [
        c
        for c in live_00
        if isinstance(c.get("minute"), int) and c["minute"] < 32
    ]
    if approaching:
        return max(approaching, key=lambda c: int(c.get("minute") or 0))

    for card in schedule_cards or []:
        status = str(card.get("status") or "")
        if status == "scheduled":
            return card
        if status == "live" and card.get("is_00"):
            return card
    return None


def focus_reason(focus: Mapping[str, Any] | None) -> str | None:
    if not focus:
        return None
    if focus.get("in_window") and focus.get("is_00"):
        return "finestra"
    if focus.get("is_00") and focus.get("minute") is not None:
        return "live_00"
    if str(focus.get("status") or "") == "scheduled":
        return "upcoming"
    return "watch"
