"""Base weights + omit/renorm (fail-closed) — shot-stats index only.

Product-xG live blend uses **only** these nine GOAL live shot criteria
(Alessandro 2026-09-16). Old residual_time / form / corners / possession /
priors / etc. are **not** in the weighted index.

Initial weights (Σ=100%), quality > raw volume:
  shot_xg 20 · xgot 16 · sot 18 · shots_total 14 · shots_inside_box 8 ·
  woodwork 6 · shots_off 6 · shots_blocked 6 · shots_outside_box 6
"""

from __future__ import annotations

from typing import Mapping

# Locked base table (percent points). Keys = stable feature ids.
BASE_WEIGHTS: dict[str, float] = {
    "shots_total": 14.0,  # Tiri totali
    "sot": 18.0,  # Tiri in porta
    "shot_xg": 20.0,  # Goal attesi (xG classico cumulato)
    "xgot": 16.0,  # Expected goals on target (xGOT)
    "woodwork": 6.0,  # Pali e traverse
    "shots_off": 6.0,  # Tiri fuori
    "shots_blocked": 6.0,  # Tiri respinti
    "shots_inside_box": 8.0,  # Tiri in area di rigore
    "shots_outside_box": 6.0,  # Tiri da fuori area
}

# Italian UI labels for fixture «Indice — componenti».
COMPONENT_LABELS_IT: dict[str, str] = {
    "shots_total": "Tiri totali",
    "sot": "Tiri in porta",
    "shot_xg": "Goal attesi (xG)",
    "xgot": "Expected goals on target (xGOT)",
    "woodwork": "Pali e traverse",
    "shots_off": "Tiri fuori",
    "shots_blocked": "Tiri respinti",
    "shots_inside_box": "Tiri in area di rigore",
    "shots_outside_box": "Tiri da fuori area",
}

# Soft metadata (not scored). Kept for import compatibility.
COACH_IDENTITY_FEATURE = "coach_identity"

# No MVP omit terms in the shot-only table (legacy ids removed from BASE_WEIGHTS).
MVP_OMIT_TERMS: frozenset[str] = frozenset()
COACH_H2H_TERM = "coach_h2h"

# Prematch Phase A is separate from the live shot index (uses team priors only).
PHASE_A_PREMATCH_AVAILABLE: frozenset[str] = frozenset({"team_priors"})


def mvp_omit() -> frozenset[str]:
    return MVP_OMIT_TERMS


def omit_and_renorm(
    base: Mapping[str, float] | None = None,
    *,
    omit: frozenset[str] | set[str] | None = None,
    available: frozenset[str] | set[str] | None = None,
) -> dict[str, float]:
    """Drop missing/omitted terms and renormalize remaining weights to Σ=100.

    Fail-closed: omitted / unavailable → weight 0. Empty if nothing remains.
    """
    weights = dict(base or BASE_WEIGHTS)
    drop: set[str] = set(omit or ())
    if available is not None:
        drop |= {k for k in weights if k not in available}

    kept = {k: float(v) for k, v in weights.items() if k not in drop and float(v) > 0}
    total = sum(kept.values())
    if total <= 0:
        return {}
    return {k: (v / total) * 100.0 for k, v in kept.items()}


def mvp_renorm(base: Mapping[str, float] | None = None) -> dict[str, float]:
    """Omit MVP terms (none in shot-only table) then renorm."""
    return omit_and_renorm(base, omit=MVP_OMIT_TERMS)
