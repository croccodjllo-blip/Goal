from __future__ import annotations

import pytest

from goal_xg.model.over05 import xg_score_from_p
from goal_xg.model.weights import (
    BASE_WEIGHTS,
    MVP_OMIT_TERMS,
    mvp_renorm,
    omit_and_renorm,
)


@pytest.mark.parametrize(
    "p,expected",
    [
        (0.0, 0),
        (1.0, 100),
        (0.5, 50),
        (0.78, 78),
        (0.784, 78),
        (0.795, 80),
        (-0.1, 0),
        (1.2, 100),
    ],
)
def test_xg_score_from_p_clamp_and_round(p: float, expected: int) -> None:
    assert xg_score_from_p(p) == expected


def test_xg_score_canonical_examples() -> None:
    assert xg_score_from_p(0.78) == 78
    assert xg_score_from_p(0.849) == 85
    assert xg_score_from_p(0.851) == 85


def test_base_weights_sum_100() -> None:
    assert abs(sum(BASE_WEIGHTS.values()) - 100.0) < 1e-9


def test_mvp_omit_renorm() -> None:
    w = mvp_renorm()
    assert "live_ratings" not in w
    assert "coach_h2h" not in w
    assert abs(sum(w.values()) - 100.0) < 1e-9
    # Mass from omitted terms redistributed.
    omitted_mass = sum(BASE_WEIGHTS[k] for k in MVP_OMIT_TERMS)
    assert omitted_mass == 6.0
    assert w["team_priors"] > BASE_WEIGHTS["team_priors"]


def test_omit_and_renorm_available_subset() -> None:
    w = omit_and_renorm(available={"team_priors", "form"})
    assert set(w) == {"team_priors", "form"}
    assert abs(sum(w.values()) - 100.0) < 1e-9
    assert w["team_priors"] == pytest.approx(10 / 14 * 100)
    assert w["form"] == pytest.approx(4 / 14 * 100)


def test_omit_everything_returns_empty() -> None:
    w = omit_and_renorm(omit=set(BASE_WEIGHTS))
    assert w == {}
