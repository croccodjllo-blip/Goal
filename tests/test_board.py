"""Board helpers: today's Big-5 schedule + watch-focus (0-0 only)."""

from __future__ import annotations

from goal_xg.live30.board import (
    classify_match_status,
    filter_watch_00,
    select_watch_focus,
)


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
