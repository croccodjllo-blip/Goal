"""Score product xG for live 0-0 @ ≈30′ 1H."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from goal_xg.features.prematch import PrematchPriors
from goal_xg.live30.stats import (
    LiveVolumeStats,
    formation_shift_label,
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
from goal_xg.model.calibration import DEFAULT_P_OVER05_GIVEN_00_AT_30
from goal_xg.model.dynamic_weights import apply_event_shifts, event_shift_deltas
from goal_xg.model.over05 import xg_score_from_p
from goal_xg.model.weights import MVP_OMIT_TERMS, omit_and_renorm

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
    """Map a non-negative count to [0,1] with mid≈neutral-ish for 0-0@30′."""
    if total is None:
        return None
    t = max(0.0, float(total))
    if t <= 0:
        return 0.32  # sterile volume → mild down vs residual baseline
    if t >= high:
        return 0.88
    if t <= mid:
        return 0.32 + (0.55 - 0.32) * (t / mid)
    return 0.55 + (0.88 - 0.55) * min(1.0, (t - mid) / (high - mid))


def _signal_possession(home_pct: float | None) -> float | None:
    if home_pct is None:
        return None
    # Possession alone is weak; near 50 → slight lift if not extreme dominance without volume.
    p = float(home_pct)
    if p > 1.5:  # already percent
        p = p
    elif p <= 1.0:
        p = p * 100.0
    # Distance from 50: mild U-shape inverted — balanced slightly preferred for goal chance
    # when still 0-0; extreme possession without conversion slightly lower.
    bal = 1.0 - min(1.0, abs(p - 50.0) / 35.0)
    return _clamp01(0.42 + 0.20 * bal)


def _signal_saves(saves: float | None, sot: float | None) -> float | None:
    if saves is None:
        return None
    base = _signal_count(saves, mid=2.0, high=5.0)
    if base is None:
        return None
    if sot is not None and sot >= 2 and saves >= 2:
        return _clamp01(base + 0.08)  # pressure without breakthrough
    return base


def _signal_def_yellows(total: float | None) -> float | None:
    if total is None:
        return None
    t = float(total)
    if t <= 0:
        return 0.48
    return _clamp01(0.52 + 0.08 * min(3.0, t))


def _signal_subs(stats: LiveVolumeStats) -> float | None:
    if stats.subs_total is None and not (
        stats.formation_home_ko or stats.formation_home_now
    ):
        return None
    shift = formation_shift_label(stats)
    subs = stats.subs_total if stats.subs_total is not None else 0
    if shift == "defensive":
        return 0.40  # early defensive change → slight ↑ 0-0 risk → ↓ xG
    if shift == "offensive":
        return 0.62
    if subs == 0:
        return 0.50  # neutral
    return 0.52


def _residual_time_signal(
    priors: PrematchPriors | None,
    *,
    league_p_over05_given_00: float | None = None,
) -> float:
    """P(Over 0.5 FT | 0-0 @ 30′) backbone — league calib or default."""
    base = (
        float(league_p_over05_given_00)
        if league_p_over05_given_00 is not None
        else DEFAULT_P_OVER05_GIVEN_00_AT_30
    )
    if priors is None:
        return _clamp01(base)
    # Shrink toward team prior (already Over 0.5 from KO), dampened for 0-0 condition.
    prior = _clamp01(priors.p_over05_prior)
    # Conditioning on still 0-0 lowers unconditional prior slightly.
    conditioned_prior = _clamp01(0.55 * prior + 0.45 * base)
    # More history → trust conditioned_prior / base blend via shrinkage.
    w = _clamp01(priors.shrinkage)
    return _clamp01((1.0 - w) * base + w * conditioned_prior)


def build_live_signals(
    stats: LiveVolumeStats,
    priors: PrematchPriors | None,
    *,
    extra_signals: Mapping[str, float] | None = None,
    league_p_over05_given_00: float | None = None,
    weather_signal: float | None = None,
) -> dict[str, float]:
    """Build [0,1] ensemble signals; omit missing keys (caller renorms)."""
    signals: dict[str, float] = {
        "residual_time": _residual_time_signal(
            priors, league_p_over05_given_00=league_p_over05_given_00
        ),
    }
    if priors is not None:
        signals["team_priors"] = _clamp01(priors.p_over05_prior)

    sot = _signal_count(stats.sot_total, mid=2.0, high=5.0)
    if sot is not None:
        signals["sot"] = sot
    att = _signal_count(stats.attacks_total, mid=25.0, high=55.0)
    if att is not None:
        signals["attacks"] = att
    cor = _signal_count(stats.corners_total, mid=3.0, high=7.0)
    if cor is not None:
        signals["corners"] = cor
    poss = _signal_possession(stats.possession_home)
    if poss is not None:
        signals["possession"] = poss
    sav = _signal_saves(stats.saves_total, stats.sot_total)
    if sav is not None:
        signals["saves"] = sav
    dy = _signal_def_yellows(stats.def_yellows_total)
    if dy is not None:
        signals["def_yellows"] = dy
    sub = _signal_subs(stats)
    if sub is not None:
        signals["subs_formation"] = sub

    if weather_signal is not None:
        signals["weather"] = _clamp01(weather_signal)

    if extra_signals:
        for k, v in extra_signals.items():
            if k in MVP_OMIT_TERMS:
                continue
            signals[k] = _clamp01(float(v))

    # Always omit MVP terms even if somehow provided.
    for k in MVP_OMIT_TERMS:
        signals.pop(k, None)
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
    league_p_over05_given_00: float | None = None,
    weather_signal: float | None = None,
    weather_adverse: bool = False,
    fatigue_flag: bool = False,
    omit: frozenset[str] | set[str] | None = None,
    apply_dynamic: bool = True,
) -> Live30Score:
    """Emit product xG for 0-0 @ ≈30′, or settled/skip outside the gate.

    Fail-closed: missing minute/score → skipped (no invented xG).
    Already scored in window → xG=100 settled.
    Outside 28–32′ 1H → skipped.
    """
    snap = snapshot_from_clock(
        fixture_id,
        minute=minute,
        period=period,
        score_home=score_home,
        score_away=score_away,
    )
    notes: list[str] = [
        f"window={LIVE_WINDOW_MIN}-{LIVE_WINDOW_MAX} target={LIVE_TARGET_MINUTE}",
        "omit+renorm: live_ratings, coach_h2h",
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

    # Settled Over 0.5 inside the window.
    if not snap.is_00:
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
            features={"settled": True},
        )

    live_stats = stats or LiveVolumeStats(notes=("no_live_stats",))
    signals = build_live_signals(
        live_stats,
        priors,
        extra_signals=extra_signals,
        league_p_over05_given_00=league_p_over05_given_00,
        weather_signal=weather_signal,
    )
    omit_set = set(MVP_OMIT_TERMS) | set(omit or ())
    available = set(signals)

    if apply_dynamic:
        deltas = event_shift_deltas(
            sot_total=live_stats.sot_total,
            attacks_total=live_stats.attacks_total,
            corners_total=live_stats.corners_total,
            saves_total=live_stats.saves_total,
            possession_home=live_stats.possession_home,
            def_yellows_total=live_stats.def_yellows_total,
            red_card=live_stats.has_red,
            subs_count=live_stats.subs_total,
            formation_shift=formation_shift_label(live_stats),
            weather_adverse=weather_adverse,
            fatigue_flag=fatigue_flag,
        )
        weights = apply_event_shifts(
            deltas=deltas, omit=omit_set, available=available
        )
        notes.append("dynamic_weights=on")
    else:
        weights = omit_and_renorm(omit=omit_set, available=available)
        notes.append("dynamic_weights=off")

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
            skip_reason="no_usable_weights",
            notes=tuple(notes),
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
        features={
            "sot_total": live_stats.sot_total,
            "attacks_total": live_stats.attacks_total,
            "corners_total": live_stats.corners_total,
            "possession_home": live_stats.possession_home,
            "saves_total": live_stats.saves_total,
            "def_yellows_total": live_stats.def_yellows_total,
            "subs_total": live_stats.subs_total,
            "source_half": live_stats.source_half,
            "formation_home": live_stats.formation_home_now or live_stats.formation_home_ko,
            "formation_away": live_stats.formation_away_now or live_stats.formation_away_ko,
        },
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
