"""Historical backtest harness for 0-0 @ 30′ → FT Over 0.5.

Works on labeled fixtures/mocks (no live network). Metrics: Brier score
and simple reliability bins for product-xG calibration checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from goal_xg.features.extractors import build_extra_signals
from goal_xg.features.prematch import FinishedFixture, compute_prematch_priors
from goal_xg.live30.score import score_live30
from goal_xg.live30.stats import LiveVolumeStats
from goal_xg.model.calibration import (
    DEFAULT_P_OVER05_GIVEN_00_AT_30,
    Labeled00At30,
    calibrate_leagues,
    resolve_league_p,
)
from goal_xg.model.over05 import xg_score_from_p


@dataclass(frozen=True)
class BacktestCase:
    """One scored case with binary FT Over 0.5 label."""

    fixture_id: str | int
    league_id: str | int | None
    p_hat: float
    y_over05: int  # 1 or 0
    xg_score: int
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReliabilityBin:
    lo: float
    hi: float
    n: int
    mean_p: float
    mean_y: float


@dataclass(frozen=True)
class BacktestReport:
    n: int
    brier: float
    log_loss: float | None
    mean_p: float
    base_rate: float
    reliability: tuple[ReliabilityBin, ...]
    cases: tuple[BacktestCase, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "brier": self.brier,
            "log_loss": self.log_loss,
            "mean_p": self.mean_p,
            "base_rate": self.base_rate,
            "reliability": [
                {
                    "lo": b.lo,
                    "hi": b.hi,
                    "n": b.n,
                    "mean_p": b.mean_p,
                    "mean_y": b.mean_y,
                }
                for b in self.reliability
            ],
        }


def brier_score(p_hats: Sequence[float], ys: Sequence[int]) -> float:
    if not p_hats:
        raise ValueError("empty predictions")
    if len(p_hats) != len(ys):
        raise ValueError("length mismatch")
    return sum((float(p) - float(y)) ** 2 for p, y in zip(p_hats, ys)) / len(p_hats)


def log_loss(p_hats: Sequence[float], ys: Sequence[int], *, eps: float = 1e-12) -> float:
    import math

    if not p_hats:
        raise ValueError("empty predictions")
    total = 0.0
    for p, y in zip(p_hats, ys):
        pp = min(1.0 - eps, max(eps, float(p)))
        yy = int(y)
        total += -(yy * math.log(pp) + (1 - yy) * math.log(1.0 - pp))
    return total / len(p_hats)


def reliability_diagram(
    p_hats: Sequence[float],
    ys: Sequence[int],
    *,
    n_bins: int = 10,
) -> list[ReliabilityBin]:
    if n_bins <= 0:
        raise ValueError("n_bins must be > 0")
    bins: list[list[tuple[float, int]]] = [[] for _ in range(n_bins)]
    for p, y in zip(p_hats, ys):
        pp = max(0.0, min(1.0, float(p)))
        idx = min(n_bins - 1, int(pp * n_bins))
        bins[idx].append((pp, int(y)))
    out: list[ReliabilityBin] = []
    for i, bucket in enumerate(bins):
        lo = i / n_bins
        hi = (i + 1) / n_bins
        if not bucket:
            out.append(ReliabilityBin(lo=lo, hi=hi, n=0, mean_p=0.0, mean_y=0.0))
            continue
        mean_p = sum(p for p, _ in bucket) / len(bucket)
        mean_y = sum(y for _, y in bucket) / len(bucket)
        out.append(
            ReliabilityBin(lo=lo, hi=hi, n=len(bucket), mean_p=mean_p, mean_y=mean_y)
        )
    return out


def evaluate_predictions(
    cases: Sequence[BacktestCase] | Sequence[Mapping[str, Any]],
    *,
    n_bins: int = 10,
) -> BacktestReport:
    """Compute Brier / log-loss / reliability for precomputed cases."""
    parsed: list[BacktestCase] = []
    for c in cases:
        if isinstance(c, BacktestCase):
            parsed.append(c)
            continue
        if not isinstance(c, Mapping):
            continue
        p = float(c["p_hat"])
        y = int(c.get("y_over05", c.get("y", 0)))
        parsed.append(
            BacktestCase(
                fixture_id=c.get("fixture_id", "?"),
                league_id=c.get("league_id"),
                p_hat=p,
                y_over05=y,
                xg_score=int(c.get("xg_score", xg_score_from_p(p))),
            )
        )
    if not parsed:
        return BacktestReport(
            n=0,
            brier=0.0,
            log_loss=None,
            mean_p=0.0,
            base_rate=0.0,
            reliability=tuple(),
            cases=tuple(),
        )
    ps = [c.p_hat for c in parsed]
    ys = [c.y_over05 for c in parsed]
    return BacktestReport(
        n=len(parsed),
        brier=brier_score(ps, ys),
        log_loss=log_loss(ps, ys),
        mean_p=sum(ps) / len(ps),
        base_rate=sum(ys) / len(ys),
        reliability=tuple(reliability_diagram(ps, ys, n_bins=n_bins)),
        cases=tuple(parsed),
    )


@dataclass(frozen=True)
class HistoricalLive30Row:
    """Labeled historical row for offline backtest (fixtures/mocks)."""

    fixture_id: str | int
    league_id: str | int
    home_team_id: str | int
    away_team_id: str | int
    over05_ft: bool
    finished_history: tuple[FinishedFixture | Mapping[str, Any], ...] = ()
    stats: LiveVolumeStats | None = None
    minute: int = 30
    period: str = "1H"


def score_historical_row(
    row: HistoricalLive30Row,
    *,
    calib_table: Mapping[str, Any] | None = None,
    apply_dynamic: bool = True,
) -> BacktestCase:
    """Score one labeled 0-0@30′ row with the live ensemble (offline)."""
    hist = list(row.finished_history)
    priors = None
    extra = {}
    fatigue_flag = False
    if hist:
        priors = compute_prematch_priors(
            home_team_id=row.home_team_id,
            away_team_id=row.away_team_id,
            finished=hist,
            fixture_id=row.fixture_id,
        )
        built = build_extra_signals(
            home_team_id=row.home_team_id,
            away_team_id=row.away_team_id,
            finished=hist,
            league_baseline=priors.league_pct_over05,
        )
        extra = built.signals
        fatigue_flag = built.fatigue_flag

    league_p = resolve_league_p(
        calib_table,  # type: ignore[arg-type]
        row.league_id,
        prior=DEFAULT_P_OVER05_GIVEN_00_AT_30,
        allow_prior_fallback=True,
    )

    scored = score_live30(
        fixture_id=row.fixture_id,
        minute=row.minute,
        period=row.period,
        score_home=0,
        score_away=0,
        stats=row.stats,
        priors=priors,
        extra_signals=extra or None,
        league_p_over05_given_00=league_p,
        fatigue_flag=fatigue_flag,
        apply_dynamic=apply_dynamic,
    )
    return BacktestCase(
        fixture_id=row.fixture_id,
        league_id=row.league_id,
        p_hat=scored.p_over05_ft,
        y_over05=1 if row.over05_ft else 0,
        xg_score=scored.xg_score,
        notes=scored.notes,
    )


def run_backtest(
    rows: Sequence[HistoricalLive30Row],
    *,
    fit_calibration: bool = True,
    n_bins: int = 10,
    apply_dynamic: bool = True,
) -> BacktestReport:
    """Fit optional per-league calib on labels, score each row, evaluate."""
    calib = None
    if fit_calibration:
        labeled = [
            Labeled00At30(
                league_id=r.league_id,
                over05_ft=r.over05_ft,
                fixture_id=r.fixture_id,
            )
            for r in rows
        ]
        calib = calibrate_leagues(labeled)

    cases = [
        score_historical_row(
            r, calib_table=calib, apply_dynamic=apply_dynamic
        )
        for r in rows
    ]
    return evaluate_predictions(cases, n_bins=n_bins)


def labeled_from_dicts(rows: Iterable[Mapping[str, Any]]) -> list[Labeled00At30]:
    out: list[Labeled00At30] = []
    for row in rows:
        lid = row.get("league_id") or row.get("leagueId")
        if lid is None:
            continue
        if "over05_ft" in row:
            y = bool(row["over05_ft"])
        else:
            continue
        if row.get("was_00_at_30") is False:
            continue
        out.append(
            Labeled00At30(
                league_id=lid,
                over05_ft=y,
                fixture_id=row.get("fixture_id"),
            )
        )
    return out
