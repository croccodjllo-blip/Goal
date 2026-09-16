"""Over 0.5 FT model + product-xG score (0–100).

``xg_score = round(100 * p)`` clamped to [0, 100].
Not classic shot-level xG.

Live index (Phase B) uses shot-stats ``BASE_WEIGHTS`` only.
Prematch (Phase A) still uses team priors directly — not the shot table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from goal_xg.features.prematch import PrematchPriors


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


def score_prematch(
    priors: PrematchPriors,
    *,
    fixture_id: int | str | None = None,
    extra_signals: Mapping[str, float] | None = None,
    omit: frozenset[str] | set[str] | None = None,
) -> PrematchScore:
    """Phase A pre-match: P(Over 0.5) from shrunk team priors.

    Live shot-index weights do not apply here. Optional ``extra_signals`` are
    ignored unless they somehow redefine the prior (kept for API compat).
    """
    _ = (extra_signals, omit)
    p = max(0.0, min(1.0, float(priors.p_over05_prior)))
    score = xg_score_from_p(p)
    notes = (
        "context=prematch (not conditioned on 0-0 @ 30′)",
        "blend=team_priors_only (shot index is live-only)",
        f"shrinkage={priors.shrinkage:.3f}",
        f"n_home={priors.n_home} n_away={priors.n_away}",
    )
    return PrematchScore(
        fixture_id=fixture_id if fixture_id is not None else priors.fixture_id,
        p_over05=p,
        p_00=1.0 - p,
        xg_score=score,
        context="prematch",
        weights_used={"team_priors": 100.0},
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
