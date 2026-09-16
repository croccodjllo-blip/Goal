"""Feature builders (pre-match + live extractors)."""

from goal_xg.features.extractors import (
    EXTRA_SIGNAL_KEYS,
    ExtraSignalsResult,
    build_extra_signals,
    raw_goals_scored_last5_ha,
    raw_standings,
    signal_club_h2h,
    signal_fatigue,
    signal_form,
    signal_goal_minutes_last5,
    signal_goals_scored_last5_ha,
    signal_matchup,
    signal_standings,
    signal_streaks,
)
from goal_xg.features.prematch import PrematchPriors, compute_prematch_priors

__all__ = [
    "EXTRA_SIGNAL_KEYS",
    "ExtraSignalsResult",
    "PrematchPriors",
    "build_extra_signals",
    "compute_prematch_priors",
    "raw_goals_scored_last5_ha",
    "raw_standings",
    "signal_club_h2h",
    "signal_fatigue",
    "signal_form",
    "signal_goal_minutes_last5",
    "signal_goals_scored_last5_ha",
    "signal_matchup",
    "signal_standings",
    "signal_streaks",
]
