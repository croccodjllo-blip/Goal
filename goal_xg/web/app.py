"""FastAPI + Jinja dashboard for live Big-5 Over 0.5 product-xG."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from goal_xg.clients.goal_api import GoalApiClient, GoalApiError, _as_league_id
from goal_xg.live30.service import (
    list_live30_candidates,
    live30_score_to_dict,
    live_row_to_state,
    score_fixture_live30,
)

_WEB_DIR = Path(__file__).resolve().parent
_TEMPLATES = Jinja2Templates(directory=str(_WEB_DIR / "templates"))


def _load_env() -> None:
    load_dotenv(Path.cwd() / ".env", override=False)


def _xg_band(xg: int | None) -> str:
    if xg is None:
        return "n/a"
    if xg <= 20:
        return "Molto bassa"
    if xg <= 40:
        return "Bassa"
    if xg <= 60:
        return "Media"
    if xg <= 80:
        return "Alta"
    return "Molto alta"


def _client_or_none() -> GoalApiClient | None:
    _load_env()
    if not os.environ.get("GOAL_API_KEY", "").strip():
        return None
    try:
        return GoalApiClient()
    except GoalApiError:
        return None


def _unwrap_live_rows(client: GoalApiClient) -> list[dict[str, Any]]:
    payload = client.fixtures_live()
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("data", "response", "fixtures", "results"):
            val = payload.get(key)
            if isinstance(val, list):
                return [r for r in val if isinstance(r, dict)]
    return []


def _big5_filter(client: GoalApiClient, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        ids = {lg.league_id for lg in client.discover_big5_leagues()}
    except GoalApiError:
        return rows
    if not ids:
        return rows
    out: list[dict[str, Any]] = []
    for row in rows:
        league = row.get("league") if isinstance(row.get("league"), dict) else {}
        lid = _as_league_id(
            league.get("id") or row.get("leagueId") or row.get("league_id")
        )
        if lid is not None and lid in ids:
            out.append(row)
    return out


def _row_card(row: dict[str, Any]) -> dict[str, Any]:
    state = live_row_to_state(row)
    if state is None:
        return {
            "fixture_id": str(row.get("id") or ""),
            "home_name": row.get("homeTeamName") or "?",
            "away_name": row.get("awayTeamName") or "?",
            "league_name": row.get("leagueName") or "",
            "minute": None,
            "score_home": None,
            "score_away": None,
            "is_00": False,
            "in_window": False,
            "is_live30_candidate": False,
        }
    return {
        "fixture_id": state.fixture_id or "",
        "home_name": state.home_name or "?",
        "away_name": state.away_name or "?",
        "league_name": state.league_name or "",
        "minute": state.minute,
        "period": state.period,
        "score_home": state.home_score,
        "score_away": state.away_score,
        "is_00": state.is_00,
        "in_window": state.in_live30_window,
        "is_live30_candidate": state.is_live30_candidate,
    }


_FEATURE_LABELS: dict[str, str] = {
    "sot_total": "Tiri in porta",
    "attacks_total": "Attacchi",
    "corners_total": "Calci d'angolo",
    "possession_home": "Possesso casa %",
    "saves_total": "Parate",
    "def_yellows_total": "Ammonizioni dif.",
    "subs_total": "Sostituzioni",
    "source_half": "Fonte stats",
    "formation_home": "Modulo casa",
    "formation_away": "Modulo trasferta",
    "settled": "Settled",
}

_SIGNAL_LABELS: dict[str, str] = {
    "pace": "Ritmo",
    "pressure": "Pressione",
    "set_pieces": "Palle inattive",
    "goalkeeper": "Portiere",
    "discipline": "Disciplina",
    "subs": "Cambi",
    "prematch": "Prior pre-match",
}


def _group_by_league(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Preserve first-seen league order (SofaScore tournament blocks)."""
    order: list[str] = []
    buckets: dict[str, list[dict[str, Any]]] = {}
    for card in cards:
        name = str(card.get("league_name") or "").strip() or "Big-5"
        if name not in buckets:
            buckets[name] = []
            order.append(name)
        buckets[name].append(card)
    return [{"league_name": name, "matches": buckets[name]} for name in order]


def _feature_rows(score: dict[str, Any] | None) -> list[dict[str, str]]:
    if not score:
        return []
    rows: list[dict[str, str]] = []
    features = score.get("features") if isinstance(score.get("features"), dict) else {}
    signals = score.get("signals") if isinstance(score.get("signals"), dict) else {}
    for key, raw in features.items():
        if raw is None or raw == "":
            continue
        label = _FEATURE_LABELS.get(str(key), str(key).replace("_", " "))
        rows.append({"label": label, "value": str(raw)})
    for key, raw in signals.items():
        if raw is None:
            continue
        label = _SIGNAL_LABELS.get(str(key), str(key).replace("_", " "))
        try:
            val = f"{float(raw):.2f}"
        except (TypeError, ValueError):
            val = str(raw)
        rows.append({"label": f"Segnale · {label}", "value": val})
    return rows


