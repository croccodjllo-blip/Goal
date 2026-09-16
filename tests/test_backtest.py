"""Backtest harness tests (fixtures/mocks only)."""

from __future__ import annotations

from pathlib import Path

import pytest

from goal_xg.backtest import (
    BacktestCase,
    HistoricalLive30Row,
    brier_score,
    evaluate_predictions,
    reliability_diagram,
    run_backtest,
)
from goal_xg.features.prematch import FinishedFixture
from goal_xg.live30.stats import LiveVolumeStats


def test_brier_and_reliability() -> None:
    ps = [0.8, 0.7, 0.2, 0.1]
    ys = [1, 1, 0, 0]
    # (0.2² + 0.3² + 0.2² + 0.1²) / 4 = 0.045
    assert brier_score(ps, ys) == pytest.approx(0.045)
    bins = reliability_diagram(ps, ys, n_bins=5)
    assert len(bins) == 5
    assert sum(b.n for b in bins) == 4


def test_evaluate_predictions_empty() -> None:
    report = evaluate_predictions([])
    assert report.n == 0
    assert report.brier == 0.0


def test_run_backtest_with_history() -> None:
    hist = tuple(
        FinishedFixture(
            home_team_id=1,
            away_team_id=10 + i,
            goals_home=2,
            goals_away=0,
            league_id="135",
        )
        for i in range(6)
    ) + tuple(
        FinishedFixture(
            home_team_id=20 + i,
            away_team_id=2,
            goals_home=0,
            goals_away=1,
            league_id="135",
        )
        for i in range(6)
    )
    rows = [
        HistoricalLive30Row(
            fixture_id=f"a{i}",
            league_id="135",
            home_team_id=1,
            away_team_id=2,
            over05_ft=True,
            finished_history=hist,
            stats=LiveVolumeStats(sot_home=2, sot_away=1, source_half="firstHalf"),
        )
        for i in range(5)
    ] + [
        HistoricalLive30Row(
            fixture_id="b0",
            league_id="135",
            home_team_id=1,
            away_team_id=2,
            over05_ft=False,
            finished_history=hist,
            stats=LiveVolumeStats(sot_home=0, sot_away=0, source_half="firstHalf"),
        )
    ]
    report = run_backtest(rows, fit_calibration=True, n_bins=5)
    assert report.n == 6
    assert 0.0 <= report.brier <= 1.0
    assert report.log_loss is not None
    assert report.base_rate == 5 / 6
    assert all(isinstance(c, BacktestCase) for c in report.cases)
    d = report.as_dict()
    assert d["n"] == 6
    assert "reliability" in d


def test_cli_module_on_sample_jsonl(tmp_path: Path) -> None:
    sample = Path(__file__).parent / "fixtures" / "backtest_sample.jsonl"
    assert sample.exists()
    from goal_xg.backtest.__main__ import main

    assert main([str(sample), "--bins", "5"]) == 0
