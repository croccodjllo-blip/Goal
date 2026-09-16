"""Tests for league calibration P(Over 0.5 FT | 0-0 @ 30′)."""

from __future__ import annotations

from goal_xg.model.calibration import (
    DEFAULT_P_OVER05_GIVEN_00_AT_30,
    Labeled00At30,
    calibrate_leagues,
    estimate_p_over05_given_00_at_30,
    resolve_league_p,
)


def test_estimate_fail_closed_empty() -> None:
    assert estimate_p_over05_given_00_at_30([]) is None


def test_estimate_shrinks_toward_prior() -> None:
    # 1/1 raw=1.0 should shrink hard toward 0.78 with small N.
    calib = estimate_p_over05_given_00_at_30(
        [True], prior=DEFAULT_P_OVER05_GIVEN_00_AT_30, shrink_k=20.0, league_id="SA"
    )
    assert calib is not None
    assert calib.raw_rate == 1.0
    assert calib.n == 1
    assert DEFAULT_P_OVER05_GIVEN_00_AT_30 < calib.shrunk_rate < 1.0
    # Large sample close to raw.
    big = estimate_p_over05_given_00_at_30(
        [True] * 80 + [False] * 20, prior=0.78, shrink_k=20.0
    )
    assert big is not None
    assert abs(big.shrunk_rate - 0.8) < 0.05


def test_calibrate_leagues_and_resolve() -> None:
    rows = [
        Labeled00At30(league_id="135", over05_ft=True),
        Labeled00At30(league_id="135", over05_ft=True),
        Labeled00At30(league_id="135", over05_ft=False),
        Labeled00At30(league_id="39", over05_ft=True),
    ]
    table = calibrate_leagues(rows)
    assert set(table) == {"135", "39"}
    assert table["135"].n == 3
    p = resolve_league_p(table, "135", allow_prior_fallback=False)
    assert p is not None
    assert 0.0 < p < 1.0
    assert resolve_league_p(table, "missing", allow_prior_fallback=False) is None
    assert resolve_league_p(table, "missing", allow_prior_fallback=True) == 0.78


def test_calibrate_from_mapping_was_00_gate() -> None:
    rows = [
        {"league_id": "SA", "over05_ft": True, "was_00_at_30": True},
        {"league_id": "SA", "over05_ft": False, "was_00_at_30": False},  # skipped
        {"league_id": "SA", "goals_home": 2, "goals_away": 0, "was_00_at_30": True},
    ]
    table = calibrate_leagues(rows)
    assert table["SA"].n == 2
    assert table["SA"].n_over05_ft == 2
