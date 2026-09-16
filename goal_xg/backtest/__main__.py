"""CLI entry: ``python -m goal_xg.backtest`` on a JSONL fixture file."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from goal_xg.backtest import (
    BacktestCase,
    HistoricalLive30Row,
    evaluate_predictions,
    run_backtest,
)
from goal_xg.features.prematch import FinishedFixture
from goal_xg.live30.stats import LiveVolumeStats


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rows.append(json.loads(line))
    return rows


def _history(raw: Any) -> tuple[FinishedFixture, ...]:
    if not isinstance(raw, list):
        return ()
    out: list[FinishedFixture] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        try:
            out.append(
                FinishedFixture(
                    home_team_id=row["home_team_id"],
                    away_team_id=row["away_team_id"],
                    goals_home=int(row["goals_home"]),
                    goals_away=int(row["goals_away"]),
                    league_id=row.get("league_id"),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(out)


def _stats(raw: Any) -> LiveVolumeStats | None:
    if not isinstance(raw, dict):
        return None
    return LiveVolumeStats(
        sot_home=raw.get("sot_home"),
        sot_away=raw.get("sot_away"),
        dangerous_attacks_home=raw.get("dangerous_attacks_home"),
        dangerous_attacks_away=raw.get("dangerous_attacks_away"),
        corners_home=raw.get("corners_home"),
        corners_away=raw.get("corners_away"),
        possession_home=raw.get("possession_home"),
        possession_away=raw.get("possession_away"),
        saves_home=raw.get("saves_home"),
        saves_away=raw.get("saves_away"),
        source_half=raw.get("source_half"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backtest 0-0@30′ → FT Over 0.5 predictions. "
            "Input: JSONL with fixture_id, league_id, home/away ids, over05_ft, "
            "optional finished_history + stats."
        )
    )
    parser.add_argument(
        "path",
        type=Path,
        help="JSONL labeled file (see tests/fixtures/backtest_sample.jsonl)",
    )
    parser.add_argument("--bins", type=int, default=10)
    parser.add_argument(
        "--no-fit-calib",
        action="store_true",
        help="Do not fit per-league calib on the same labels (use global prior)",
    )
    parser.add_argument(
        "--predictions-only",
        action="store_true",
        help="Rows already have p_hat + y_over05; skip live scoring",
    )
    args = parser.parse_args(argv)

    raw_rows = _load_jsonl(args.path)
    if args.predictions_only:
        cases = [
            BacktestCase(
                fixture_id=r.get("fixture_id", i),
                league_id=r.get("league_id"),
                p_hat=float(r["p_hat"]),
                y_over05=int(r.get("y_over05", r.get("over05_ft", 0))),
                xg_score=int(r.get("xg_score", round(100 * float(r["p_hat"])))),
            )
            for i, r in enumerate(raw_rows)
            if "p_hat" in r
        ]
        report = evaluate_predictions(cases, n_bins=args.bins)
    else:
        hist_rows = [
            HistoricalLive30Row(
                fixture_id=r.get("fixture_id", i),
                league_id=r["league_id"],
                home_team_id=r["home_team_id"],
                away_team_id=r["away_team_id"],
                over05_ft=bool(r["over05_ft"]),
                finished_history=_history(r.get("finished_history")),
                stats=_stats(r.get("stats")),
                minute=int(r.get("minute", 30)),
                period=str(r.get("period", "1H")),
            )
            for i, r in enumerate(raw_rows)
            if "league_id" in r and "over05_ft" in r
        ]
        report = run_backtest(
            hist_rows,
            fit_calibration=not args.no_fit_calib,
            n_bins=args.bins,
        )

    json.dump(report.as_dict(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
