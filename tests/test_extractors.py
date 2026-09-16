"""Tests for prematch/live extra-signal extractors."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from goal_xg.features.extractors import (
    EXTRA_SIGNAL_KEYS,
    RichFinished,
    build_extra_signals,
    fatigue_flag_from_signal,
    signal_club_h2h,
    signal_fatigue,
    signal_form,
    signal_goal_minutes_last5,
    signal_matchup,
    signal_standings,
    signal_streaks,
)
from goal_xg.live30.score import score_live30
from goal_xg.live30.stats import LiveVolumeStats


def _hist() -> list[dict]:
    rows = []
    base = datetime(2026, 9, 1, tzinfo=timezone.utc)
    for i in range(5):
        rows.append(
            {
                "home_team_id": 1,
                "away_team_id": 100 + i,
                "goals_home": 2,
                "goals_away": 0,
                "league_id": 135,
                "date": (base + timedelta(days=i * 7)).isoformat(),
                "goal_minutes": [12, 55],
            }
        )
        rows.append(
            {
                "home_team_id": 200 + i,
                "away_team_id": 2,
                "goals_home": 0,
                "goals_away": 1,
                "league_id": 135,
                "date": (base + timedelta(days=i * 7 + 1)).isoformat(),
                "goal_minutes": [40],
            }
        )
    return rows


def test_form_streaks_matchup_present() -> None:
    hist = _hist()
    form = signal_form(hist, 1, 2)
    streaks = signal_streaks(hist, 1, 2)
    matchup = signal_matchup(hist, 1, 2)
    assert form is not None and 0.0 <= form <= 1.0
    assert streaks is not None
    assert matchup is not None


def test_goal_minutes_omit_without_timestamps() -> None:
    bare = [
        {"home_team_id": 1, "away_team_id": 9, "goals_home": 1, "goals_away": 0},
        {"home_team_id": 8, "away_team_id": 2, "goals_home": 0, "goals_away": 2},
    ]
    assert signal_goal_minutes_last5(bare, 1, 2) is None
    assert signal_goal_minutes_last5(_hist(), 1, 2) is not None


def test_club_h2h_min_n_fail_closed() -> None:
    assert signal_club_h2h([{"home_team_id": 1, "away_team_id": 2, "goals_home": 1, "goals_away": 0}]) is None
    h2h = [
        {"home_team_id": 1, "away_team_id": 2, "goals_home": 1, "goals_away": 0},
        {"home_team_id": 2, "away_team_id": 1, "goals_home": 0, "goals_away": 0},
        {"home_team_id": 1, "away_team_id": 2, "goals_home": 2, "goals_away": 1},
    ]
    s = signal_club_h2h(h2h)
    assert s is not None and 0.0 < s < 1.0


def test_standings_and_fatigue() -> None:
    standings = {
        "data": [
            {"team": {"id": 1}, "rank": 4, "played": 10},
            {"team": {"id": 2}, "rank": 15, "played": 10},
        ]
    }
    assert signal_standings(standings, 1, 2) is not None
    assert signal_standings({"data": []}, 1, 2) is None

    ko = datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc)
    hist = [
        RichFinished(
            home_team_id=1,
            away_team_id=50,
            goals_home=1,
            goals_away=0,
            kickoff=ko - timedelta(hours=60),
        ),
        RichFinished(
            home_team_id=60,
            away_team_id=2,
            goals_home=0,
            goals_away=1,
            kickoff=ko - timedelta(days=3),
        ),
    ]
    fat = signal_fatigue(hist, 1, 2, kickoff=ko)
    assert fat is not None
    assert fatigue_flag_from_signal(fat) is True
    assert signal_fatigue(hist, 1, 2, kickoff=None) is None


def test_build_extra_signals_and_live_wire() -> None:
    built = build_extra_signals(
        home_team_id=1,
        away_team_id=2,
        finished=_hist(),
        h2h_rows=[
            {"home_team_id": 1, "away_team_id": 2, "goals_home": 1, "goals_away": 1},
            {"home_team_id": 2, "away_team_id": 1, "goals_home": 0, "goals_away": 2},
            {"home_team_id": 1, "away_team_id": 2, "goals_home": 0, "goals_away": 0},
        ],
        standings_payload={
            "data": [
                {"teamId": 1, "position": 3, "played": 8},
                {"teamId": 2, "position": 12, "played": 8},
            ]
        },
        kickoff="2026-09-20T15:00:00+00:00",
    )
    assert "form" in built.signals
    assert "streaks" in built.signals
    assert "matchup" in built.signals
    assert "club_h2h" in built.signals
    assert "standings" in built.signals
    assert set(built.signals).issubset(EXTRA_SIGNAL_KEYS)

    scored = score_live30(
        fixture_id="fx",
        minute=30,
        period="1H",
        score_home=0,
        score_away=0,
        stats=LiveVolumeStats(sot_home=2, sot_away=1, source_half="firstHalf"),
        extra_signals=built.signals,
        fatigue_flag=built.fatigue_flag,
        league_p_over05_given_00=0.75,
    )
    assert not scored.skipped
    assert "form" in scored.weights_used
    assert "club_h2h" in scored.weights_used
    assert "live_ratings" not in scored.weights_used


def test_omit_all_extractors_when_no_data() -> None:
    built = build_extra_signals(home_team_id=1, away_team_id=2, finished=[])
    assert built.signals == {}
    assert any(n.startswith("omit:") for n in built.notes)
