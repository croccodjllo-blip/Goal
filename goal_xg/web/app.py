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
from goal_xg.live30.score import score_live30
from goal_xg.live30.service import (
    list_live30_candidates,
    live30_score_to_dict,
    live_row_to_state,
    score_fixture_live30,
)
from goal_xg.model.weights import BASE_WEIGHTS, COMPONENT_LABELS_IT

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


def _board_xg(
    *,
    fixture_id: Any,
    minute: int | None,
    period: str | None,
    score_home: int | None,
    score_away: int | None,
) -> int | None:
    """Product xG for list rows from clock/score only (no extra REST).

    Available inside the live30 gate: densified detail still lands on
    ``/fixtures/{id}``. Outside the window → ``None`` (do not invent).
    """
    if minute is None or score_home is None or score_away is None:
        return None
    result = score_live30(
        fixture_id=fixture_id or "",
        minute=minute,
        period=period,
        score_home=score_home,
        score_away=score_away,
    )
    if result.skipped and not result.settled:
        return None
    return int(result.xg_score)


def _xg_tone(xg: int | None) -> str:
    if xg is None:
        return ""
    if xg >= 60:
        return "high"
    if xg >= 40:
        return "mid"
    return "low"


def _attach_board_xg(card: dict[str, Any]) -> dict[str, Any]:
    xg = _board_xg(
        fixture_id=card.get("fixture_id"),
        minute=card.get("minute"),
        period=card.get("period"),
        score_home=card.get("score_home"),
        score_away=card.get("score_away"),
    )
    card["xg_score"] = xg
    card["xg_tone"] = _xg_tone(xg)
    return card


def _row_card(row: dict[str, Any]) -> dict[str, Any]:
    state = live_row_to_state(row)
    if state is None:
        return _attach_board_xg(
            {
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
        )
    return _attach_board_xg(
        {
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
    )


_FEATURE_LABELS: dict[str, str] = {
    "shots_total": "Tiri totali",
    "sot_total": "Tiri in porta",
    "shot_xg_total": "Goal attesi (xG)",
    "xgot_total": "xGOT",
    "woodwork_total": "Pali e traverse",
    "shots_off_total": "Tiri fuori",
    "shots_blocked_total": "Tiri respinti",
    "shots_inside_box_total": "Tiri in area",
    "shots_outside_box_total": "Tiri da fuori",
    "source_half": "Fonte stats",
    "settled": "Settled",
    "prior_fallback": "Prior fallback",
}


# Shot-index BASE_WEIGHTS → Italian labels (fixture «Indice — componenti»).
_COMPONENT_LABELS: dict[str, str] = dict(COMPONENT_LABELS_IT)


def _group_by_league(
    cards: list[dict[str, Any]],
    *,
    exclude_fixture_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Preserve first-seen league order (SofaScore tournament blocks).

    When ``exclude_fixture_ids`` is set (Finestra candidates), those rows are
    omitted from league groups to avoid duplicate cards.
    """
    skip = {str(x) for x in (exclude_fixture_ids or set()) if str(x)}
    order: list[str] = []
    buckets: dict[str, list[dict[str, Any]]] = {}
    for card in cards:
        fid = str(card.get("fixture_id") or "")
        if fid and fid in skip:
            continue
        name = str(card.get("league_name") or "").strip() or "Big-5"
        if name not in buckets:
            buckets[name] = []
            order.append(name)
        buckets[name].append(card)
    return [{"league_name": name, "matches": buckets[name]} for name in order]


def _feature_rows(score: dict[str, Any] | None) -> list[dict[str, str]]:
    """Live volume / formation stats only (not BASE_WEIGHTS ensemble dump)."""
    if not score:
        return []
    rows: list[dict[str, str]] = []
    features = score.get("features") if isinstance(score.get("features"), dict) else {}
    for key, raw in features.items():
        if raw is None or raw == "":
            continue
        label = _FEATURE_LABELS.get(str(key), str(key).replace("_", " "))
        if isinstance(raw, float):
            value = f"{raw:.3f}".rstrip("0").rstrip(".")
        else:
            value = str(raw)
        rows.append({"label": label, "value": value})
    return rows


def _component_rows(score: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Only available index criteria (Attivo). No omit/absent rows."""
    signals: dict[str, Any] = {}
    weights: dict[str, Any] = {}
    if score:
        if isinstance(score.get("signals"), dict):
            signals = score["signals"]
        if isinstance(score.get("weights_used"), dict):
            weights = score["weights_used"]

    rows: list[dict[str, Any]] = []
    order: list[str] = []
    for key in BASE_WEIGHTS:
        if key in signals or key in weights:
            order.append(key)
    for key in weights:
        if key not in order:
            order.append(str(key))
    for key in signals:
        if key not in order:
            order.append(str(key))

    for key in order:
        if key not in BASE_WEIGHTS:
            continue
        if key not in signals or signals[key] is None:
            continue
        label = _COMPONENT_LABELS.get(key, key.replace("_", " "))
        base_w = float(BASE_WEIGHTS[key])
        try:
            sig = float(signals[key])
            value_display = f"{sig:.2f}"
        except (TypeError, ValueError):
            continue
        w_raw = weights.get(key)
        try:
            w_pp = float(w_raw) if w_raw is not None else None
        except (TypeError, ValueError):
            w_pp = None
        weight_display = (
            f"peso {w_pp:.1f}%" if w_pp is not None else f"base {base_w:.0f}%"
        )
        rows.append(
            {
                "id": key,
                "label": label,
                "status": "active",
                "status_label": "Attivo",
                "value": sig,
                "value_display": value_display,
                "weight_pp": w_pp,
                "base_pp": base_w,
                "weight_display": weight_display,
            }
        )
    return rows


def create_app() -> FastAPI:
    app = FastAPI(title="Goal xG", version="0.4.1", docs_url="/docs")
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
            "version": "0.4.1",
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
                # One /fixtures/live fetch shared by board + live30 candidates (quota).
                raw_live = _unwrap_live_rows(client)
                rows = _big5_filter(client, raw_live)
                live_cards = [_row_card(r) for r in rows]
                candidates = [
                    _attach_board_xg(dict(c))
                    for c in list_live30_candidates(
                        client,
                        live_rows=raw_live,
                        big5_only=True,
                        require_00=True,
                        in_window_only=True,
                    )
                ]
            except GoalApiError as exc:
                error = str(exc)
            finally:
                client.close()

        candidate_ids = {
            str(c.get("fixture_id") or "")
            for c in candidates
            if c.get("fixture_id") is not None and str(c.get("fixture_id") or "")
        }
        return _TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "error": error,
                "live_cards": live_cards,
                "league_groups": _group_by_league(
                    live_cards, exclude_fixture_ids=candidate_ids
                ),
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
                    "component_rows": [],
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
                "component_rows": _component_rows(score_dict),
            },
        )

    @app.get("/api/live")
    def api_live() -> JSONResponse:
        _load_env()
        client = _client_or_none()
        if client is None:
            raise HTTPException(status_code=503, detail="GOAL_API_KEY missing")
        try:
            # Single /fixtures/live for both board cards and candidates.
            raw_live = _unwrap_live_rows(client)
            rows = _big5_filter(client, raw_live)
            cards = [_row_card(r) for r in rows]
            candidates = [
                _attach_board_xg(dict(c))
                for c in list_live30_candidates(
                    client,
                    live_rows=raw_live,
                    big5_only=True,
                    require_00=True,
                    in_window_only=True,
                )
            ]
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
