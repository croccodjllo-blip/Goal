"""Pre-match priors for Over 0.5 from finished fixtures.

Computes H/A rates (O0.5, FTS, CS, GF/GA) with shrinkage toward league baseline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal, Sequence

Side = Literal["home", "away"]


@dataclass(frozen=True)
class FinishedFixture:
    """Minimal FT result row used for prior estimation."""

    home_team_id: int
    away_team_id: int
    goals_home: int
    goals_away: int
    league_id: int | None = None


@dataclass(frozen=True)
class TeamSideStats:
    team_id: int
    side: Side
    n: int
    pct_over05: float
    pct_fts: float  # failed to score
    pct_cs: float  # clean sheet
    gf_avg: float
    ga_avg: float


@dataclass(frozen=True)
class PrematchPriors:
    fixture_id: int | str | None
    home_team_id: int
    away_team_id: int
    home_pct_over05: float
    away_pct_over05: float
    home_fts: float
    away_fts: float
    home_cs: float
    away_cs: float
    home_gf_avg: float
    home_ga_avg: float
    away_gf_avg: float
    away_ga_avg: float
    league_pct_over05: float
    league_pct_00: float
    p_over05_prior: float
    p_00_prior: float
    shrinkage: float
    n_home: int
    n_away: int


def _as_finished(rows: Iterable[Any]) -> list[FinishedFixture]:
    out: list[FinishedFixture] = []
    for row in rows:
        if isinstance(row, FinishedFixture):
            out.append(row)
            continue
        if not isinstance(row, dict):
            continue
        # Support GOAL-like nested shapes lightly.
        teams = row.get("teams") if isinstance(row.get("teams"), dict) else {}
        goals = row.get("goals") if isinstance(row.get("goals"), dict) else {}
        home = teams.get("home") if isinstance(teams.get("home"), dict) else {}
        away = teams.get("away") if isinstance(teams.get("away"), dict) else {}
        hid = row.get("home_team_id") or home.get("id") or row.get("homeTeamId")
        aid = row.get("away_team_id") or away.get("id") or row.get("awayTeamId")
        gh = row.get("goals_home")
        if gh is None:
            gh = goals.get("home") if goals else row.get("homeScore")
        ga = row.get("goals_away")
        if ga is None:
            ga = goals.get("away") if goals else row.get("awayScore")
        if hid is None or aid is None or gh is None or ga is None:
            continue
        try:
            out.append(
                FinishedFixture(
                    home_team_id=int(hid),
                    away_team_id=int(aid),
                    goals_home=int(gh),
                    goals_away=int(ga),
                    league_id=(
                        int(row["league_id"])
                        if row.get("league_id") is not None
                        else None
                    ),
                )
            )
        except (TypeError, ValueError):
            continue
    return out


def league_over05_rate(fixtures: Sequence[FinishedFixture]) -> tuple[float, float]:
    """Return (pct_over05, pct_00) for a finished sample."""
    if not fixtures:
        # Neutral Big-5-ish fallback if empty (caller should prefer real league rate).
        return 0.92, 0.08
    n = len(fixtures)
    n00 = sum(1 for f in fixtures if f.goals_home == 0 and f.goals_away == 0)
    pct_00 = n00 / n
    return 1.0 - pct_00, pct_00


def team_side_stats(
    fixtures: Sequence[FinishedFixture],
    team_id: int,
    side: Side,
) -> TeamSideStats:
    rows: list[tuple[int, int]] = []  # (gf, ga)
    for f in fixtures:
        if side == "home" and f.home_team_id == team_id:
            rows.append((f.goals_home, f.goals_away))
        elif side == "away" and f.away_team_id == team_id:
            rows.append((f.goals_away, f.goals_home))
    n = len(rows)
    if n == 0:
        return TeamSideStats(
            team_id=team_id,
            side=side,
            n=0,
            pct_over05=0.0,
            pct_fts=0.0,
            pct_cs=0.0,
            gf_avg=0.0,
            ga_avg=0.0,
        )
    over = sum(1 for gf, ga in rows if (gf + ga) >= 1)
    fts = sum(1 for gf, _ in rows if gf == 0)
    cs = sum(1 for _, ga in rows if ga == 0)
    gf_avg = sum(gf for gf, _ in rows) / n
    ga_avg = sum(ga for _, ga in rows) / n
    return TeamSideStats(
        team_id=team_id,
        side=side,
        n=n,
        pct_over05=over / n,
        pct_fts=fts / n,
        pct_cs=cs / n,
        gf_avg=gf_avg,
        ga_avg=ga_avg,
    )


def shrink_rate(raw: float, league: float, n: int, *, k: float = 8.0) -> float:
    """Empirical Bayes shrinkage of a rate toward the league baseline.

    ``k`` = pseudo-counts. Small samples pull hard toward ``league``.
    """
    if n < 0:
        raise ValueError("n must be >= 0")
    if k <= 0:
        return raw
    return (n * raw + k * league) / (n + k)


def compute_prematch_priors(
    *,
    home_team_id: int,
    away_team_id: int,
    finished: Sequence[Any],
    fixture_id: int | str | None = None,
    shrink_k: float = 8.0,
) -> PrematchPriors:
    """Build shrunk H/A priors and a simple blank-product Over 0.5 prior.

    Model sketch (Phase A):
    - Estimate home blank-at-home and away blank-away (FTS).
    - P(0-0) ≈ p_home_blank × p_away_blank (independence approx).
    - Blend with league O0.5 via shrinkage on team rates first.
    """
    fixtures = _as_finished(finished)
    league_o05, league_00 = league_over05_rate(fixtures)

    home = team_side_stats(fixtures, home_team_id, "home")
    away = team_side_stats(fixtures, away_team_id, "away")

    # Shrink O0.5 / FTS / CS toward league (CS≈FTS complement only loosely).
    home_o05 = shrink_rate(home.pct_over05, league_o05, home.n, k=shrink_k)
    away_o05 = shrink_rate(away.pct_over05, league_o05, away.n, k=shrink_k)
    home_fts = shrink_rate(home.pct_fts, league_00, home.n, k=shrink_k)  # blank ~ 0-0-ish
    away_fts = shrink_rate(away.pct_fts, league_00, away.n, k=shrink_k)
    home_cs = shrink_rate(home.pct_cs, league_00, home.n, k=shrink_k)
    away_cs = shrink_rate(away.pct_cs, league_00, away.n, k=shrink_k)

    # Blank product: both fail to score → 0-0.
    p_00 = max(0.0, min(1.0, home_fts * away_fts))
    # Also respect league floor/ceiling lightly via average of blank-product and
    # complementary team O0.5 geometric mean.
    geo = (home_o05 * away_o05) ** 0.5 if home_o05 > 0 and away_o05 > 0 else league_o05
    p_over_blank = 1.0 - p_00
    p_over05 = max(0.0, min(1.0, 0.65 * p_over_blank + 0.35 * geo))

    # Effective shrinkage intensity for transparency (0 = no data, 1 = large N).
    n_eff = home.n + away.n
    shrinkage = n_eff / (n_eff + shrink_k) if (n_eff + shrink_k) else 0.0

    return PrematchPriors(
        fixture_id=fixture_id,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        home_pct_over05=home_o05,
        away_pct_over05=away_o05,
        home_fts=home_fts,
        away_fts=away_fts,
        home_cs=home_cs,
        away_cs=away_cs,
        home_gf_avg=home.gf_avg,
        home_ga_avg=home.ga_avg,
        away_gf_avg=away.gf_avg,
        away_ga_avg=away.ga_avg,
        league_pct_over05=league_o05,
        league_pct_00=league_00,
        p_over05_prior=p_over05,
        p_00_prior=1.0 - p_over05,
        shrinkage=shrinkage,
        n_home=home.n,
        n_away=away.n,
    )
