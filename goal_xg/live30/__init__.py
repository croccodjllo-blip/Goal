"""Live @ ≈30′ 1H — Phase B product-xG for 0-0 Big-5.

Primary trigger (locked): clock ≈30′ of 1H (operative window 28–32′),
still 0-0. Product xG = round(100 × P(Over 0.5 FT | 0-0 @ 30′)).

WS-first for clock/score/events; REST statistics only when in-window 0-0.

Orchestration helpers live in ``goal_xg.live30.service`` (not imported here
to avoid circular imports with ``goal_ws``).
"""

from goal_xg.live30.score import (
    Live30Score,
    Live30Snapshot,
    build_live_signals,
    score_live30,
    score_live30_from_payloads,
    snapshot_from_clock,
)
from goal_xg.live30.stats import (
    LiveVolumeStats,
    formation_shift_label,
    merge_events_into_stats,
    merge_fill_shot_stats,
    parse_statistics_payload,
)
from goal_xg.live30.window import (
    LIVE_TARGET_MINUTE,
    LIVE_WINDOW_MAX,
    LIVE_WINDOW_MIN,
    in_live30_window,
    is_score_00,
    normalize_period,
    parse_minute,
)

__all__ = [
    "LIVE_TARGET_MINUTE",
    "LIVE_WINDOW_MAX",
    "LIVE_WINDOW_MIN",
    "Live30Score",
    "Live30Snapshot",
    "LiveVolumeStats",
    "build_live_signals",
    "formation_shift_label",
    "in_live30_window",
    "is_score_00",
    "merge_events_into_stats",
    "merge_fill_shot_stats",
    "normalize_period",
    "parse_minute",
    "parse_statistics_payload",
    "score_live30",
    "score_live30_from_payloads",
    "snapshot_from_clock",
]
