"""GOAL API WebSocket client for live clock / score / events.

Auth: Bearer ``GOAL_API_KEY`` (env only). Phase B uses this for the
≈30′ 0-0 trigger (WS-first); REST stats densified only in 28–32′.

Subscribe flow (GOAL API ENDPOINTS.md / SDK contract):

1. Optional REST snapshot: ``GET /fixtures/live`` for in-play match ids.
2. Gateway upgrade: ``wss://api.goal-api.com/ws`` (**not** ``/v1/ws``).
3. First frame must be auth::

       {"type": "auth", "apiKey": "<API_KEY>"}

4. Server replies ``auth_success`` (``maxSubscriptions``; **0** = no updates).
5. Subscribe::

       {"type": "subscribe", "resource": "match", "matchId": "<id>"}

6. Live frames: ``match_update`` (provider live shape — scores as strings,
   ``match_status`` is the minute clock), ``pong``, ``status``, ``error``,
   ``server_shutdown``. Optional nested ``clock`` when the gateway adds it.
7. Unsubscribe / ping as documented.

``match_update.data`` is the **provider live shape**, not the REST fixture
row — decode via :func:`parse_match_update`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlencode

import httpx

from goal_xg.clients.goal_api import DEFAULT_BASE_URL, GoalApiError
from goal_xg.live30.window import (
    in_live30_window,
    is_score_00,
    normalize_period,
    parse_minute,
)

DEFAULT_WS_URL = "wss://api.goal-api.com/ws"
# Documented browser path; nginx does not Upgrade here — prefer DEFAULT_WS_URL.
LEGACY_WS_URL = "wss://api.goal-api.com/v1/ws"

WS_MSG_AUTH_SUCCESS = "auth_success"
WS_MSG_MATCH_UPDATE = "match_update"
WS_MSG_PONG = "pong"
WS_MSG_STATUS = "status"
WS_MSG_ERROR = "error"
WS_MSG_SERVER_SHUTDOWN = "server_shutdown"
WS_MSG_SUBSCRIBE_RESPONSE = "subscribe_response"
WS_MSG_UNSUBSCRIBE_RESPONSE = "unsubscribe_response"


def _require_key(api_key: str | None) -> str:
    key = (api_key if api_key is not None else os.environ.get("GOAL_API_KEY", "")).strip()
    if not key:
        raise GoalApiError(
            "GOAL_API_KEY is missing. Set it in the environment or a local .env "
            "(see .env.example). Never commit the key."
        )
    return key


def auth_frame(*, api_key: str | None = None, ws_token: str | None = None) -> dict[str, str]:
    """First client message after upgrade (required by websocket-service)."""
    token = (ws_token or "").strip()
    if token:
        return {"type": "auth", "token": token}
    return {"type": "auth", "apiKey": _require_key(api_key)}


def subscribe_frame(match_id: int | str) -> dict[str, str]:
    return {"type": "subscribe", "resource": "match", "matchId": str(match_id)}


def unsubscribe_frame(match_id: int | str) -> dict[str, str]:
    return {"type": "unsubscribe", "resource": "match", "matchId": str(match_id)}


def ping_frame() -> dict[str, str]:
    return {"type": "ping"}


def status_frame() -> dict[str, str]:
    return {"type": "status"}


def handshake_headers(api_key: str | None = None) -> dict[str, str]:
    key = _require_key(api_key)
    return {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    }


def _as_int_score(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _first_present(data: Mapping[str, Any], *keys: str) -> Any:
    """Return the first key that is present (including numeric 0)."""
    for key in keys:
        if key in data and data[key] is not None and data[key] != "":
            return data[key]
    return None


@dataclass(frozen=True)
class LiveMatchState:
    """Normalized clock/score/events from a ``match_update`` (or REST live row)."""

    fixture_id: str | None
    provider_match_id: str | None
    minute: int | None
    elapsed: int | None
    extra: int | None
    period: str
    home_score: int | None
    away_score: int | None
    status_raw: str | None
    home_name: str | None = None
    away_name: str | None = None
    league_name: str | None = None
    country_name: str | None = None
    goals: tuple[dict[str, Any], ...] = ()
    cards: tuple[dict[str, Any], ...] = ()
    substitutions: Any = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_00(self) -> bool:
        return is_score_00(self.home_score, self.away_score)

    @property
    def in_live30_window(self) -> bool:
        return in_live30_window(self.minute, period=self.period)

    @property
    def is_live30_candidate(self) -> bool:
        """True when ≈30′ 1H and still 0-0 (primary Phase B emit gate)."""
        return self.in_live30_window and self.is_00


def parse_ws_message(raw: str | bytes | Mapping[str, Any]) -> dict[str, Any]:
    """Parse a WS frame into a dict; raise GoalApiError on bad JSON."""
    if isinstance(raw, Mapping):
        return dict(raw)
    text = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GoalApiError(f"GOAL WS invalid JSON: {text[:200]}") from exc
    if not isinstance(payload, dict):
        raise GoalApiError(f"GOAL WS expected object frame, got {type(payload)}")
    return payload


def _match_body(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    if payload.get("type") == WS_MSG_MATCH_UPDATE:
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        return None
    # Allow bare provider payloads (tests / REST-shaped live rows).
    if any(
        k in payload
        for k in (
            "match_hometeam_score",
            "match_status",
            "homeScore",
            "matchElapsed",
            "clock",
        )
    ):
        return dict(payload)
    return None


def parse_match_update(payload: Mapping[str, Any]) -> LiveMatchState | None:
    """Decode ``match_update`` (or nested ``data`` / bare provider live shape)."""
    data = _match_body(payload)
    if data is None:
        return None

    clock = data.get("clock") if isinstance(data.get("clock"), dict) else {}
    minute = parse_minute(
        clock.get("minute")
        if clock
        else None
    )
    if minute is None:
        minute = parse_minute(
            data.get("match_status")
            or data.get("matchMinute")
            or data.get("match_status_minute")
            or data.get("minute")
        )
    elapsed = clock.get("elapsed") if clock else data.get("matchElapsed")
    if elapsed is not None:
        try:
            elapsed = int(elapsed)
        except (TypeError, ValueError):
            elapsed = parse_minute(elapsed)
    else:
        elapsed = minute
    if minute is None and elapsed is not None:
        minute = int(elapsed)

    extra = clock.get("extra") if clock else data.get("matchExtra")
    try:
        extra_i = int(extra) if extra is not None else None
    except (TypeError, ValueError):
        extra_i = None

    period_raw = (
        (clock.get("period") if clock else None)
        or data.get("matchPeriod")
        or data.get("period")
    )
    # Infer 1H from minute when period missing (provider live shape often omits it).
    if period_raw is None and minute is not None:
        if minute <= 45:
            period_raw = "FIRST_HALF"
        elif minute <= 90:
            period_raw = "SECOND_HALF"
    period = normalize_period(str(period_raw) if period_raw is not None else None)

    home = _as_int_score(
        _first_present(
            data,
            "match_hometeam_score",
            "homeTeamScore",
            "homeScore",
            "home_score",
            "score_home",
        )
    )
    away = _as_int_score(
        _first_present(
            data,
            "match_awayteam_score",
            "awayTeamScore",
            "awayScore",
            "away_score",
            "score_away",
        )
    )

    goals_raw = data.get("goalscorer") or data.get("goals") or []
    cards_raw = data.get("cards") or []
    goals = tuple(g for g in goals_raw if isinstance(g, dict)) if isinstance(goals_raw, list) else ()
    cards = tuple(c for c in cards_raw if isinstance(c, dict)) if isinstance(cards_raw, list) else ()
    subs = data.get("substitutions")

    fixture_id = data.get("id") or data.get("fixtureId") or data.get("fixture_id")
    provider_id = data.get("match_id") or data.get("apiId") or data.get("matchId")

    return LiveMatchState(
        fixture_id=str(fixture_id) if fixture_id is not None else None,
        provider_match_id=str(provider_id) if provider_id is not None else None,
        minute=minute,
        elapsed=int(elapsed) if elapsed is not None else None,
        extra=extra_i,
        period=period,
        home_score=home,
        away_score=away,
        status_raw=str(data.get("match_status") or data.get("matchStatus") or data.get("status") or "")
        or None,
        home_name=data.get("match_hometeam_name") or data.get("homeTeamName"),
        away_name=data.get("match_awayteam_name") or data.get("awayTeamName"),
        league_name=data.get("league_name") or data.get("leagueName"),
        country_name=data.get("country_name") or data.get("countryName"),
        goals=goals,
        cards=cards,
        substitutions=subs,
        raw=data,
    )


def parse_live_clock(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """Extract clock + score dict (compat helper for older callers)."""
    state = parse_match_update(payload)
    if state is None:
        return None
    return {
        "match_id": state.fixture_id,
        "provider_match_id": state.provider_match_id,
        "minute": state.minute,
        "elapsed": state.elapsed,
        "extra": state.extra,
        "period": state.period,
        "home_score": state.home_score,
        "away_score": state.away_score,
        "status": state.status_raw,
        "is_00": state.is_00,
        "in_live30_window": state.in_live30_window,
        "is_live30_candidate": state.is_live30_candidate,
        "raw_type": payload.get("type"),
    }


def classify_ws_message(payload: Mapping[str, Any]) -> str:
    return str(payload.get("type") or "unknown")


class GoalWsClient:
    """Thin live socket helper. Fail-closed without ``GOAL_API_KEY``."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        ws_url: str | None = None,
        rest_base_url: str | None = None,
        timeout: float = 30.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._api_key = _require_key(api_key)
        self.ws_url = (ws_url or os.environ.get("GOAL_API_WS_URL") or DEFAULT_WS_URL).rstrip("/")
        self.rest_base_url = (
            rest_base_url or os.environ.get("GOAL_API_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self._timeout = timeout
        self._owns_http = http_client is None
        self._http = http_client or httpx.Client(
            base_url=self.rest_base_url,
            headers=handshake_headers(self._api_key),
            timeout=timeout,
        )
        self._ws: Any = None
        self.last_auth: dict[str, Any] | None = None
        self.subscribed: set[str] = set()

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> GoalWsClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def ws_url_with_token(self, ws_token: str) -> str:
        q = urlencode({"wsToken": ws_token})
        sep = "&" if "?" in self.ws_url else "?"
        return f"{self.ws_url}{sep}{q}"

    def mint_connect_token(self) -> dict[str, Any]:
        """POST /ws/token — short-lived single-use token for browser clients."""
        resp = self._http.post("/ws/token")
        if resp.status_code >= 400:
            raise GoalApiError(f"GOAL WS token {resp.status_code}: {resp.text[:500]}")
        return resp.json()

    def subscribe_messages(self, match_ids: Iterable[int | str]) -> list[str]:
        """JSON frames: auth first, then one subscribe per match (60/min cap)."""
        frames = [auth_frame(api_key=self._api_key)]
        frames.extend(subscribe_frame(mid) for mid in match_ids)
        return [json.dumps(f) for f in frames]

    async def connect(self) -> Any:
        """Upgrade to ``wss://api.goal-api.com/ws`` with Bearer handshake.

        Requires the optional ``websockets`` package. Does not subscribe; call
        ``send_auth`` then ``subscribe``.
        """
        try:
            import websockets  # type: ignore[import-untyped]
        except ImportError as exc:
            raise GoalApiError(
                "Optional dependency 'websockets' is required for GoalWsClient.connect(). "
                "Install with: pip install 'goal-xg[live]'"
            ) from exc

        self._ws = await websockets.connect(
            self.ws_url,
            additional_headers=handshake_headers(self._api_key),
            open_timeout=self._timeout,
        )
        return self._ws

    async def send_auth(self, ws: Any | None = None) -> dict[str, Any]:
        sock = ws or self._ws
        if sock is None:
            raise GoalApiError("WebSocket is not connected")
        await sock.send(json.dumps(auth_frame(api_key=self._api_key)))
        raw = await sock.recv()
        payload = parse_ws_message(raw)
        self.last_auth = payload
        if payload.get("type") == WS_MSG_ERROR or payload.get("success") is False:
            raise GoalApiError(f"GOAL WS auth error: {payload}")
        if payload.get("type") != WS_MSG_AUTH_SUCCESS:
            raise GoalApiError(
                f"GOAL WS expected auth_success, got type={payload.get('type')!r}"
            )
        return self.last_auth

    async def subscribe(self, match_ids: Sequence[int | str], ws: Any | None = None) -> None:
        sock = ws or self._ws
        if sock is None:
            raise GoalApiError("WebSocket is not connected")
        for mid in match_ids:
            await sock.send(json.dumps(subscribe_frame(mid)))
            self.subscribed.add(str(mid))

    async def unsubscribe(self, match_ids: Sequence[int | str], ws: Any | None = None) -> None:
        sock = ws or self._ws
        if sock is None:
            raise GoalApiError("WebSocket is not connected")
        for mid in match_ids:
            await sock.send(json.dumps(unsubscribe_frame(mid)))
            self.subscribed.discard(str(mid))

    async def ping(self, ws: Any | None = None) -> None:
        sock = ws or self._ws
        if sock is None:
            raise GoalApiError("WebSocket is not connected")
        await sock.send(json.dumps(ping_frame()))

    async def connect_and_subscribe(self, match_ids: Sequence[int | str]) -> Any:
        """Connect, auth, subscribe. Caller iterates the socket."""
        ws = await self.connect()
        await self.send_auth(ws)
        if isinstance(self.last_auth, dict):
            data = self.last_auth.get("data")
            max_sub = None
            if isinstance(data, dict):
                max_sub = data.get("maxSubscriptions")
            if max_sub is None:
                max_sub = self.last_auth.get("maxSubscriptions")
            if max_sub == 0:
                raise GoalApiError(
                    "GOAL WS auth_success.maxSubscriptions is 0 — "
                    "socket is up but match_update will never arrive (plan cap)."
                )
        await self.subscribe(match_ids, ws)
        return ws

    async def iter_messages(
        self,
        ws: Any | None = None,
        *,
        on_message: Callable[[dict[str, Any]], None] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield parsed JSON frames until the socket closes."""
        sock = ws or self._ws
        if sock is None:
            raise GoalApiError("WebSocket is not connected")
        async for raw in sock:
            payload = parse_ws_message(raw)
            if on_message is not None:
                on_message(payload)
            yield payload

    async def iter_match_states(
        self,
        ws: Any | None = None,
    ) -> AsyncIterator[LiveMatchState]:
        """Yield :class:`LiveMatchState` for each ``match_update`` frame."""
        async for payload in self.iter_messages(ws):
            if classify_ws_message(payload) == WS_MSG_SERVER_SHUTDOWN:
                break
            if classify_ws_message(payload) == WS_MSG_ERROR:
                raise GoalApiError(f"GOAL WS error: {payload}")
            state = parse_match_update(payload)
            if state is not None:
                yield state
