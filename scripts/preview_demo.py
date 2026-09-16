#!/usr/bin/env python3
"""Preview-only FastAPI server with mock Big-5 live data (no GOAL_API_KEY).

Does not alter production routes. Run:

  python scripts/preview_demo.py

Then open http://127.0.0.1:8765/
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# Allow running from repo root without install.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from goal_xg.web.app import (  # noqa: E402
    _attach_board_xg,
    _component_rows,
    _feature_rows,
    _group_by_league,
    _xg_band,
)

_WEB_DIR = _ROOT / "goal_xg" / "web"
_TEMPLATES = Jinja2Templates(directory=str(_WEB_DIR / "templates"))


def _demo_raw_rows() -> list[dict[str, Any]]:
    """Sample live rows shaped like GOAL /fixtures/live entries."""
    return [
        {
            "id": "demo-inter-milan",
            "matchElapsed": 30,
            "matchPeriod": "FIRST_HALF",
            "homeTeamScore": 0,
            "awayTeamScore": 0,
            "league": {"id": 135, "name": "Serie A"},
            "homeTeamName": "Inter",
            "awayTeamName": "Milan",
            "leagueName": "Serie A",
        },
        {
            "id": "demo-arsenal-chelsea",
            "matchElapsed": 29,
            "matchPeriod": "FIRST_HALF",
            "homeTeamScore": 0,
            "awayTeamScore": 0,
            "league": {"id": 39, "name": "Premier League"},
            "homeTeamName": "Arsenal",
            "awayTeamName": "Chelsea",
            "leagueName": "Premier League",
        },
        {
            "id": "demo-barca-atletico",
            "matchElapsed": 18,
            "matchPeriod": "FIRST_HALF",
            "homeTeamScore": 0,
            "awayTeamScore": 0,
            "league": {"id": 140, "name": "La Liga"},
            "homeTeamName": "Barcelona",
            "awayTeamName": "Atlético Madrid",
            "leagueName": "La Liga",
        },
        {
            "id": "demo-bayern-dortmund",
            "matchElapsed": 41,
            "matchPeriod": "FIRST_HALF",
            "homeTeamScore": 1,
            "awayTeamScore": 0,
            "league": {"id": 78, "name": "Bundesliga"},
            "homeTeamName": "Bayern München",
            "awayTeamName": "Borussia Dortmund",
            "leagueName": "Bundesliga",
        },
        {
            "id": "demo-psg-marseille",
            "matchElapsed": 55,
            "matchPeriod": "SECOND_HALF",
            "homeTeamScore": 2,
            "awayTeamScore": 1,
            "league": {"id": 61, "name": "Ligue 1"},
            "homeTeamName": "Paris Saint-Germain",
            "awayTeamName": "Olympique Marseille",
            "leagueName": "Ligue 1",
        },
        {
            "id": "demo-liverpool-city",
            "matchElapsed": 12,
            "matchPeriod": "FIRST_HALF",
            "homeTeamScore": 0,
            "awayTeamScore": 1,
            "league": {"id": 39, "name": "Premier League"},
            "homeTeamName": "Liverpool",
            "awayTeamName": "Manchester City",
            "leagueName": "Premier League",
        },
        {
            "id": "demo-juve-roma",
            "matchElapsed": 67,
            "matchPeriod": "SECOND_HALF",
            "homeTeamScore": 0,
            "awayTeamScore": 0,
            "league": {"id": 135, "name": "Serie A"},
            "homeTeamName": "Juventus",
            "awayTeamName": "Roma",
            "leagueName": "Serie A",
        },
    ]


def _row_to_card(row: dict[str, Any]) -> dict[str, Any]:
    from goal_xg.web.app import _row_card

    return _row_card(row)


def _demo_board() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    cards = [_row_to_card(r) for r in _demo_raw_rows()]
    candidates = [
        c
        for c in cards
        if c.get("is_live30_candidate")
        or (
            c.get("is_00")
            and c.get("in_window")
            and c.get("minute") is not None
            and 28 <= int(c["minute"]) <= 32
        )
    ]
    # Ensure window candidates carry board xG.
    candidates = [_attach_board_xg(dict(c)) for c in candidates]
    candidate_ids = {
        str(c.get("fixture_id") or "")
        for c in candidates
        if c.get("fixture_id") is not None and str(c.get("fixture_id") or "")
    }
    league_groups = _group_by_league(cards, exclude_fixture_ids=candidate_ids)
    return cards, candidates, league_groups


def _demo_score(card: dict[str, Any]) -> dict[str, Any]:
    """Fixture score payload: shot-index components + live stats."""
    from goal_xg.live30.score import score_live30
    from goal_xg.live30.stats import LiveVolumeStats
    from goal_xg.live30.service import live30_score_to_dict

    stats = None
    if card.get("is_00") and card.get("in_window"):
        stats = LiveVolumeStats(
            shots_total_home=6,
            shots_total_away=4,
            sot_home=3,
            sot_away=2,
            shot_xg_home=0.55,
            shot_xg_away=0.32,
            xgot_home=0.40,
            xgot_away=0.22,
            woodwork_home=1,
            woodwork_away=0,
            shots_off_home=2,
            shots_off_away=1,
            shots_blocked_home=1,
            shots_blocked_away=1,
            shots_inside_box_home=4,
            shots_inside_box_away=2,
            shots_outside_box_home=2,
            shots_outside_box_away=2,
            def_yellows_home=1,
            def_yellows_away=0,
            subs_home=0,
            subs_away=0,
            formation_home_now="3-5-2",
            formation_away_now="4-2-3-1",
            source_half="firstHalf",
        )
    result = score_live30(
        fixture_id=card.get("fixture_id") or "demo",
        minute=card.get("minute"),
        period=card.get("period") or "1H",
        score_home=card.get("score_home"),
        score_away=card.get("score_away"),
        stats=stats,
        extra_signals={
            "goals_scored_last5_ha": 0.62,
            "standings": 0.58,
        },
    )
    data = live30_score_to_dict(result)
    notes = list(data.get("notes") or [])
    notes.append("Preview demo — dati mock Big-5 (nessuna chiamata GOAL API).")
    data["notes"] = notes
    if not data.get("features"):
        data["features"] = {
            "shots_total": 10,
            "sot_total": 5,
            "shot_xg_total": 0.87,
            "xgot_total": 0.62,
            "woodwork_total": 1,
            "shots_off_total": 3,
            "shots_blocked_total": 2,
            "shots_inside_box_total": 6,
            "shots_outside_box_total": 4,
            "source_half": "firstHalf",
        }
    if not data.get("signals"):
        data["signals"] = {
            "shots_total": 0.60,
            "sot": 0.55,
            "shot_xg": 0.70,
            "xgot": 0.65,
            "woodwork": 0.45,
            "shots_off": 0.40,
            "shots_blocked": 0.40,
            "shots_inside_box": 0.58,
            "shots_outside_box": 0.42,
            "goals_scored_last5_ha": 0.62,
            "standings": 0.58,
        }
        data["weights_used"] = {
            "shots_total": 9.0,
            "sot": 13.0,
            "shot_xg": 20.0,
            "xgot": 16.0,
            "woodwork": 6.0,
            "shots_off": 6.0,
            "shots_blocked": 6.0,
            "shots_inside_box": 8.0,
            "shots_outside_box": 6.0,
            "goals_scored_last5_ha": 5.0,
            "standings": 5.0,
        }
    return data


def create_preview_app() -> FastAPI:
    app = FastAPI(title="Goal xG Preview", version="0.4.0-preview", docs_url=None)
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR / "static")), name="static")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "service": "goal-xg-preview", "mock": True}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        live_cards, candidates, league_groups = _demo_board()
        return _TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "error": None,
                "live_cards": live_cards,
                "league_groups": league_groups,
                "candidates": candidates,
                # No auto-refresh on preview (stable screenshots).
                "refresh_seconds": None,
            },
        )

    @app.get("/fixtures/{fixture_id}", response_class=HTMLResponse)
    def fixture_page(request: Request, fixture_id: str) -> HTMLResponse:
        live_cards, _, _ = _demo_board()
        by_id = {str(c["fixture_id"]): c for c in live_cards}
        card = by_id.get(fixture_id)
        if card is None:
            # Default to the window candidate for deep-link screenshots.
            card = by_id.get("demo-inter-milan") or (live_cards[0] if live_cards else None)
            if card is not None:
                card = dict(card)
                card["fixture_id"] = fixture_id
        score_dict = _demo_score(card) if card else None
        xg = score_dict.get("xg_score") if score_dict else None
        return _TEMPLATES.TemplateResponse(
            request,
            "fixture.html",
            {
                "error": None,
                "fixture_id": fixture_id,
                "card": card,
                "score": score_dict,
                "band": _xg_band(int(xg) if xg is not None else None),
                "feature_rows": _feature_rows(score_dict),
                "component_rows": _component_rows(score_dict),
            },
        )

    return app


app = create_preview_app()


def main() -> None:
    import uvicorn

    host = "127.0.0.1"
    port = 8765
    print(f"Goal xG preview (mock Big-5) → http://{host}:{port}/")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
