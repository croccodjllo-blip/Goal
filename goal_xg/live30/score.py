"""Score product xG for live 0-0 @ ≈30′ 1H — shot-stats ensemble only."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from goal_xg.features.prematch import PrematchPriors
from goal_xg.live30.stats import (
    LiveVolumeStats,
    merge_events_into_stats,
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
from goal_xg.model.dynamic_weights import apply_event_shifts, event_shift_deltas
from goal_xg.model.over05 import xg_score_from_p
from goal_xg.model.weights import BASE_WEIGHTS, omit_and_renorm


@dataclass(frozen=True)
class Live30Snapshot:
    """Clock/score gate for the Phase B emit."""

    fixture_id: int | str
    minute: int | None
    period: str
    score_home: int | None
    score_away: int | None
    is_00: bool
    in_window: bool


@dataclass(frozen=True)
class Live30Score:
    fixture_id: int | str
    minute: int | None
    period: str
    score_home: int
    score_away: int
    is_00: bool
    in_window: bool
    p_over05_ft: float
    p_00_ft: float
    xg_score: int
    context: str = "live30"
    settled: bool = False
    weights_used: Mapping[str, float] = field(default_factory=dict)
    signals: Mapping[str, float] = field(default_factory=dict)
    features: Mapping[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    skipped: bool = False
    skip_reason: str | None = None


def _blend(values: Mapping[str, float], weights: Mapping[str, float]) -> float:
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


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _signal_count(total: float | None, *, mid: float, high: float) -> float | None:
    """Map a non-negative count to [0,1] with mid≈neutral for 0-0@30′."""
    if total is None:
        return None
    t = max(0.0, float(total))
    if t <= 0:
        return 0.28  # sterile → mild down
    if t >= high:
        return 0.90
    if t <= mid:
        return 0.28 + (0.55 - 0.28) * (t / mid)
    return 0.55 + (0.90 - 0.55) * min(1.0, (t - mid) / (high - mid))


def _signal_xg_sum(total: float | None, *, mid: float, high: float) -> float | None:
    """Map cumulative classic xG / xGOT (already probability-ish) to [0,1]."""
    if total is None:
        return None
    t = max(0.0, float(total))
    if t <= 0:
        return 0.30
    if t >= high:
        return 0.92
    if t <= mid:
        return 0.30 + (0.58 - 0.30) * (t / mid)
    return 0.58 + (0.92 - 0.58) * min(1.0, (t - mid) / (high - mid))


def build_live_signals(
    stats: LiveVolumeStats,
    priors: PrematchPriors | None = None,
    *,
    extra_signals: Mapping[str, float] | None = None,
    league_p_over05_given_00: float | None = None,
    weather_signal: float | None = None,
) -> dict[str, float]:
    """Build [0,1] signals for shot criteria + optional BASE_WEIGHTS extras; omit missing."""
    # Prematch/weather/league prior are not weighted index criteria
    # unless present in BASE_WEIGHTS and passed via extra_signals.
    _ = (priors, league_p_over05_given_00, weather_signal)

    signals: dict[str, float] = {}
    mapping: list[tuple[str, float | None]] = [
        ("shots_total", _signal_count(stats.shots_total, mid=5.0, high=12.0)),
        ("sot", _signal_count(stats.sot_total, mid=2.0, high=5.0)),
        ("shot_xg", _signal_xg_sum(stats.shot_xg_total, mid=0.45, high=1.20)),
        ("xgot", _signal_xg_sum(stats.xgot_total, mid=0.30, high=0.90)),
        ("woodwork", _signal_count(stats.woodwork_total, mid=1.0, high=2.0)),
        ("shots_off", _signal_count(stats.shots_off_total, mid=3.0, high=7.0)),
        ("shots_blocked", _signal_count(stats.shots_blocked_total, mid=2.0, high=5.0)),
        (
            "shots_inside_box",
            _signal_count(stats.shots_inside_box_total, mid=3.0, high=7.0),
        ),
        (
            "shots_outside_box",
            _signal_count(stats.shots_outside_box_total, mid=2.0, high=5.0),
        ),
    ]
    for key, sig in mapping:
        if sig is not None and key in BASE_WEIGHTS:
            signals[key] = _clamp01(sig)

    if extra_signals:
        for k, v in extra_signals.items():
            if k not in BASE_WEIGHTS:
                continue
            signals[k] = _clamp01(float(v))

    return signals


def snapshot_from_clock(
    fixture_id: int | str,
    *,
    minute: int | None,
    period: str | None,
    score_home: int | None,
    score_away: int | None,
) -> Live30Snapshot:
    per = normalize_period(period)
    return Live30Snapshot(
        fixture_id=fixture_id,
        minute=minute,
        period=per,
        score_home=score_home,
        score_away=score_away,
        is_00=is_score_00(score_home, score_away),
        in_window=in_live30_window(minute, period=per),
    )


def shot_features_from_stats(stats: LiveVolumeStats) -> dict[str, Any]:
    """Raw shot totals for features / fixture UI (None-preserving)."""
    return {
        "shots_total": stats.shots_total,
        "sot_total": stats.sot_total,
        "shot_xg_total": stats.shot_xg_total,
        "xgot_total": stats.xgot_total,
        "woodwork_total": stats.woodwork_total,
        "shots_off_total": stats.shots_off_total,
        "shots_blocked_total": stats.shots_blocked_total,
        "shots_inside_box_total": stats.shots_inside_box_total,
        "shots_outside_box_total": stats.shots_outside_box_total,
        "source_half": stats.source_half,
    }


def _shot_features(stats: LiveVolumeStats) -> dict[str, Any]:
    return shot_features_from_stats(stats)


def score_live30(
    *,
    fixture_id: int | str,
    minute: int | None,
    period: str | None,
    score_home: int | None,
    score_away: int | None,
    stats: LiveVolumeStats | None = None,
    priors: PrematchPriors | None = None,
    extra_signals: Mapping[str, float] | None = None,
    extra_features: Mapping[str, Any] | None = None,
    league_p_over05_given_00: float | None = None,
    weather_signal: float | None = None,
    weather_adverse: bool = False,
    fatigue_flag: bool = False,
    omit: frozenset[str] | set[str] | None = None,
    apply_dynamic: bool = True,
) -> Live30Score:
    """Emit product xG for 0-0 @ ≈30′ from shot stats, or settled/skip.

    Fail-closed: missing minute/score → skipped.
    Already scored in window → xG=100 settled.
    Outside 28–32′ 1H → skipped.
    No parseable shot criteria → skipped (no invented prior).
    """
    _ = (weather_adverse, fatigue_flag)  # not in shot-index blend
    snap = snapshot_from_clock(
        fixture_id,
        minute=minute,
        period=period,
        score_home=score_home,
        score_away=score_away,
    )
    notes: list[str] = [
        f"window={LIVE_WINDOW_MIN}-{LIVE_WINDOW_MAX} target={LIVE_TARGET_MINUTE}",
        "index=shot_stats_v2 (11 criteria: 9 shots + goals_scored_last5_ha + standings)",
    ]

    if snap.minute is None or snap.score_home is None or snap.score_away is None:
        return Live30Score(
            fixture_id=fixture_id,
            minute=snap.minute,
            period=snap.period,
            score_home=int(snap.score_home or 0),
            score_away=int(snap.score_away or 0),
            is_00=snap.is_00,
            in_window=snap.in_window,
            p_over05_ft=0.0,
            p_00_ft=1.0,
            xg_score=0,
            skipped=True,
            skip_reason="missing_minute_or_score",
            notes=tuple(notes + ["fail-closed: missing clock/score"]),
        )

    if not snap.in_window:
        return Live30Score(
            fixture_id=fixture_id,
            minute=snap.minute,
            period=snap.period,
            score_home=int(snap.score_home),
            score_away=int(snap.score_away),
            is_00=snap.is_00,
            in_window=False,
            p_over05_ft=0.0,
            p_00_ft=1.0,
            xg_score=0,
            skipped=True,
            skip_reason="outside_live30_window",
            notes=tuple(notes),
        )

    if not snap.is_00:
        # Settled: Over 0.5 already true. Do not invent component inputs.
        settled_features: dict[str, Any] = {"settled": True}
        if extra_features:
            for k, v in extra_features.items():
                if v is not None and v != "":
                    settled_features[str(k)] = v
        return Live30Score(
            fixture_id=fixture_id,
            minute=snap.minute,
            period=snap.period,
            score_home=int(snap.score_home),
            score_away=int(snap.score_away),
            is_00=False,
            in_window=True,
            p_over05_ft=1.0,
            p_00_ft=0.0,
            xg_score=100,
            settled=True,
            notes=tuple(notes + ["settled: already ≥1 goal @ ~30′"]),
            features=settled_features,
        )

    live_stats = stats or LiveVolumeStats(notes=("no_live_stats",))
    signals = build_live_signals(
        live_stats,
        priors,
        extra_signals=extra_signals,
        league_p_over05_given_00=league_p_over05_given_00,
        weather_signal=weather_signal,
    )
    omit_set = set(omit or ())
    available = set(signals)

    if apply_dynamic:
        deltas = event_shift_deltas(
            shots_total=live_stats.shots_total,
            sot_total=live_stats.sot_total,
            shot_xg_total=live_stats.shot_xg_total,
            xgot_total=live_stats.xgot_total,
            woodwork_total=live_stats.woodwork_total,
            shots_off_total=live_stats.shots_off_total,
            shots_blocked_total=live_stats.shots_blocked_total,
            shots_inside_box_total=live_stats.shots_inside_box_total,
            shots_outside_box_total=live_stats.shots_outside_box_total,
        )
        weights = apply_event_shifts(
            deltas=deltas, omit=omit_set, available=available
        )
        notes.append("dynamic_weights=on")
    else:
        weights = omit_and_renorm(omit=omit_set, available=available)
        notes.append("dynamic_weights=off")

    features = _shot_features(live_stats)
    if extra_features:
        for k, v in extra_features.items():
            if v is not None and v != "":
                features[str(k)] = v

    if not weights:
        return Live30Score(
            fixture_id=fixture_id,
            minute=snap.minute,
            period=snap.period,
            score_home=0,
            score_away=0,
            is_00=True,
            in_window=True,
            p_over05_ft=0.0,
            p_00_ft=1.0,
            xg_score=0,
            skipped=True,
            skip_reason="no_usable_shot_stats",
            features=features,
            notes=tuple(notes + ["fail-closed: no shot criteria available"]),
        )

    p = _clamp01(_blend(signals, weights))
    xg = xg_score_from_p(p)
    return Live30Score(
        fixture_id=fixture_id,
        minute=snap.minute,
        period=snap.period,
        score_home=0,
        score_away=0,
        is_00=True,
        in_window=True,
        p_over05_ft=p,
        p_00_ft=1.0 - p,
        xg_score=xg,
        weights_used=weights,
        signals=signals,
        features=features,
        notes=tuple(notes),
    )


def score_live30_from_payloads(
    *,
    fixture_id: int | str,
    clock: Mapping[str, Any],
    statistics_payload: Any | None = None,
    cards: Any | None = None,
    substitutions: Any | None = None,
    lineups: Any | None = None,
    priors: PrematchPriors | None = None,
    **kwargs: Any,
) -> Live30Score:
    """Convenience: parse clock + optional REST/WS payloads then score."""
    minute = parse_minute(
        clock.get("minute")
        if clock.get("minute") is not None
        else clock.get("elapsed") or clock.get("matchElapsed") or clock.get("match_status")
    )
    period = clock.get("period") or clock.get("matchPeriod") or "1H"
    home = clock.get("home_score")
    if home is None:
        home = clock.get("homeScore") or clock.get("score_home")
    away = clock.get("away_score")
    if away is None:
        away = clock.get("awayScore") or clock.get("score_away")
    try:
        score_home = int(home) if home is not None and str(home) != "" else None
    except (TypeError, ValueError):
        score_home = None
    try:
        score_away = int(away) if away is not None and str(away) != "" else None
    except (TypeError, ValueError):
        score_away = None

    stats = (
        parse_statistics_payload(statistics_payload)
        if statistics_payload is not None
        else LiveVolumeStats()
    )
    stats = merge_events_into_stats(
        stats,
        cards=cards if isinstance(cards, list) else None,
        substitutions=substitutions,
        lineups=lineups if isinstance(lineups, dict) else None,
    )
    return score_live30(
        fixture_id=fixture_id,
        minute=minute,
        period=str(period) if period is not None else None,
        score_home=score_home,
        score_away=score_away,
        stats=stats,
        priors=priors,
        **kwargs,
    )
