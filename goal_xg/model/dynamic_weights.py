"""Dynamic weight shifts among shot-stat criteria only.

Caps unchanged: ±5 pp pre-renorm; live shot-volume terms ≤18% pre-renorm.
"""

from __future__ import annotations

from typing import Mapping

from goal_xg.model.weights import BASE_WEIGHTS, omit_and_renorm

SHIFT_CAP_PP = 5.0
LIVE_VOLUME_CAP_PP = 18.0

# Shot volume / quality terms subject to the live cap.
LIVE_VOLUME_TERMS: frozenset[str] = frozenset(
    {
        "shots_total",
        "sot",
        "shot_xg",
        "xgot",
        "woodwork",
        "shots_off",
        "shots_blocked",
        "shots_inside_box",
        "shots_outside_box",
    }
)


def _clamp_shift(delta: float) -> float:
    return max(-SHIFT_CAP_PP, min(SHIFT_CAP_PP, float(delta)))


def event_shift_deltas(
    *,
    shots_total: float | None = None,
    sot_total: float | None = None,
    shot_xg_total: float | None = None,
    xgot_total: float | None = None,
    woodwork_total: float | None = None,
    shots_off_total: float | None = None,
    shots_blocked_total: float | None = None,
    shots_inside_box_total: float | None = None,
    shots_outside_box_total: float | None = None,
    # Legacy kwargs ignored (compat with older callers).
    **_legacy: object,
) -> dict[str, float]:
    """Directional pp shifts from shot volume / quality @ ≈30′."""
    deltas: dict[str, float] = {}
    volume_parts: list[float] = []
    if sot_total is not None:
        volume_parts.append(min(1.0, float(sot_total) / 4.0))
    if shots_total is not None:
        volume_parts.append(min(1.0, float(shots_total) / 8.0))
    if shots_inside_box_total is not None:
        volume_parts.append(min(1.0, float(shots_inside_box_total) / 5.0))
    if shot_xg_total is not None:
        volume_parts.append(min(1.0, float(shot_xg_total) / 1.0))
    if xgot_total is not None:
        volume_parts.append(min(1.0, float(xgot_total) / 0.7))

    if volume_parts:
        pressure = sum(volume_parts) / len(volume_parts)
        if pressure >= 0.55:
            for k in ("sot", "shot_xg", "xgot", "shots_inside_box"):
                deltas[k] = deltas.get(k, 0.0) + 3.0 * pressure
            deltas["shots_outside_box"] = deltas.get("shots_outside_box", 0.0) - 1.5
            deltas["shots_off"] = deltas.get("shots_off", 0.0) - 1.0
        elif pressure <= 0.25:
            # Sterile → lean on totals / blocked / off-target if present.
            deltas["shots_total"] = deltas.get("shots_total", 0.0) + 2.0
            for k in ("sot", "shot_xg", "xgot", "shots_inside_box"):
                deltas[k] = deltas.get(k, 0.0) - 2.0

    if woodwork_total is not None and woodwork_total >= 1:
        deltas["woodwork"] = deltas.get("woodwork", 0.0) + 3.0
        deltas["sot"] = deltas.get("sot", 0.0) + 1.0

    if shots_blocked_total is not None and shots_blocked_total >= 2:
        deltas["shots_blocked"] = deltas.get("shots_blocked", 0.0) + 2.0

    if shots_off_total is not None and shots_off_total >= 4:
        deltas["shots_off"] = deltas.get("shots_off", 0.0) + 1.5

    return {k: _clamp_shift(v) for k, v in deltas.items()}


def apply_event_shifts(
    base: Mapping[str, float] | None = None,
    *,
    deltas: Mapping[str, float] | None = None,
    omit: frozenset[str] | set[str] | None = None,
    available: frozenset[str] | set[str] | None = None,
) -> dict[str, float]:
    """Apply capped shifts, clamp live terms, renorm to 100%."""
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
        shifted[k] = max(0.0, base_w + _clamp_shift(delta))

    for k in LIVE_VOLUME_TERMS:
        if k in shifted and shifted[k] > LIVE_VOLUME_CAP_PP:
            shifted[k] = LIVE_VOLUME_CAP_PP

    return omit_and_renorm(shifted, omit=omit_set, available=set(shifted))
