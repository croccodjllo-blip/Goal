"""Dynamic weight shifts from live events @ ≈30′ (locked caps).

Source: docs/indice-xg-0-100.md § Pesi dinamici da eventi.
"""

from __future__ import annotations

from typing import Mapping

from goal_xg.model.weights import BASE_WEIGHTS, omit_and_renorm

# Absolute max shift vs base (percentage points) before renorm.
SHIFT_CAP_PP = 5.0
# After shift+renorm, no live-volume term may exceed this.
LIVE_VOLUME_CAP_PP = 18.0

LIVE_VOLUME_TERMS: frozenset[str] = frozenset(
    {"sot", "attacks", "corners", "possession", "saves", "def_yellows"}
)


def _clamp_shift(delta: float) -> float:
    return max(-SHIFT_CAP_PP, min(SHIFT_CAP_PP, float(delta)))


def event_shift_deltas(
    *,
    sot_total: float | None = None,
    attacks_total: float | None = None,
    corners_total: float | None = None,
    saves_total: float | None = None,
    possession_home: float | None = None,
    def_yellows_total: float | None = None,
    red_card: bool = False,
    subs_count: int | None = None,
    formation_shift: str | None = None,  # "defensive" | "offensive" | None
    weather_adverse: bool = False,
    fatigue_flag: bool = False,
) -> dict[str, float]:
    """Directional absolute pp shifts keyed by weight term (pre-cap)."""
    deltas: dict[str, float] = {}

    # Pressure score from available volume (counts at ~30′ 0-0).
    volume_parts: list[float] = []
    if sot_total is not None:
        volume_parts.append(min(1.0, float(sot_total) / 4.0))
    if attacks_total is not None:
        volume_parts.append(min(1.0, float(attacks_total) / 40.0))
    if corners_total is not None:
        volume_parts.append(min(1.0, float(corners_total) / 6.0))
    if saves_total is not None:
        volume_parts.append(min(1.0, float(saves_total) / 4.0))

    if volume_parts:
        pressure = sum(volume_parts) / len(volume_parts)
        if pressure >= 0.55:
            # High pressure still 0-0 → trust live volume; dial down priors/form.
            for k in ("sot", "attacks", "corners", "saves"):
                deltas[k] = deltas.get(k, 0.0) + 3.0 * pressure
            deltas["team_priors"] = deltas.get("team_priors", 0.0) - 2.0
            deltas["form"] = deltas.get("form", 0.0) - 1.5
            deltas["residual_time"] = deltas.get("residual_time", 0.0) - 1.0
        elif pressure <= 0.25:
            # Sterile 0-0 → lean on residual + priors + streaks.
            deltas["residual_time"] = deltas.get("residual_time", 0.0) + 3.0
            deltas["team_priors"] = deltas.get("team_priors", 0.0) + 2.0
            deltas["streaks"] = deltas.get("streaks", 0.0) + 1.5
            for k in ("sot", "attacks", "corners", "possession", "saves"):
                deltas[k] = deltas.get(k, 0.0) - 2.0

    if def_yellows_total is not None and def_yellows_total >= 1:
        deltas["def_yellows"] = deltas.get("def_yellows", 0.0) + 2.0
        deltas["possession"] = deltas.get("possession", 0.0) - 1.0

    if red_card:
        deltas["def_yellows"] = deltas.get("def_yellows", 0.0) + 3.0
        deltas["residual_time"] = deltas.get("residual_time", 0.0) + 2.0
        deltas["possession"] = deltas.get("possession", 0.0) - 2.0

    if formation_shift == "defensive" or (subs_count is not None and subs_count >= 1 and formation_shift == "defensive"):
        deltas["subs_formation"] = deltas.get("subs_formation", 0.0) + 3.0
        deltas["attacks"] = deltas.get("attacks", 0.0) - 1.5
    elif formation_shift == "offensive":
        deltas["subs_formation"] = deltas.get("subs_formation", 0.0) + 2.0
        deltas["attacks"] = deltas.get("attacks", 0.0) + 2.0
        deltas["sot"] = deltas.get("sot", 0.0) + 1.5
    elif subs_count is not None and subs_count >= 1:
        deltas["subs_formation"] = deltas.get("subs_formation", 0.0) + 1.0

    if weather_adverse:
        deltas["weather"] = deltas.get("weather", 0.0) + 2.0

    if fatigue_flag and volume_parts and (sum(volume_parts) / len(volume_parts)) <= 0.35:
        deltas["fatigue"] = deltas.get("fatigue", 0.0) + 2.0

    # Cap each shift at ±5 pp.
    return {k: _clamp_shift(v) for k, v in deltas.items()}


def apply_event_shifts(
    base: Mapping[str, float] | None = None,
    *,
    deltas: Mapping[str, float] | None = None,
    omit: frozenset[str] | set[str] | None = None,
    available: frozenset[str] | set[str] | None = None,
) -> dict[str, float]:
    """Apply capped shifts to base weights, clamp live volume, then renorm to 100%.

    Absent/omitted terms stay 0 and do not receive shifts (fail-closed).
    """
    weights = dict(base or BASE_WEIGHTS)
    omit_set = set(omit or ())
    present = {
        k
        for k, v in weights.items()
        if float(v) > 0
        and k not in omit_set
        and (available is None or k in available)
    }

    shifted: dict[str, float] = {}
    for k in present:
        base_w = float(weights[k])
        delta = float((deltas or {}).get(k, 0.0)) if k in (deltas or {}) else 0.0
        # Only shift terms that are present.
        shifted[k] = max(0.0, base_w + _clamp_shift(delta))

    # Soft clamp live-volume terms before renorm (absolute pp on pre-renorm scale).
    for k in LIVE_VOLUME_TERMS:
        if k in shifted and shifted[k] > LIVE_VOLUME_CAP_PP:
            shifted[k] = LIVE_VOLUME_CAP_PP

    # Renorm via omit_and_renorm path (available = present keys only).
    return omit_and_renorm(shifted, omit=omit_set, available=set(shifted))
