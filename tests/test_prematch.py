from __future__ import annotations

from goal_xg.features.prematch import FinishedFixture, compute_prematch_priors
from goal_xg.model.over05 import score_prematch


def _sample_history() -> list[FinishedFixture]:
    # Home team 1 scores often at home; away team 2 blanks often away.
    rows: list[FinishedFixture] = []
    for i in range(10):
        rows.append(
            FinishedFixture(
                home_team_id=1,
                away_team_id=100 + i,
                goals_home=2,
                goals_away=0,
                league_id=39,
            )
        )
        rows.append(
            FinishedFixture(
                home_team_id=200 + i,
                away_team_id=2,
                goals_home=1,
                goals_away=0,
                league_id=39,
            )
        )
    # A few 0-0 for league baseline.
    for i in range(2):
        rows.append(
            FinishedFixture(
                home_team_id=300 + i,
                away_team_id=400 + i,
                goals_home=0,
                goals_away=0,
                league_id=39,
            )
        )
    return rows


def test_compute_prematch_priors_and_score() -> None:
    priors = compute_prematch_priors(
        home_team_id=1,
        away_team_id=2,
        finished=_sample_history(),
        fixture_id=999,
    )
    assert priors.n_home == 10
    assert priors.n_away == 10
    assert 0.0 < priors.p_over05_prior < 1.0
    assert abs(priors.p_over05_prior + priors.p_00_prior - 1.0) < 1e-9

    scored = score_prematch(priors, fixture_id=999)
    assert scored.context == "prematch"
    assert scored.xg_score == round(100 * scored.p_over05)
    assert 0 <= scored.xg_score <= 100
    assert "team_priors" in scored.weights_used
    assert "live_ratings" not in scored.weights_used
    assert "coach_h2h" not in scored.weights_used
