"""League calibration for P(Over 0.5 FT | 0-0 @ 30′).

Fail-closed: empty sample → ``None`` (no invented per-league rate).
Shrinkage pulls the raw rate toward ``DEFAULT_P_OVER05_GIVEN_00_AT_30``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from goal_xg.features.prematch import shrink_rate

# Global empirical-ish prior (Big-5 average) when no league sample exists.
DEFAULT_P_OVER05_GIVEN_00_AT_30 = 0.78
DEFAULT_SHRINK_K = 20.0


@dataclass(frozen=True)
class League00At30Calib:
    """Shrunk P(Over 0.5 FT | 0-0 @ ≈30′) for one league."""

    league_id: str
    n: int
    n_over05_ft: int
    raw_rate: float
    shrunk_rate: float
    prior: float
    shrink_k: float

    @property
    def p(self) -> float:
        return self.shrunk_rate


@dataclass(frozen=True)
class Labeled00At30:
    """One historical case: still 0-0 at ≈30′, known FT Over 0.5 outcome."""

    league_id: str | int
    over05_ft: bool
    fixture_id: str | int | None = None


def estimate_p_over05_given_00_at_30(
    outcomes: Sequence[bool],
    *,
    prior: float = DEFAULT_P_OVER05_GIVEN_00_AT_30,
    shrink_k: float = DEFAULT_SHRINK_K,
    league_id: str | int | None = None,
) -> League00At30Calib | None:
    """Estimate shrunk rate from FT Over-0.5 labels among 0-0@30′ cases.

    Returns ``None`` if ``outcomes`` is empty (fail-closed — do not invent).
    """
    n = len(outcomes)
    if n <= 0:
        return None
    n_over = sum(1 for y in outcomes if y)
    raw = n_over / n
    prior_c = max(0.0, min(1.0, float(prior)))
    shrunk = shrink_rate(raw, prior_c, n, k=float(shrink_k))
    lid = "unknown" if league_id is None else str(league_id)
    return League00At30Calib(
        league_id=lid,
        n=n,
        n_over05_ft=n_over,
        raw_rate=raw,
        shrunk_rate=shrunk,
        prior=prior_c,
        shrink_k=float(shrink_k),
    )


def calibrate_leagues(
    rows: Iterable[Labeled00At30 | Mapping[str, Any]],
    *,
    prior: float = DEFAULT_P_OVER05_GIVEN_00_AT_30,
    shrink_k: float = DEFAULT_SHRINK_K,
) -> dict[str, League00At30Calib]:
    """Group labeled 0-0@30′ rows by league and shrink each rate.

    Leagues with zero rows are omitted (fail-closed per league).
    """
    by_league: dict[str, list[bool]] = {}
    for row in rows:
        if isinstance(row, Labeled00At30):
            lid = str(row.league_id)
            y = bool(row.over05_ft)
        elif isinstance(row, Mapping):
            raw_lid = row.get("league_id") or row.get("leagueId")
            if raw_lid is None:
                continue
            lid = str(raw_lid)
            if "over05_ft" in row:
                y = bool(row["over05_ft"])
            elif "ft_over05" in row:
                y = bool(row["ft_over05"])
            else:
                gh = row.get("goals_home")
                ga = row.get("goals_away")
                if gh is None or ga is None:
                    continue
                try:
                    y = (int(gh) + int(ga)) >= 1
                except (TypeError, ValueError):
                    continue
            # Optional gate: only keep rows marked as 0-0 at 30′.
            if "was_00_at_30" in row and not row["was_00_at_30"]:
                continue
        else:
            continue
        by_league.setdefault(lid, []).append(y)

    out: dict[str, League00At30Calib] = {}
    for lid, outcomes in by_league.items():
        calib = estimate_p_over05_given_00_at_30(
            outcomes, prior=prior, shrink_k=shrink_k, league_id=lid
        )
        if calib is not None:
            out[lid] = calib
    return out


def resolve_league_p(
    table: Mapping[str, League00At30Calib] | None,
    league_id: str | int | None,
    *,
    prior: float = DEFAULT_P_OVER05_GIVEN_00_AT_30,
    allow_prior_fallback: bool = True,
) -> float | None:
    """Look up shrunk league rate; optionally fall back to global prior.

    Fail-closed when ``allow_prior_fallback=False`` and league missing/empty.
    """
    if league_id is not None and table:
        hit = table.get(str(league_id))
        if hit is not None and hit.n > 0:
            return float(hit.shrunk_rate)
    if allow_prior_fallback:
        return max(0.0, min(1.0, float(prior)))
    return None
