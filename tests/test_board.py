"""Board helpers: today's Big-5 schedule + watch-focus (0-0 only)."""

from __future__ import annotations

from goal_xg.live30.board import (
    classify_match_status,
    filter_watch_00,
    select_watch_focus,
)
from goal_xg.web.app import build_day_list, group_day_list_by_league


def test_classify_match_status() -> None:
    assert classify_match_status({"matchStatus": "SCHEDULED"}) == "scheduled"
    assert classify_match_status({"matchStatus": "LIVE", "matchLive": "1"}) == "live"
    assert classify_match_status({"matchStatus": "FT"}) == "finished"
    assert classify_match_status({"matchStatus": "HALF_TIME"}) == "live"


def test_filter_watch_00_drops_settled() -> None:
    rows = [
        {"fixture_id": "a", "is_00": True, "score_home": 0, "score_away": 0, "minute": 30},
        {"fixture_id": "b", "is_00": False, "score_home": 1, "score_away": 0, "minute": 30},
        {"fixture_id": "c", "is_00": True, "score_home": 0, "score_away": 1, "minute": 29},
    ]
    kept = filter_watch_00(rows)
    assert [r["fixture_id"] for r in kept] == ["a"]


def test_select_watch_focus_prefers_finestra_then_upcoming() -> None:
    candidates = [
        {
            "fixture_id": "win",
            "is_00": True,
            "score_home": 0,
            "score_away": 0,
            "minute": 30,
            "in_window": True,
        }
    ]
    live = [
        {
            "fixture_id": "early",
            "is_00": True,
            "score_home": 0,
            "score_away": 0,
            "minute": 12,
            "in_window": False,
        },
        {
            "fixture_id": "settled",
            "is_00": False,
            "score_home": 1,
            "score_away": 0,
            "minute": 30,
            "in_window": True,
        },
    ]
    schedule = [
        {
            "fixture_id": "next",
            "status": "scheduled",
            "is_00": True,
            "kickoff_time": "19:30",
            "home_name": "A",
            "away_name": "B",
        }
    ]
    focus = select_watch_focus(
        candidates=candidates, live_cards=live, schedule_cards=schedule
    )
    assert focus is not None and focus["fixture_id"] == "win"

    focus2 = select_watch_focus(
        candidates=[], live_cards=live, schedule_cards=schedule
    )
    assert focus2 is not None and focus2["fixture_id"] == "early"

    focus3 = select_watch_focus(
        candidates=[],
        live_cards=[live[1]],
        schedule_cards=schedule,
    )
    assert focus3 is not None and focus3["fixture_id"] == "next"


def test_build_day_list_merges_live_and_keeps_all() -> None:
    scheduled = [
        {
            "fixture_id": "a",
            "home_name": "Home A",
            "away_name": "Away A",
            "league_name": "Serie A",
            "kickoff_time": "15:00",
            "kickoff_utc": "2099-01-01T14:00:00+00:00",
            "status": "scheduled",
            "is_00": True,
        },
        {
            "fixture_id": "b",
            "home_name": "Home B",
            "away_name": "Away B",
            "league_name": "La Liga",
            "kickoff_time": "18:00",
            "kickoff_utc": "2099-01-01T17:00:00+00:00",
            "status": "scheduled",
            "is_00": True,
        },
    ]
    live = [
        {
            "fixture_id": "a",
            "home_name": "Home A",
            "away_name": "Away A",
            "league_name": "Serie A",
            "minute": 30,
            "score_home": 0,
            "score_away": 0,
            "is_00": True,
            "in_window": True,
            "xg_score": 62,
            "xg_tone": "high",
        }
    ]
    day = build_day_list(
        scheduled,
        live_cards=live,
        candidates=live,
        focus=live[0],
    )
    assert len(day) == 2
    by_id = {c["fixture_id"]: c for c in day}
    assert by_id["a"]["status"] == "live"
    assert by_id["a"]["minute"] == 30
    assert by_id["a"]["is_focus"] is True
    assert "win" in by_id["a"]["filter_tags"]
    assert by_id["b"]["status"] == "scheduled"
    assert "oggi" in by_id["b"]["filter_tags"]
    groups = group_day_list_by_league(day)
    assert {g["league_name"] for g in groups} == {"Serie A", "La Liga"}
    # Live league sorts first.
    assert groups[0]["league_name"] == "Serie A"