def create_app() -> FastAPI:
    app = FastAPI(title="Goal xG", version="0.3.0", docs_url="/docs")
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR / "static")), name="static")

    @app.get("/health")
    def health() -> dict[str, Any]:
        _load_env()
        goal_key = bool(os.environ.get("GOAL_API_KEY", "").strip())
        fd_key = bool(
            os.environ.get("FOOTBALL_DATA_API_KEY", "").strip()
            or os.environ.get("FOOTBALL_DATA_TOKEN", "").strip()
        )
        return {
            "ok": True,
            "service": "goal-xg",
            "version": "0.3.0",
            "goal_api_key_configured": goal_key,
            "football_data_configured": fd_key,
        }

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        _load_env()
        client = _client_or_none()
        error: str | None = None
        live_cards: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        if client is None:
            error = "GOAL_API_KEY mancante. Imposta la key nel file .env (solo server)."
        else:
            try:
                rows = _big5_filter(client, _unwrap_live_rows(client))
                live_cards = [_row_card(r) for r in rows]
                candidates = list_live30_candidates(
                    client, big5_only=True, require_00=True, in_window_only=True
                )
            except GoalApiError as exc:
                error = str(exc)
            finally:
                client.close()

        return _TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "error": error,
                "live_cards": live_cards,
                "league_groups": _group_by_league(live_cards),
                "candidates": candidates,
                "refresh_seconds": 30,
            },
        )

    @app.get("/fixtures/{fixture_id}", response_class=HTMLResponse)
    def fixture_page(request: Request, fixture_id: str) -> HTMLResponse:
        _load_env()
        client = _client_or_none()
        if client is None:
            return _TEMPLATES.TemplateResponse(
                request,
                "fixture.html",
                {
                    "error": "GOAL_API_KEY mancante.",
                    "fixture_id": fixture_id,
                    "card": None,
                    "score": None,
                    "band": None,
                    "feature_rows": [],
                },
            )
        error: str | None = None
        card: dict[str, Any] | None = None
        score_dict: dict[str, Any] | None = None
        try:
            detail = client.fixture_by_id(fixture_id)
            row = detail
            if isinstance(detail, dict):
                for key in ("data", "response", "fixture"):
                    val = detail.get(key)
                    if isinstance(val, dict):
                        row = val
                        break
                    if isinstance(val, list) and val and isinstance(val[0], dict):
                        row = val[0]
                        break
            if isinstance(row, dict):
                card = _row_card(row)
            scored = score_fixture_live30(client, fixture_id, fetch_history=False)
            score_dict = live30_score_to_dict(scored)
        except GoalApiError as exc:
            error = str(exc)
        finally:
            client.close()

        xg = score_dict.get("xg_score") if score_dict else None
        return _TEMPLATES.TemplateResponse(
            request,
            "fixture.html",
            {
                "error": error,
                "fixture_id": fixture_id,
                "card": card,
                "score": score_dict,
                "band": _xg_band(int(xg) if xg is not None else None),
                "feature_rows": _feature_rows(score_dict),
            },
        )

    @app.get("/api/live")
    def api_live() -> JSONResponse:
        _load_env()
        client = _client_or_none()
        if client is None:
            raise HTTPException(status_code=503, detail="GOAL_API_KEY missing")
        try:
            rows = _big5_filter(client, _unwrap_live_rows(client))
            cards = [_row_card(r) for r in rows]
            candidates = list_live30_candidates(client)
            return JSONResponse({"live": cards, "live30_candidates": candidates})
        except GoalApiError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        finally:
            client.close()

    @app.get("/api/live30/{fixture_id}")
    def api_live30(fixture_id: str) -> JSONResponse:
        _load_env()
        client = _client_or_none()
        if client is None:
            raise HTTPException(status_code=503, detail="GOAL_API_KEY missing")
        try:
            scored = score_fixture_live30(client, fixture_id, fetch_history=False)
            data = live30_score_to_dict(scored)
            data["band"] = _xg_band(int(data["xg_score"]))
            return JSONResponse(data)
        except GoalApiError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        finally:
            client.close()

    return app


# ASGI entry: uvicorn goal_xg.web.app:app
app = create_app()


def run(host: str = "127.0.0.1", port: int = 8000) -> None:
    """CLI entry for ``goal-xg-web`` / ``python -m goal_xg.web``."""
    import uvicorn

    uvicorn.run(
        "goal_xg.web.app:app",
        host=os.environ.get("GOAL_XG_HOST", host),
        port=int(os.environ.get("GOAL_XG_PORT", str(port))),
        reload=False,
    )


if __name__ == "__main__":
    run()
