"""FastAPI + Jinja dashboard for live Big-5 Over 0.5 product-xG."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from goal_xg.clients.api_sports import maybe_client as maybe_api_sports
from goal_xg.clients.goal_api import GoalApiClient, GoalApiError, _as_league_id
from goal_xg.live30.board import (
    clear_schedule_cache as clear_today_schedule_cache,
    focus_reason,
    list_todays_big5_fixtures,
    select_watch_focus,
)
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

# Backward-compatible aliases for tests / older call sites.
clear_schedule_cache = clear_today_schedule_cache
_SCHEDULE_TTL_SEC = 120.0  # today's programme cache (see live30.board)


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


def _filter_live_00(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only 0-0 live rows — drop scored matches from the watchlist."""
    return [c for c in cards if c.get("is_00") is True]


def _sort_live_watch(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prioritize 28–32′ window, then closer to 30′."""

    def _key(card: dict[str, Any]) -> tuple[int, int, int]:
        in_window = 0 if card.get("in_window") else 1
        minute = card.get("minute")
        m = int(minute) if isinstance(minute, (int, float)) else 999
        return (in_window, abs(m - 30), -m if m != 999 else 0)

    return sorted(cards, key=_key)


def list_scheduled_big5(
    client: GoalApiClient,
    *,
    now: Any = None,
    use_cache: bool = True,
    day: str | None = None,
) -> list[dict[str, Any]]:
    """Today's Big-5 programme (scheduled / live / finished).

    ``now`` is accepted for test compatibility; day defaults to Europe/Rome oggi.
    """
    day_s = day
    if day_s is None and now is not None:
        try:
            day_s = now.astimezone().date().isoformat()
        except Exception:
            day_s = None
    return list_todays_big5_fixtures(client, day=day_s, use_cache=use_cache)


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
    "goals_scored_last5_ha_avg": "Media gol U5 (combinata)",
    "goals_scored_last5_ha_home_avg": "Media gol U5 casa",
    "goals_scored_last5_ha_away_avg": "Media gol U5 trasferta",
    "standings_label": "Classifica (posizioni)",
    "standings_home_rank": "Posizione casa",
    "standings_away_rank": "Posizione trasferta",
    "source_half": "Fonte stats",
    "settled": "Settled",
    "prior_fallback": "Prior fallback",
}


# Shot-index BASE_WEIGHTS → Italian labels (fixture «Indice — componenti»).
_COMPONENT_LABELS: dict[str, str] = dict(COMPONENT_LABELS_IT)

# Component id → features key holding the raw live/prematch input.
_COMPONENT_RAW_KEYS: dict[str, str] = {
    "shots_total": "shots_total",
    "sot": "sot_total",
    "shot_xg": "shot_xg_total",
    "xgot": "xgot_total",
    "woodwork": "woodwork_total",
    "shots_off": "shots_off_total",
    "shots_blocked": "shots_blocked_total",
    "shots_inside_box": "shots_inside_box_total",
    "shots_outside_box": "shots_outside_box_total",
    "goals_scored_last5_ha": "goals_scored_last5_ha_avg",
    "standings": "standings_label",
}

# Feature keys already shown in the componenti table (avoid duplicate list).
_COMPONENT_COVERED_FEATURES: frozenset[str] = frozenset(
    {
        *_COMPONENT_RAW_KEYS.values(),
        "goals_scored_last5_ha_home_avg",
        "goals_scored_last5_ha_away_avg",
        "goals_scored_last5_ha_home_n",
        "goals_scored_last5_ha_away_n",
        "standings_home_rank",
        "standings_away_rank",
        "standings_n_teams",
        "standings_gap",
        "settled",
    }
)


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


def _format_raw_value(raw: Any) -> str | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        return "sì" if raw else "no"
    if isinstance(raw, float):
        if raw == int(raw) and abs(raw) >= 1:
            return str(int(raw))
        text = f"{raw:.3f}".rstrip("0").rstrip(".")
        return text or "0"
    if isinstance(raw, int):
        return str(raw)
    return str(raw)


def _component_raw_display(
    key: str, features: Mapping[str, Any]
) -> tuple[str | None, str | None]:
    """Return (raw_display, raw_detail) for a component id."""
    if key == "goals_scored_last5_ha":
        home = features.get("goals_scored_last5_ha_home_avg")
        away = features.get("goals_scored_last5_ha_away_avg")
        combined = features.get("goals_scored_last5_ha_avg")
        if home is not None and away is not None:
            h = _format_raw_value(home)
            a = _format_raw_value(away)
            detail = f"casa {h} · trasferta {a}"
            primary = _format_raw_value(combined) if combined is not None else h
            return primary, detail
        return _format_raw_value(combined), None
    if key == "standings":
        label = features.get("standings_label")
        if label:
            return str(label), None
        hr = features.get("standings_home_rank")
        ar = features.get("standings_away_rank")
        if hr is not None and ar is not None:
            return f"{hr}ª–{ar}ª", None
        return None, None
    feat_key = _COMPONENT_RAW_KEYS.get(key)
    if not feat_key:
        return None, None
    return _format_raw_value(features.get(feat_key)), None


def _feature_rows(score: dict[str, Any] | None) -> list[dict[str, str]]:
    """Leftover live metadata not already shown in componenti rows."""
    if not score:
        return []
    rows: list[dict[str, str]] = []
    features = score.get("features") if isinstance(score.get("features"), dict) else {}
    for key, raw in features.items():
        if raw is None or raw == "":
            continue
        if str(key) in _COMPONENT_COVERED_FEATURES:
            continue
        label = _FEATURE_LABELS.get(str(key), str(key).replace("_", " "))
        value = _format_raw_value(raw)
        if value is None:
            continue
        rows.append({"label": label, "value": value})
    return rows


def _component_rows(score: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Only available index criteria. Italian label + raw + weight + contrib."""
    signals: dict[str, Any] = {}
    weights: dict[str, Any] = {}
    features: dict[str, Any] = {}
    if score:
        if isinstance(score.get("signals"), dict):
            signals = score["signals"]
        if isinstance(score.get("weights_used"), dict):
            weights = score["weights_used"]
        if isinstance(score.get("features"), dict):
            features = score["features"]

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
        contrib: float | None = None
        contrib_display: str | None = None
        if w_pp is not None:
            contrib = sig * (w_pp / 100.0)
            contrib_display = f"{contrib:.3f}".rstrip("0").rstrip(".")
        raw_display, raw_detail = _component_raw_display(key, features)
        rows.append(
            {
                "id": key,
                "label": label,
                "status": "active",
                "status_label": "Attivo",
                "value": sig,
                "value_display": value_display,
                "raw_display": raw_display,
                "raw_detail": raw_detail,
                "weight_pp": w_pp,
                "base_pp": base_w,
                "weight_display": weight_display,
                "contrib": contrib,
                "contrib_display": contrib_display,
            }
        )
    return rows


def _settled_snapshot_rows(score: dict[str, Any] | None) -> list[dict[str, str]]:
    """When settled (no blend), list available raw shot inputs for the snapshot."""
    if not score or not score.get("settled"):
        return []
    if score.get("signals"):
        return []  # componenti table already covers active criteria
    features = score.get("features") if isinstance(score.get("features"), dict) else {}
    rows: list[dict[str, str]] = []
    for comp_id, feat_key in _COMPONENT_RAW_KEYS.items():
        if comp_id in ("goals_scored_last5_ha", "standings"):
            raw_display, raw_detail = _component_raw_display(comp_id, features)
            if not raw_display:
                continue
            label = _COMPONENT_LABELS.get(comp_id, comp_id)
            value = raw_display if not raw_detail else f"{raw_display} ({raw_detail})"
            rows.append({"label": label, "value": value})
            continue
        raw = features.get(feat_key)
        if raw is None or raw == "":
            continue
        value = _format_raw_value(raw)
        if value is None:
            continue
        rows.append(
            {
                "label": _COMPONENT_LABELS.get(comp_id, feat_key),
                "value": value,
            }
        )
    return rows


def _format_stat_cell(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "sì" if value else "no"
    if isinstance(value, float):
        if value == int(value) and abs(value) >= 1:
            return str(int(value))
        text = f"{value:.3f}".rstrip("0").rstrip(".")
        return text or "0"
    return str(value)


def _api_sports_stat_rows_for_display(
    dump: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Normalize dump rows for the fixture «Stats API-Football» table."""
    if not dump:
        return []
    raw_rows = dump.get("rows") if isinstance(dump.get("rows"), list) else []
    out: list[dict[str, Any]] = []
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        typ = str(row.get("type") or "")
        label = str(row.get("label") or typ or "—")
        out.append(
            {
                "type": typ,
                "label": label,
                "home": _format_stat_cell(row.get("home")),
                "away": _format_stat_cell(row.get("away")),
                "home_1h": _format_stat_cell(row.get("home_1h")),
                "away_1h": _format_stat_cell(row.get("away_1h")),
                "in_index": bool(row.get("in_index")),
                "has_1h": row.get("home_1h") is not None
                or row.get("away_1h") is not None,
            }
        )
    return out


def _load_api_sports_dump_for_fixture(
    fixture_row: Mapping[str, Any] | None,
    card: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Fetch full API-Football statistics dump when key is configured."""
    asp = maybe_api_sports()
    if asp is None:
        return None
    try:
        home_name = None
        away_name = None
        api_id = None
        date = None
        if card:
            home_name = card.get("home_name")
            away_name = card.get("away_name")
        if isinstance(fixture_row, dict):
            teams = (
                fixture_row.get("teams")
                if isinstance(fixture_row.get("teams"), dict)
                else {}
            )
            home_t = teams.get("home") if isinstance(teams.get("home"), dict) else {}
            away_t = teams.get("away") if isinstance(teams.get("away"), dict) else {}
            home_name = (
                home_name or home_t.get("name") or fixture_row.get("homeTeamName")
            )
            away_name = (
                away_name or away_t.get("name") or fixture_row.get("awayTeamName")
            )
            for key in ("apiId", "api_id", "providerId", "provider_id"):
                if fixture_row.get(key) is not None:
                    api_id = fixture_row.get(key)
                    break
            kickoff = (
                fixture_row.get("starting_at")
                or fixture_row.get("startingAt")
                or fixture_row.get("date")
                or fixture_row.get("kickoff")
            )
            if kickoff:
                date = str(kickoff).strip()[:10]
        return asp.load_fixture_stat_dump(
            home_name=str(home_name) if home_name else None,
            away_name=str(away_name) if away_name else None,
            date=date,
            api_id=api_id,
            include_events=True,
            prefer_half=True,
        )
    except Exception:
        return {
            "meta": {"configured": True, "error": "api_sports_dump_failed"},
            "rows": [],
            "events": [],
        }
    finally:
        asp.close()


def create_app() -> FastAPI:
    app = FastAPI(title="Goal xG", version="0.4.5", docs_url="/docs")
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR / "static")), name="static")

    @app.get("/health")
    def health() -> dict[str, Any]:
        _load_env()
        goal_key = bool(os.environ.get("GOAL_API_KEY", "").strip())
        fd_key = bool(
            os.environ.get("FOOTBALL_DATA_API_KEY", "").strip()
            or os.environ.get("FOOTBALL_DATA_TOKEN", "").strip()
        )
        api_sports_key = bool(
            os.environ.get("API_SPORTS_KEY", "").strip()
            or os.environ.get("APISPORTS_KEY", "").strip()
        )
        return {
            "ok": True,
            "service": "goal-xg",
            "version": "0.4.5",
            "goal_api_key_configured": goal_key,
            "football_data_configured": fd_key,
            "api_sports_configured": api_sports_key,
        }

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        _load_env()
        client = _client_or_none()
        error: str | None = None
        live_cards: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        scheduled: list[dict[str, Any]] = []
        focus: dict[str, Any] | None = None
        if client is None:
            error = "GOAL_API_KEY mancante. Imposta la key nel file .env (solo server)."
        else:
            try:
                # One /fixtures/live fetch shared by board + live30 candidates (quota).
                raw_live = _unwrap_live_rows(client)
                rows = _big5_filter(client, raw_live)
                live_cards = _sort_live_watch(
                    _filter_live_00([_row_card(r) for r in rows])
                )
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
                scheduled = list_todays_big5_fixtures(client)
                focus = select_watch_focus(
                    candidates=candidates,
                    live_cards=live_cards,
                    schedule_cards=scheduled,
                )
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
                "scheduled": scheduled,
                "focus": focus,
                "focus_reason": focus_reason(focus),
                "refresh_seconds": 30,
            },
        )

    @app.get("/fixtures/{fixture_id}", response_class=HTMLResponse)
    def fixture_page(request: Request, fixture_id: str) -> HTMLResponse:
        _load_env()
        client = _client_or_none()
        empty_ctx = {
            "error": "GOAL_API_KEY mancante.",
            "fixture_id": fixture_id,
            "card": None,
            "score": None,
            "band": None,
            "feature_rows": [],
            "component_rows": [],
            "settled_snapshot_rows": [],
            "api_sports_rows": [],
            "api_sports_events": [],
            "api_sports_meta": None,
        }
        if client is None:
            return _TEMPLATES.TemplateResponse(request, "fixture.html", empty_ctx)
        error: str | None = None
        card: dict[str, Any] | None = None
        score_dict: dict[str, Any] | None = None
        fixture_row: dict[str, Any] | None = None
        try:
            detail = client.fixture_by_id(fixture_id)
            row: Any = detail
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
                fixture_row = row
                card = _row_card(row)
            # Fixture detail densifies history + standings so Alessandro sees
            # every available calculation input (U5 HA + classifica included).
            scored = score_fixture_live30(client, fixture_id, fetch_history=True)
            score_dict = live30_score_to_dict(scored)
        except GoalApiError as exc:
            error = str(exc)
        finally:
            client.close()

        api_dump = _load_api_sports_dump_for_fixture(fixture_row, card)
        api_rows = _api_sports_stat_rows_for_display(api_dump)
        api_events = (
            api_dump.get("events")
            if isinstance(api_dump, dict) and isinstance(api_dump.get("events"), list)
            else []
        )
        api_meta = (
            api_dump.get("meta")
            if isinstance(api_dump, dict) and isinstance(api_dump.get("meta"), dict)
            else None
        )

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
                "settled_snapshot_rows": _settled_snapshot_rows(score_dict),
                "api_sports_rows": api_rows,
                "api_sports_events": api_events,
                "api_sports_meta": api_meta,
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
            cards = _sort_live_watch(_filter_live_00([_row_card(r) for r in rows]))
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
            scheduled = list_todays_big5_fixtures(client)
            focus = select_watch_focus(
                candidates=candidates,
                live_cards=cards,
                schedule_cards=scheduled,
            )
            return JSONResponse(
                {
                    "live": cards,
                    "live30_candidates": candidates,
                    "scheduled": scheduled,
                    "focus": focus,
                    "focus_reason": focus_reason(focus),
                }
            )
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
            # Optional full API-Football dump (transparency; not index weights).
            detail = client.fixture_by_id(fixture_id)
            row: Any = detail
            if isinstance(detail, dict):
                for key in ("data", "response", "fixture"):
                    val = detail.get(key)
                    if isinstance(val, dict):
                        row = val
                        break
                    if isinstance(val, list) and val and isinstance(val[0], dict):
                        row = val[0]
                        break
            card = _row_card(row) if isinstance(row, dict) else None
            dump = _load_api_sports_dump_for_fixture(
                row if isinstance(row, dict) else None, card
            )
            data["api_sports"] = dump
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
