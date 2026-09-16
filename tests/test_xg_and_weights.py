from __future__ import annotations

import pytest

from goal_xg.model.over05 import xg_score_from_p
from goal_xg.model.weights import (
    BASE_WEIGHTS,
    COMPONENT_LABELS_IT,
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


def test_base_weights_shot_index_sum_100() -> None:
    assert abs(sum(BASE_WEIGHTS.values()) - 100.0) < 1e-9
    assert set(BASE_WEIGHTS) == set(COMPONENT_LABELS_IT)
    assert len(BASE_WEIGHTS) == 9
    # Old criteria removed from the product index.
    for removed in (
        "residual_time",
        "team_priors",
        "form",
        "corners",
        "possession",
        "attacks",
        "saves",
        "live_ratings",
        "coach_h2h",
        "weather",
    ):
        assert removed not in BASE_WEIGHTS


def test_shot_index_weight_table() -> None:
    assert BASE_WEIGHTS["shot_xg"] == 20.0
    assert BASE_WEIGHTS["sot"] == 18.0
    assert BASE_WEIGHTS["xgot"] == 16.0
    assert BASE_WEIGHTS["shots_total"] == 14.0
    assert BASE_WEIGHTS["shots_inside_box"] == 8.0
    assert BASE_WEIGHTS["woodwork"] == 6.0
    assert BASE_WEIGHTS["shots_off"] == 6.0
    assert BASE_WEIGHTS["shots_blocked"] == 6.0
    assert BASE_WEIGHTS["shots_outside_box"] == 6.0


def test_mvp_omit_empty_for_shot_index() -> None:
    assert MVP_OMIT_TERMS == frozenset()
    w = mvp_renorm()
    assert set(w) == set(BASE_WEIGHTS)
    assert abs(sum(w.values()) - 100.0) < 1e-9


def test_omit_and_renorm_available_subset() -> None:
    w = omit_and_renorm(available={"sot", "shot_xg"})
    assert set(w) == {"sot", "shot_xg"}
    assert abs(sum(w.values()) - 100.0) < 1e-9
    assert w["sot"] == pytest.approx(18 / 38 * 100)
    assert w["shot_xg"] == pytest.approx(20 / 38 * 100)


def test_omit_everything_returns_empty() -> None:
    w = omit_and_renorm(omit=set(BASE_WEIGHTS))
    assert w == {}
