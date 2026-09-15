"""GOAL API REST client (Phase A).

Auth: Bearer token from env ``GOAL_API_KEY`` only — never hardcode.
Base: https://api.goal-api.com/v1

Phase A uses REST + historical. Live clock/score/events: ``goal_ws.py``
(WebSocket helpers; CLI does not run the socket continuously).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import httpx

DEFAULT_BASE_URL = "https://api.goal-api.com/v1"

# Canonical Big-5 codes used in this project (MVP locked).
BIG5_LEAGUE_CODES: tuple[str, ...] = ("PL", "PD", "SA", "BL1", "FL1")

# Name / country heuristics for discovering league IDs from /leagues.
# Provider IDs vary by season; we match on name+country then cache.
_BIG5_MATCHERS: tuple[tuple[str, str, str], ...] = (
    # (code, country_hint_lower, name_substr_lower)
    ("PL", "england", "premier league"),
    ("PD", "spain", "la liga"),
    ("SA", "italy", "serie a"),
    ("BL1", "germany", "bundesliga"),
    ("FL1", "france", "ligue 1"),
)

# Alternate name fragments (provider naming drift).
_NAME_ALIASES: dict[str, tuple[str, ...]] = {
    "PL": ("premier league", "premierleague"),
    "PD": ("la liga", "laliga", "primera division", "primera división"),
    "SA": ("serie a", "seria a"),
    "BL1": ("bundesliga",),
    "FL1": ("ligue 1", "ligue1"),
}


@dataclass(frozen=True)
class RateLimitInfo:
    """Parsed rate-limit headers from a GOAL API response."""

    limit: int | None = None
    remaining: int | None = None
    reset: int | None = None
    limit_type: str | None = None  # e.g. DAILY
    raw: Mapping[str, str] = field(default_factory=dict)


@dataclass
class Big5League:
    code: str
    # GOAL API uses opaque string CUIDs (not numeric provider ids).
    league_id: str
    name: str
    country: str
    season: int | None = None


def _as_league_id(value: Any) -> str | None:
    """Normalize a league id to a comparable string (CUID or numeric)."""
    if value is None or value == "":
        return None
    return str(value).strip()


class GoalApiError(RuntimeError):
    """HTTP or configuration error talking to GOAL API."""


class GoalApiClient:
    """Thin Bearer REST client with rate-limit header awareness."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        cache_dir: str | Path | None = None,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        key = (api_key if api_key is not None else os.environ.get("GOAL_API_KEY", "")).strip()
        if not key:
            raise GoalApiError(
                "GOAL_API_KEY is missing. Set it in the environment or a local .env "
                "(see .env.example). Never commit the key."
            )
        self.base_url = (base_url or os.environ.get("GOAL_API_BASE_URL") or DEFAULT_BASE_URL).rstrip(
            "/"
        )
        self._cache_dir = Path(
            cache_dir
            or os.environ.get("GOAL_API_CACHE_DIR")
            or Path.cwd() / ".cache" / "goal_api"
        )
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {key}",
                "Accept": "application/json",
            },
            timeout=timeout,
        )
        self.last_rate_limit: RateLimitInfo | None = None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> GoalApiClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- HTTP -------------------------------------------------------------

    def _parse_rate_limit(self, headers: httpx.Headers) -> RateLimitInfo:
        def _int(name: str) -> int | None:
            for key in (name, name.lower(), name.upper()):
                raw = headers.get(key)
                if raw is None:
                    continue
                try:
                    return int(raw)
                except ValueError:
                    return None
            return None

        # Common variants (GOAL probe: x-ratelimit-* + DAILY type).
        limit = _int("x-ratelimit-limit") or _int("x-ratelimit-limit-day")
        remaining = _int("x-ratelimit-remaining") or _int("x-ratelimit-remaining-day")
        reset = _int("x-ratelimit-reset")
        limit_type = headers.get("x-ratelimit-type") or headers.get("X-RateLimit-Type")
        raw = {k: v for k, v in headers.items() if "ratelimit" in k.lower() or "rate-limit" in k.lower()}
        info = RateLimitInfo(
            limit=limit,
            remaining=remaining,
            reset=reset,
            limit_type=limit_type,
            raw=raw,
        )
        self.last_rate_limit = info
        return info

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Perform a request; return parsed JSON (dict/list) or raise."""
        url_path = path if path.startswith("/") else f"/{path}"
        resp = self._client.request(method.upper(), url_path, **kwargs)
        self._parse_rate_limit(resp.headers)
        if resp.status_code == 429:
            raise GoalApiError(
                f"Rate limited (429). last_rate_limit={self.last_rate_limit!r}"
            )
        if resp.status_code >= 400:
            body = resp.text[:500]
            raise GoalApiError(f"GOAL API {resp.status_code} on {url_path}: {body}")
        if not resp.content:
            return None
        return resp.json()

    def get(self, path: str, **params: Any) -> Any:
        clean = {k: v for k, v in params.items() if v is not None}
        return self.request("GET", path, params=clean)

    # --- Big-5 league discovery / cache ----------------------------------

    def _league_cache_path(self) -> Path:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        return self._cache_dir / "big5_leagues.json"

    def load_cached_big5(self) -> list[Big5League] | None:
        path = self._league_cache_path()
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        out: list[Big5League] = []
        for row in data.get("leagues", []):
            lid = _as_league_id(row.get("league_id"))
            if lid is None:
                continue
            season = row.get("season")
            if season is not None and not isinstance(season, int):
                try:
                    season = int(str(season).split("/")[0])
                except (TypeError, ValueError):
                    season = None
            out.append(
                Big5League(
                    code=str(row["code"]),
                    league_id=lid,
                    name=str(row["name"]),
                    country=str(row.get("country", "")),
                    season=season,
                )
            )
        return out or None

    def save_cached_big5(self, leagues: list[Big5League]) -> Path:
        path = self._league_cache_path()
        payload = {
            "updated_at": int(time.time()),
            "leagues": [
                {
                    "code": lg.code,
                    "league_id": lg.league_id,
                    "name": lg.name,
                    "country": lg.country,
                    "season": lg.season,
                }
                for lg in leagues
            ],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    @staticmethod
    def _normalize_league_rows(payload: Any) -> list[dict[str, Any]]:
        """Normalize provider shapes into flat dicts with id/name/country/season."""
        if payload is None:
            return []
        rows: list[Any]
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            for key in ("data", "response", "leagues", "results"):
                if isinstance(payload.get(key), list):
                    rows = payload[key]
                    break
            else:
                rows = [payload]
        else:
            return []

        flat: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            # Nested {league: {...}, country: {...}} style
            league = row.get("league") if isinstance(row.get("league"), dict) else row
            country_obj = row.get("country")
            if isinstance(country_obj, dict):
                country = str(country_obj.get("name") or country_obj.get("code") or "")
            else:
                country = str(row.get("country") or league.get("country") or "")
            seasons = row.get("seasons")
            season: int | None = None
            if isinstance(seasons, list) and seasons:
                current = next(
                    (s for s in seasons if isinstance(s, dict) and s.get("current")),
                    seasons[-1] if isinstance(seasons[-1], dict) else None,
                )
                if isinstance(current, dict):
                    year = current.get("year") or current.get("season")
                    if year is not None:
                        try:
                            season = int(year)
                        except (TypeError, ValueError):
                            season = None
            lid = _as_league_id(
                league.get("id") or league.get("league_id") or row.get("id")
            )
            name = league.get("name") or row.get("name")
            if lid is None or not name:
                continue
            # Prefer nested country name; fall back to flat countryName.
            if not country:
                country = str(
                    row.get("countryName")
                    or league.get("countryName")
                    or league.get("country")
                    or ""
                )
            # Top-level season may be "2026/2027" when seasons[] is absent.
            if season is None:
                raw_season = league.get("season") or row.get("season")
                if raw_season is not None:
                    try:
                        season = int(str(raw_season).split("/")[0])
                    except (TypeError, ValueError):
                        season = None
            flat.append(
                {
                    "id": lid,
                    "name": str(name),
                    "country": country,
                    "season": season,
                }
            )
        return flat

    @staticmethod
    def _match_big5(row: dict[str, Any]) -> str | None:
        name = str(row.get("name", "")).lower()
        country = str(row.get("country", "")).lower()
        for code, country_hint, _ in _BIG5_MATCHERS:
            aliases = _NAME_ALIASES[code]
            if country_hint not in country and country not in country_hint:
                # allow empty country if name is unambiguous
                if country and country_hint not in country:
                    continue
            if any(alias in name for alias in aliases):
                # Prefer top division: skip "2. Bundesliga", "Serie A Women", etc.
                if "2." in name or "ii" == name.strip() or "women" in name or "femen" in name:
                    continue
                if code == "BL1" and "2. bundesliga" in name:
                    continue
                if code == "SA" and "serie b" in name:
                    continue
                return code
        return None

    def discover_big5_leagues(self, *, force_refresh: bool = False) -> list[Big5League]:
        """Return Big-5 leagues with provider IDs; cache on disk."""
        if not force_refresh:
            cached = self.load_cached_big5()
            if cached and len(cached) == len(BIG5_LEAGUE_CODES):
                return cached

        payload = self.get("/leagues")
        rows = self._normalize_league_rows(payload)
        found: dict[str, Big5League] = {}
        for row in rows:
            code = self._match_big5(row)
            if code is None or code in found:
                continue
            lid = _as_league_id(row.get("id"))
            if lid is None:
                continue
            found[code] = Big5League(
                code=code,
                league_id=lid,
                name=str(row["name"]),
                country=str(row.get("country", "")),
                season=row.get("season"),
            )

        missing = [c for c in BIG5_LEAGUE_CODES if c not in found]
        if missing:
            raise GoalApiError(
                f"Could not discover Big-5 league IDs for: {missing}. "
                f"Got {len(rows)} league rows from /leagues."
            )
        ordered = [found[c] for c in BIG5_LEAGUE_CODES]
        self.save_cached_big5(ordered)
        return ordered

    # --- Convenience endpoints (Phase A) ---------------------------------

    def fixtures_by_date(self, date: str, *, league_id: int | str | None = None) -> Any:
        """Fetch fixtures for YYYY-MM-DD; optionally filter by league id client-side later."""
        params: dict[str, Any] = {"date": date}
        if league_id is not None:
            params["league"] = league_id
        # Try common path shapes; GOAL docs use /fixtures
        return self.get("/fixtures", **params)

    def fixture_by_id(self, fixture_id: int | str) -> Any:
        return self.get(f"/fixtures/{fixture_id}")

    def fixtures_by_league(
        self,
        league_id: int | str,
        *,
        season: int | None = None,
        status: str | None = "FT",
    ) -> Any:
        params: dict[str, Any] = {"league": league_id}
        if season is not None:
            params["season"] = season
        if status is not None:
            params["status"] = status
        return self.get("/fixtures", **params)

    # --- Live / densified REST (Phase B — use sparingly; prefer WS) --------

    def fixtures_live(self, *, league_id: int | str | None = None) -> Any:
        """``GET /fixtures/live`` — in-play fixtures (one REST hit for candidate list)."""
        params: dict[str, Any] = {}
        if league_id is not None:
            params["leagueId"] = league_id
            params["league"] = league_id  # accept either query name
        return self.get("/fixtures/live", **params)

    def fixture_statistics(
        self,
        fixture_id: int | str,
        *,
        half: str | None = "1half",
    ) -> Any:
        """``GET /fixtures/:id/statistics`` — prefer ``half=1half`` for ~30′."""
        params: dict[str, Any] = {}
        if half is not None:
            params["half"] = half
        return self.get(f"/fixtures/{fixture_id}/statistics", **params)

    def fixture_events(self, fixture_id: int | str) -> Any:
        return self.get(f"/fixtures/{fixture_id}/events")

    def fixture_lineups(self, fixture_id: int | str) -> Any:
        return self.get(f"/fixtures/{fixture_id}/lineups")

    def fixture_cards(self, fixture_id: int | str) -> Any:
        return self.get(f"/fixtures/{fixture_id}/cards")

    def fixture_substitutions(self, fixture_id: int | str) -> Any:
        return self.get(f"/fixtures/{fixture_id}/substitutions")
