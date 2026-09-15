"""Base weights + omit/renorm (fail-closed).

Source of truth: docs/indice-xg-0-100.md (pesi variabili v1, Σ=100%).
MVP omit by default: live player ratings + true coach-vs-coach H2H.

Coach identity (football-data.org ``team.coach`` / ``/persons/{id}``) is a
**soft metadata feature** (``coach_identity``) — not a BASE_WEIGHTS term and
never a substitute for ``coach_h2h``. No dedicated coach-vs-coach endpoint →
keep ``coach_h2h`` omit + renorm.
"""

from __future__ import annotations

from typing import Mapping

# Locked base table (percent points). Keys are stable feature ids used in code.
BASE_WEIGHTS: dict[str, float] = {
    "residual_time": 14.0,
    "team_priors": 10.0,
    "sot": 10.0,
    "attacks": 8.0,
    "corners": 6.0,
    "possession": 5.0,
    "saves": 5.0,
    "live_ratings": 4.0,  # MVP omit + renorm
    "form": 4.0,
    "goal_minutes_last5": 4.0,
    "streaks": 4.0,
    "matchup": 4.0,
    "subs_formation": 3.0,
    "scoring_by_formation": 3.0,
    "standings": 3.0,
    "fatigue": 3.0,
    "def_yellows": 3.0,
    "club_h2h": 3.0,
    "coach_h2h": 2.0,  # true H2H: omit + renorm (no fd.org endpoint)
    "weather": 2.0,
}

# Soft feature id (metadata only; not in BASE_WEIGHTS / not scored).
COACH_IDENTITY_FEATURE = "coach_identity"

# Always omitted until a licensed live-ratings feed exists and true
# coach-vs-coach H2H is available (football-data.org has identity only).
MVP_OMIT_TERMS: frozenset[str] = frozenset({"live_ratings", "coach_h2h"})
COACH_H2H_TERM = "coach_h2h"

# Pre-match Phase A: only priors (+ optional form later). Live terms absent.
PHASE_A_PREMATCH_AVAILABLE: frozenset[str] = frozenset(
    {
        "team_priors",
        "form",
        "goal_minutes_last5",
        "streaks",
        "matchup",
        "scoring_by_formation",
        "standings",
        "fatigue",
        "club_h2h",
        "coach_h2h",
        "weather",
        # residual_time is live-conditional; unused in pure prematch path
    }
)


def mvp_omit() -> frozenset[str]:
    """MVP omit set: live ratings + true coach H2H (identity is soft-only)."""
    return MVP_OMIT_TERMS


def omit_and_renorm(
    base: Mapping[str, float] | None = None,
    *,
    omit: frozenset[str] | set[str] | None = None,
    available: frozenset[str] | set[str] | None = None,
) -> dict[str, float]:
    """Drop missing/omitted terms and renormalize remaining weights to Σ=100.

    Rules (locked):
    - Fail-closed: omitted / unavailable terms get weight 0 and do not participate.
    - If ``available`` is set, any key not in ``available`` is treated as missing.
    - If nothing remains, returns an empty dict (caller must not invent signal).
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
    """Convenience: omit live_ratings + coach_h2h then renorm."""
    return omit_and_renorm(base, omit=MVP_OMIT_TERMS)
