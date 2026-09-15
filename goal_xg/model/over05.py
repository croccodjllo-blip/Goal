"""Over 0.5 FT model + product-xG score (0–100).

``xg_score = round(100 * p)`` clamped to [0, 100].
Not classic shot-level xG.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from goal_xg.features.prematch import PrematchPriors
from goal_xg.model.weights import (
    MVP_OMIT_TERMS,
    PHASE_A_PREMATCH_AVAILABLE,
    omit_and_renorm,
)


def xg_score_from_p(p: float) -> int:
    """Map probability in [0, 1] to product xG integer 0–100."""
    if p != p:  # NaN
        raise ValueError("p is NaN")
    clamped = max(0.0, min(1.0, float(p)))
    return int(round(100.0 * clamped))


@dataclass(frozen=True)
class PrematchScore:
    fixture_id: int | str | None
    p_over05: float
    p_00: float
    xg_score: int
    context: str = "prematch"
    weights_used: Mapping[str, float] = field(default_factory=dict)
    features: Mapping[str, float] = field(default_factory=dict)
    notes: tuple[str, ...] = ()


def _blend(values: Mapping[str, float], weights: Mapping[str, float]) -> float:
    """Weighted average of feature signals in [0, 1]; weights are percent points."""
    num = 0.0
    den = 0.0
    for key, w in weights.items():
        if key not in values:
            continue
        num += float(values[key]) * float(w)
        den += float(w)
    if den <= 0:
        raise ValueError("no overlapping features/weights to blend")
    return num / den


def score_prematch(
    priors: PrematchPriors,
    *,
    fixture_id: int | str | None = None,
    extra_signals: Mapping[str, float] | None = None,
    omit: frozenset[str] | set[str] | None = None,
) -> PrematchScore:
    """Phase A pre-match: P(Over 0.5) from shrunk priors (+ optional signals).

    Ensemble sketch:
    - Primary signal ``team_priors`` from combined home/away O0.5 / blank rates.
    - Optional keys in ``extra_signals`` (form, streaks, …) if present.
    - MVP omits live_ratings + coach_h2h; live volume terms unavailable → renorm.
    """
    omit_set = set(MVP_OMIT_TERMS) | set(omit or ())

    signals: dict[str, float] = {"team_priors": priors.p_over05_prior}
    if extra_signals:
        for k, v in extra_signals.items():
            if k in omit_set:
                continue
            signals[k] = max(0.0, min(1.0, float(v)))

    # Renorm only over terms we can actually score (fail-closed for the rest).
    available = set(signals) & (PHASE_A_PREMATCH_AVAILABLE | {"team_priors"})
    available |= {"team_priors"}
    usable = omit_and_renorm(omit=omit_set, available=available)
    if not usable:
        usable = omit_and_renorm(omit=omit_set, available={"team_priors"})

    p = _blend(signals, usable)
    p = max(0.0, min(1.0, p))
    score = xg_score_from_p(p)
    notes = (
        "context=prematch (not conditioned on 0-0 @ 30′)",
        f"shrinkage={priors.shrinkage:.3f}",
        f"n_home={priors.n_home} n_away={priors.n_away}",
    )
    return PrematchScore(
        fixture_id=fixture_id if fixture_id is not None else priors.fixture_id,
        p_over05=p,
        p_00=1.0 - p,
        xg_score=score,
        context="prematch",
        weights_used=usable,
        features={
            "home_pct_over05": priors.home_pct_over05,
            "away_pct_over05": priors.away_pct_over05,
            "home_fts": priors.home_fts,
            "away_fts": priors.away_fts,
            "home_cs": priors.home_cs,
            "away_cs": priors.away_cs,
            "home_gf_avg": priors.home_gf_avg,
            "home_ga_avg": priors.home_ga_avg,
            "away_gf_avg": priors.away_gf_avg,
            "away_ga_avg": priors.away_ga_avg,
            "league_pct_over05": priors.league_pct_over05,
            "p_over05_prior": priors.p_over05_prior,
        },
        notes=notes,
    )
