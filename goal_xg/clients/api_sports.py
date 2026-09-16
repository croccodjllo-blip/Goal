"""API-Sports (API-Football) v3 client — shot-stat enrichment for product-xG.

Auth: header ``x-apisports-key`` from env ``API_SPORTS_KEY`` or ``APISPORTS_KEY``
only — never hardcode.
Base: https://v3.football.api-sports.io

Product split (locked):
- GOAL API = primary live / fixtures / historical scoring feed
- API-Sports = **enrich** missing shot-index fields (tiri, SoT, xG, xGOT,
  woodwork, blocked, inside/outside box) and optionally live/today fixtures
  when GOAL gaps
- Fail-closed if key missing when constructing the client
- Absent fields → omit + renorm (never invent)
- Big-5 only
"""

from __future__ import annotations

import os
import re
import time
import unicodedata
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Sequence

import httpx

from goal_xg.live30.stats import LiveVolumeStats, _map_stat_type, _to_float

DEFAULT_BASE_URL = "https://v3.football.api-sports.io"

# Canonical Big-5 codes → API-Football league ids (stable).
BIG5_LEAGUE_IDS: dict[str, int] = {
    "PL": 39,  # Premier League
    "PD": 140,  # La Liga
    "SA": 135,  # Serie A
    "BL1": 78,  # Bundesliga
    "FL1": 61,  # Ligue 1
}
BIG5_LEAGUE_ID_SET: frozenset[int] = frozenset(BIG5_LEAGUE_IDS.values())
# ``live`` query accepts hyphen-joined league ids.
BIG5_LIVE_PARAM = "-".join(str(i) for i in BIG5_LEAGUE_IDS.values())

# Shot-index fields that API-Sports may fill when GOAL is sparse.
SHOT_ENRICH_FIELDS: tuple[str, ...] = (
    "shots_total_home",
    "shots_total_away",
    "sot_home",
    "sot_away",
    "shot_xg_home",
    "shot_xg_away",
    "xgot_home",
    "xgot_away",
    "woodwork_home",
    "woodwork_away",
    "shots_off_home",
    "shots_off_away",
    "shots_blocked_home",
    "shots_blocked_away",
    "shots_inside_box_home",
    "shots_inside_box_away",
    "shots_outside_box_home",
    "shots_outside_box_away",
)

# Italian labels for API-Football ``statistics[].type`` (display dump).
# Unknown types fall back to the raw English key in the UI.
STAT_LABELS_IT: dict[str, str] = {
    "Total Shots": "Tiri totali",
    "Shots on Goal": "Tiri in porta",
    "Shots off Goal": "Tiri fuori",
    "Blocked Shots": "Tiri respinti",
    "Shots insidebox": "Tiri in area",
    "Shots outsidebox": "Tiri da fuori",
    "Hit Woodwork": "Pali e traverse",
    "expected_goals": "Goal attesi (xG)",
    "Expected Goals": "Goal attesi (xG)",
    "xg": "Goal attesi (xG)",
    "expected_goals_on_target": "xGOT",
    "Expected Goals on Target": "xGOT",
    "Ball Possession": "Possesso palla",
    "Total passes": "Passaggi totali",
    "Passes accurate": "Passaggi completati",
    "Passes %": "Precisione passaggi",
    "Corner Kicks": "Calci d'angolo",
    "Offsides": "Fuorigioco",
    "Fouls": "Falli",
    "Yellow Cards": "Cartellini gialli",
    "Red Cards": "Cartellini rossi",
    "Goalkeeper Saves": "Parate",
    "Free Kicks": "Calci di punizione",
    "Throw Ins": "Rimesse laterali",
    "Goal Kicks": "Rimesse dal fondo",
    "Attacks": "Attacchi",
    "Dangerous Attacks": "Attacchi pericolosi",
    "Tackles": "Contrasti",
    "Interceptions": "Intercetti",
    "Clearances": "Spazzate",
    "Counter Attacks": "Contropiede",
    "Goals prevented": "Gol prevenuti",
    "Big Chances": "Grandissime occasioni",
    "Big Chances Missed": "Occasioni sprecate",
    "Big Chances Scored": "Occasioni convertite",
    "Shots on Goal Rate": "Tasso tiri in porta",
}

# Types that map into the existing product-index shot criteria (enrich only).
# Everything else is display-only — never auto-weighted into xG.
INDEX_MAPPED_STAT_TYPES: frozenset[str] = frozenset(
    {
        "Total Shots",
        "Shots on Goal",
        "Shots off Goal",
        "Blocked Shots",
        "Shots insidebox",
        "Shots outsidebox",
        "Hit Woodwork",
        "expected_goals",
        "Expected Goals",
        "xg",
        "expected_goals_on_target",
        "Expected Goals on Target",
    }
)


@dataclass(frozen=True)
class ApiSportsStatRow:
    """One fixture statistic type with home/away (full + optional 1H)."""

    type: str
    label: str
    home: Any = None
    away: Any = None
    home_1h: Any = None
    away_1h: Any = None
    in_index: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "label": self.label,
            "home": self.home,
            "away": self.away,
            "home_1h": self.home_1h,
            "away_1h": self.away_1h,
            "in_index": self.in_index,
        }


def label_stat_type(stat_type: str) -> str:
    """Italian label when known; otherwise the raw API type string."""
    raw = str(stat_type or "").strip()
    if not raw:
        return "—"
    if raw in STAT_LABELS_IT:
        return STAT_LABELS_IT[raw]
    # Case-insensitive fallback.
    for key, label in STAT_LABELS_IT.items():
        if key.lower() == raw.lower():
            return label
    return raw


def _stat_map(rows: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not rows:
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        typ = str(row.get("type") or row.get("name") or "").strip()
        if not typ or typ in out:
            continue
        out[typ] = row.get("value")
    return out


def flatten_fixture_statistics(
    payload: Any,
    *,
    home_team_id: int | None = None,
    home_team_name: str | None = None,
    away_team_id: int | None = None,
    away_team_name: str | None = None,
) -> list[ApiSportsStatRow]:
    """Flatten API-Sports team statistics blocks into display rows (all types)."""
    rows: list[dict[str, Any]]
    if isinstance(payload, dict):
        inner = payload.get("response")
        rows = [r for r in inner if isinstance(r, dict)] if isinstance(inner, list) else []
        if not rows and ("team" in payload or "statistics" in payload):
            rows = [payload]
    elif isinstance(payload, list):
        rows = [r for r in payload if isinstance(r, dict)]
    else:
        rows = []
    if not rows:
        return []

    def _team_id(block: Mapping[str, Any]) -> int | None:
        team = block.get("team") if isinstance(block.get("team"), dict) else {}
        try:
            return int(team["id"]) if team.get("id") is not None else None
        except (TypeError, ValueError, KeyError):
            return None

    def _team_name(block: Mapping[str, Any]) -> str | None:
        team = block.get("team") if isinstance(block.get("team"), dict) else {}
        name = team.get("name")
        return str(name) if name else None

    home_block: Mapping[str, Any] | None = None
    away_block: Mapping[str, Any] | None = None
    for block in rows:
        tid = _team_id(block)
        tname = _team_name(block)
        if home_block is None and (
            (home_team_id is not None and tid == home_team_id)
            or (home_team_name and _team_names_match(tname, home_team_name))
        ):
            home_block = block
            continue
        if away_block is None and (
            (away_team_id is not None and tid == away_team_id)
            or (away_team_name and _team_names_match(tname, away_team_name))
        ):
            away_block = block
            continue
    if home_block is None and rows:
        home_block = rows[0]
    if away_block is None and len(rows) > 1:
        away_block = rows[1]

    home_full = _stat_map(
        home_block.get("statistics") if isinstance(home_block, Mapping) else None
    )
    away_full = _stat_map(
        away_block.get("statistics") if isinstance(away_block, Mapping) else None
    )
    home_1h = _stat_map(
        home_block.get("statistics_1h") if isinstance(home_block, Mapping) else None
    )
    away_1h = _stat_map(
        away_block.get("statistics_1h") if isinstance(away_block, Mapping) else None
    )

    # Preserve first-seen order: home full → away extras → 1H-only keys.
    order: list[str] = []
    for key in (*home_full.keys(), *away_full.keys(), *home_1h.keys(), *away_1h.keys()):
        if key not in order:
            order.append(key)

    out: list[ApiSportsStatRow] = []
    for typ in order:
        in_index = typ in INDEX_MAPPED_STAT_TYPES or any(
            t.lower() == typ.lower() for t in INDEX_MAPPED_STAT_TYPES
        )
        out.append(
            ApiSportsStatRow(
                type=typ,
                label=label_stat_type(typ),
                home=home_full.get(typ),
                away=away_full.get(typ),
                home_1h=home_1h.get(typ),
                away_1h=away_1h.get(typ),
                in_index=in_index,
            )
        )
    return out


def api_sports_key_configured() -> bool:
    """True when ``API_SPORTS_KEY`` or ``APISPORTS_KEY`` is non-empty."""
    return bool(_read_api_key())


def _read_api_key(explicit: str | None = None) -> str:
    if explicit is not None:
        return explicit.strip()
    return (
        os.environ.get("API_SPORTS_KEY", "")
        or os.environ.get("APISPORTS_KEY", "")
    ).strip()


def _norm_team(name: str | None) -> str:
    """Normalize club names for fuzzy match (accents / punctuation / FC)."""
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    # Drop common suffixes that differ across providers.
    for tok in (
        "fc",
        "cf",
        "afc",
        "sc",
        "ac",
        "as",
        "ss",
        "ud",
        "cd",
        "rc",
        "club",
        "calcio",
        "united",
    ):
        text = re.sub(rf"\b{tok}\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _team_names_match(a: str | None, b: str | None) -> bool:
    na, nb = _norm_team(a), _norm_team(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if na in nb or nb in na:
        return True
    # Token overlap ≥ 2 when both have multiple tokens.
    ta, tb = set(na.split()), set(nb.split())
    if len(ta) >= 2 and len(tb) >= 2 and len(ta & tb) >= 2:
        return True
    return False


@dataclass(frozen=True)
class RateLimitInfo:
    """Parsed rate-limit headers from an API-Sports response."""

    limit: int | None = None
    remaining: int | None = None
    reset: int | None = None
    raw: Mapping[str, str] = field(default_factory=dict)


class ApiSportsError(RuntimeError):
    """HTTP or configuration error talking to API-Sports."""


class ApiSportsClient:
    """Thin ``x-apisports-key`` REST client (Big-5 enrichment)."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = 30.0,
        min_interval_s: float = 0.35,
        max_retries: int = 2,
        client: httpx.Client | None = None,
        sleep_fn: Any = time.sleep,
    ) -> None:
        key = _read_api_key(api_key if api_key is not None else None)
        if not key:
            raise ApiSportsError(
                "API_SPORTS_KEY (or APISPORTS_KEY) is missing. "
                "Set it in the environment or a local .env (see .env.example). "
                "Never commit the key."
            )
        self.base_url = (
            base_url or os.environ.get("API_SPORTS_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self._min_interval_s = float(
            os.environ.get("API_SPORTS_MIN_INTERVAL_S", min_interval_s)
        )
        self._max_retries = max(0, int(max_retries))
        self._sleep = sleep_fn
        self._last_request_at = 0.0
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self.base_url,
            headers={
                "x-apisports-key": key,
                "Accept": "application/json",
            },
            timeout=timeout,
        )
        self.last_rate_limit: RateLimitInfo | None = None
        self.last_errors: Any = None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> ApiSportsClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- HTTP -------------------------------------------------------------

    def _parse_rate_limit(self, headers: httpx.Headers) -> RateLimitInfo:
        def _int(*names: str) -> int | None:
            for name in names:
                raw = headers.get(name)
                if raw is None:
                    continue
                try:
                    return int(float(str(raw).strip()))
                except ValueError:
                    continue
            return None

        limit = _int(
            "x-ratelimit-requests-limit",
            "X-RateLimit-Requests-Limit",
            "x-ratelimit-limit",
        )
        remaining = _int(
            "x-ratelimit-requests-remaining",
            "X-RateLimit-Requests-Remaining",
            "x-ratelimit-remaining",
        )
        reset = _int(
            "x-ratelimit-requests-reset",
            "X-RateLimit-Requests-Reset",
            "x-ratelimit-reset",
        )
        raw = {
            k: v
            for k, v in headers.items()
            if "ratelimit" in k.lower() or "rate-limit" in k.lower()
        }
        info = RateLimitInfo(limit=limit, remaining=remaining, reset=reset, raw=raw)
        self.last_rate_limit = info
        return info

    def _throttle(self) -> None:
        if self._min_interval_s <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        if self._last_request_at > 0 and elapsed < self._min_interval_s:
            self._sleep(self._min_interval_s - elapsed)

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Perform a request; return parsed JSON ``response`` body or raise."""
        url_path = path if path.startswith("/") else f"/{path}"
        attempt = 0
        while True:
            self._throttle()
            resp = self._client.request(method.upper(), url_path, **kwargs)
            self._last_request_at = time.monotonic()
            info = self._parse_rate_limit(resp.headers)

            if resp.status_code == 429:
                if attempt >= self._max_retries:
                    raise ApiSportsError(
                        f"Rate limited (429) after {attempt + 1} attempts. "
                        f"last_rate_limit={info!r}"
                    )
                wait = info.reset or min(60, int(2**attempt) or 1)
                self._sleep(float(max(1, wait)))
                attempt += 1
                continue

            if resp.status_code >= 400:
                body = resp.text[:500]
                raise ApiSportsError(
                    f"API-Sports {resp.status_code} on {url_path}: {body}"
                )

            if not resp.content:
                self.last_errors = None
                return None

            payload = resp.json()
            if isinstance(payload, dict):
                errors = payload.get("errors")
                # Empty list / empty dict = ok.
                if errors and errors not in ([], {}, ""):
                    self.last_errors = errors
                    raise ApiSportsError(
                        f"API-Sports errors on {url_path}: {errors}"
                    )
                self.last_errors = None
                return payload.get("response", payload)
            self.last_errors = None
            return payload

    def get(self, path: str, **params: Any) -> Any:
        clean = {k: v for k, v in params.items() if v is not None}
        return self.request("GET", path, params=clean or None)

    # --- Endpoints --------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """``GET /status`` — account / subscription / daily request budget."""
        payload = self.get("/status")
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            return payload[0]
        return {}

    def fixtures(self, **params: Any) -> list[dict[str, Any]]:
        """``GET /fixtures`` — pass API-Sports query params (date, league, …)."""
        payload = self.get("/fixtures", **params)
        if isinstance(payload, list):
            return [r for r in payload if isinstance(r, dict)]
        return []

    def fixtures_live(self, *, big5_only: bool = True) -> list[dict[str, Any]]:
        """``GET /fixtures?live=…`` — in-play fixtures (Big-5 ids by default)."""
        live = BIG5_LIVE_PARAM if big5_only else "all"
        rows = self.fixtures(live=live)
        if not big5_only:
            return rows
        out: list[dict[str, Any]] = []
        for row in rows:
            league = row.get("league") if isinstance(row.get("league"), dict) else {}
            try:
                lid = int(league.get("id")) if league.get("id") is not None else None
            except (TypeError, ValueError):
                lid = None
            if lid in BIG5_LEAGUE_ID_SET:
                out.append(row)
        return out

    def fixtures_by_date(
        self,
        date: str,
        *,
        league_id: int | None = None,
        season: int | None = None,
        big5_only: bool = True,
    ) -> list[dict[str, Any]]:
        """``GET /fixtures`` for ``YYYY-MM-DD`` (season often required on free)."""
        if league_id is not None:
            return self.fixtures(date=date, league=league_id, season=season)
        if not big5_only:
            return self.fixtures(date=date, season=season)
        out: list[dict[str, Any]] = []
        for lid in BIG5_LEAGUE_IDS.values():
            try:
                out.extend(self.fixtures(date=date, league=lid, season=season))
            except ApiSportsError:
                continue
        return out

    def fixture_statistics(
        self,
        fixture_id: int | str,
        *,
        half: bool = True,
        team: int | None = None,
    ) -> list[dict[str, Any]]:
        """``GET /fixtures/statistics`` — prefer ``half=true`` for 1H snapshot."""
        params: dict[str, Any] = {"fixture": fixture_id}
        if half:
            params["half"] = "true"
        if team is not None:
            params["team"] = team
        payload = self.get("/fixtures/statistics", **params)
        if isinstance(payload, list):
            return [r for r in payload if isinstance(r, dict)]
        return []

    def fixture_events(
        self,
        fixture_id: int | str,
        *,
        team: int | None = None,
        event_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """``GET /fixtures/events`` — goals / cards / subs (display, not index)."""
        params: dict[str, Any] = {"fixture": fixture_id}
        if team is not None:
            params["team"] = team
        if event_type is not None:
            params["type"] = event_type
        payload = self.get("/fixtures/events", **params)
        if isinstance(payload, list):
            return [r for r in payload if isinstance(r, dict)]
        return []

    def load_fixture_stat_dump(
        self,
        *,
        home_name: str | None,
        away_name: str | None,
        date: str | None = None,
        season: int | None = None,
        api_id: int | str | None = None,
        fixture_id: int | str | None = None,
        live_rows: Sequence[Mapping[str, Any]] | None = None,
        include_events: bool = True,
        prefer_half: bool = True,
    ) -> dict[str, Any]:
        """Resolve fixture + return full stats table (all types) for UI dump.

        Index blend is unchanged — ``in_index`` only flags types that *may*
        fill existing shot criteria; other fields are transparency-only.
        """
        fid: int | None
        if fixture_id is not None:
            try:
                fid = int(str(fixture_id).strip())
            except (TypeError, ValueError):
                fid = None
        else:
            fid = self.find_fixture_id(
                home_name=home_name,
                away_name=away_name,
                date=date,
                season=season,
                api_id=api_id,
                live_rows=live_rows,
            )
        meta: dict[str, Any] = {
            "configured": True,
            "fixture_id": fid,
            "home_name": home_name,
            "away_name": away_name,
            "source": "api-football",
            "half": prefer_half,
            "error": None,
            "events_n": 0,
            "stat_types": [],
        }
        if fid is None:
            meta["error"] = "fixture_unresolved"
            return {"meta": meta, "rows": [], "events": []}

        try:
            payload = self.fixture_statistics(fid, half=prefer_half)
        except ApiSportsError as exc:
            meta["error"] = str(exc)[:200]
            return {"meta": meta, "rows": [], "events": []}

        rows = flatten_fixture_statistics(
            payload,
            home_team_name=home_name,
            away_team_name=away_name,
        )
        meta["stat_types"] = [r.type for r in rows]

        events: list[dict[str, Any]] = []
        if include_events:
            try:
                raw_events = self.fixture_events(fid)
            except ApiSportsError:
                raw_events = []
            for ev in raw_events:
                time_obj = ev.get("time") if isinstance(ev.get("time"), dict) else {}
                team = ev.get("team") if isinstance(ev.get("team"), dict) else {}
                player = ev.get("player") if isinstance(ev.get("player"), dict) else {}
                events.append(
                    {
                        "minute": time_obj.get("elapsed"),
                        "extra": time_obj.get("extra"),
                        "team": team.get("name"),
                        "player": player.get("name"),
                        "type": ev.get("type"),
                        "detail": ev.get("detail"),
                    }
                )
            meta["events_n"] = len(events)

        return {
            "meta": meta,
            "rows": [r.to_dict() for r in rows],
            "events": events,
        }

    # --- Parse / merge ----------------------------------------------------

    @staticmethod
    def parse_statistics_payload(
        payload: Any,
        *,
        home_team_id: int | None = None,
        home_team_name: str | None = None,
        away_team_id: int | None = None,
        away_team_name: str | None = None,
        prefer_half: bool = True,
    ) -> LiveVolumeStats:
        """Map API-Sports team statistics blocks → :class:`LiveVolumeStats`."""
        rows: list[dict[str, Any]]
        if isinstance(payload, dict):
            inner = payload.get("response")
            rows = [r for r in inner if isinstance(r, dict)] if isinstance(inner, list) else []
            if not rows and ("team" in payload or "statistics" in payload):
                rows = [payload]
        elif isinstance(payload, list):
            rows = [r for r in payload if isinstance(r, dict)]
        else:
            rows = []

        if not rows:
            return LiveVolumeStats(
                source_half="api_sports",
                notes=("api_sports_no_statistics",),
            )

        home_block: Mapping[str, Any] | None = None
        away_block: Mapping[str, Any] | None = None

        def _team_id(block: Mapping[str, Any]) -> int | None:
            team = block.get("team") if isinstance(block.get("team"), dict) else {}
            try:
                return int(team["id"]) if team.get("id") is not None else None
            except (TypeError, ValueError, KeyError):
                return None

        def _team_name(block: Mapping[str, Any]) -> str | None:
            team = block.get("team") if isinstance(block.get("team"), dict) else {}
            name = team.get("name")
            return str(name) if name else None

        for block in rows:
            tid = _team_id(block)
            tname = _team_name(block)
            if home_block is None and (
                (home_team_id is not None and tid == home_team_id)
                or (home_team_name and _team_names_match(tname, home_team_name))
            ):
                home_block = block
                continue
            if away_block is None and (
                (away_team_id is not None and tid == away_team_id)
                or (away_team_name and _team_names_match(tname, away_team_name))
            ):
                away_block = block
                continue

        # Fallback: API-Sports usually returns home then away.
        if home_block is None and rows:
            home_block = rows[0]
        if away_block is None and len(rows) > 1:
            away_block = rows[1]
        if away_block is None and home_block is not None and len(rows) == 1:
            away_block = None

        def _stat_rows(block: Mapping[str, Any] | None) -> list[dict[str, Any]]:
            if not isinstance(block, dict):
                return []
            if prefer_half:
                half = block.get("statistics_1h")
                if isinstance(half, list) and half:
                    return [r for r in half if isinstance(r, dict)]
            stats = block.get("statistics")
            if isinstance(stats, list):
                return [r for r in stats if isinstance(r, dict)]
            return []

        home_vals: dict[str, float | None] = {}
        away_vals: dict[str, float | None] = {}
        for side, block, bucket in (
            ("home", home_block, home_vals),
            ("away", away_block, away_vals),
        ):
            _ = side
            for row in _stat_rows(block):
                typ = str(row.get("type") or row.get("name") or "").strip().lower()
                key = _map_stat_type(typ)
                if key is None:
                    continue
                if key not in bucket:
                    bucket[key] = _to_float(row.get("value"))

        half_label = "api_sports_1h" if prefer_half else "api_sports"
        if prefer_half and home_block and not isinstance(home_block.get("statistics_1h"), list):
            half_label = "api_sports"

        notes: list[str] = ["enriched_api_sports"]
        if not home_vals and not away_vals:
            notes = ["api_sports_no_parseable_shot_stats"]

        return LiveVolumeStats(
            shots_total_home=home_vals.get("shots_total"),
            shots_total_away=away_vals.get("shots_total"),
            sot_home=home_vals.get("sot"),
            sot_away=away_vals.get("sot"),
            shot_xg_home=home_vals.get("shot_xg"),
            shot_xg_away=away_vals.get("shot_xg"),
            xgot_home=home_vals.get("xgot"),
            xgot_away=away_vals.get("xgot"),
            woodwork_home=home_vals.get("woodwork"),
            woodwork_away=away_vals.get("woodwork"),
            shots_off_home=home_vals.get("shots_off"),
            shots_off_away=away_vals.get("shots_off"),
            shots_blocked_home=home_vals.get("shots_blocked"),
            shots_blocked_away=away_vals.get("shots_blocked"),
            shots_inside_box_home=home_vals.get("shots_inside_box"),
            shots_inside_box_away=away_vals.get("shots_inside_box"),
            shots_outside_box_home=home_vals.get("shots_outside_box"),
            shots_outside_box_away=away_vals.get("shots_outside_box"),
            yellows_home=home_vals.get("yellows"),
            yellows_away=away_vals.get("yellows"),
            red_home=home_vals.get("red"),
            red_away=away_vals.get("red"),
            source_half=half_label,
            notes=tuple(notes),
        )

    @staticmethod
    def merge_fill_shot_stats(
        primary: LiveVolumeStats,
        enrich: LiveVolumeStats | None,
    ) -> LiveVolumeStats:
        """Fill ``None`` shot-index fields from ``enrich``; never overwrite."""
        if enrich is None:
            return primary
        updates: dict[str, Any] = {}
        for name in SHOT_ENRICH_FIELDS:
            cur = getattr(primary, name)
            alt = getattr(enrich, name, None)
            if cur is None and alt is not None:
                updates[name] = alt
        if not updates:
            # Still record that we attempted enrichment when enrich has notes.
            if enrich.notes and "enriched_api_sports" in enrich.notes:
                notes = tuple(
                    dict.fromkeys([*primary.notes, "api_sports_no_gaps"])
                )
                return replace(primary, notes=notes)
            return primary
        notes = tuple(
            dict.fromkeys(
                [
                    *primary.notes,
                    *enrich.notes,
                    "filled_from_api_sports",
                ]
            )
        )
        source = primary.source_half or enrich.source_half
        if primary.source_half and enrich.source_half and primary.source_half != enrich.source_half:
            source = f"{primary.source_half}+{enrich.source_half}"
        return replace(primary, source_half=source, notes=notes, **updates)

    def find_fixture_id(
        self,
        *,
        home_name: str | None,
        away_name: str | None,
        date: str | None = None,
        season: int | None = None,
        api_id: int | str | None = None,
        live_rows: Sequence[Mapping[str, Any]] | None = None,
    ) -> int | None:
        """Resolve an API-Sports fixture id (live name match → validated apiId → date).

        GOAL ``apiId`` is **not** trusted blindly — provider ids can point at the
        wrong match. Prefer live Big-5 name match when home/away are known.
        """

        def _scan(rows: Sequence[Mapping[str, Any]]) -> int | None:
            for row in rows:
                teams = row.get("teams") if isinstance(row.get("teams"), dict) else {}
                home = teams.get("home") if isinstance(teams.get("home"), dict) else {}
                away = teams.get("away") if isinstance(teams.get("away"), dict) else {}
                if _team_names_match(home.get("name"), home_name) and _team_names_match(
                    away.get("name"), away_name
                ):
                    fix = row.get("fixture") if isinstance(row.get("fixture"), dict) else {}
                    fid = fix.get("id") or row.get("id")
                    try:
                        return int(fid) if fid is not None else None
                    except (TypeError, ValueError):
                        return None
            return None

        # 1) Live name match first (most reliable for in-play densify / detail).
        if home_name and away_name:
            if live_rows is not None:
                found = _scan(live_rows)
                if found is not None:
                    return found
            else:
                try:
                    found = _scan(self.fixtures_live(big5_only=True))
                    if found is not None:
                        return found
                except ApiSportsError:
                    pass

        # 2) Explicit apiId — only accept if team names match (or names unknown).
        if api_id is not None and str(api_id).strip():
            try:
                cand = int(str(api_id).strip())
            except (TypeError, ValueError):
                cand = None
            if cand is not None:
                if not home_name and not away_name:
                    return cand
                try:
                    rows = self.fixtures(id=cand)
                except ApiSportsError:
                    rows = []
                if rows:
                    verified = _scan(rows)
                    if verified is not None:
                        return verified
                elif not home_name or not away_name:
                    return cand
                # Mismatch / empty → fall through (do not use bad GOAL apiId).

        # 3) Date + Big-5 leagues (free plans often reject history).
        if date and home_name and away_name:
            seasons: list[int | None] = [season] if season is not None else [None]
            if season is None:
                try:
                    seasons.append(int(str(date)[:4]))
                except ValueError:
                    pass
            for seas in seasons:
                try:
                    rows = self.fixtures_by_date(date, season=seas, big5_only=True)
                except ApiSportsError:
                    continue
                found = _scan(rows)
                if found is not None:
                    return found
        return None

    def enrich_live_volume(
        self,
        primary: LiveVolumeStats,
        *,
        fixture_id: int | str | None = None,
        home_name: str | None = None,
        away_name: str | None = None,
        home_team_id: int | None = None,
        away_team_id: int | None = None,
        date: str | None = None,
        season: int | None = None,
        api_id: int | str | None = None,
        live_rows: Sequence[Mapping[str, Any]] | None = None,
        prefer_half: bool = True,
    ) -> LiveVolumeStats:
        """Fetch API-Sports stats and fill missing shot fields on ``primary``."""
        fid: int | None
        if fixture_id is not None:
            try:
                fid = int(str(fixture_id).strip())
            except (TypeError, ValueError):
                fid = None
        else:
            fid = self.find_fixture_id(
                home_name=home_name,
                away_name=away_name,
                date=date,
                season=season,
                api_id=api_id,
                live_rows=live_rows,
            )
        if fid is None:
            notes = tuple(dict.fromkeys([*primary.notes, "api_sports_fixture_unresolved"]))
            return replace(primary, notes=notes)

        try:
            payload = self.fixture_statistics(fid, half=prefer_half)
        except ApiSportsError:
            notes = tuple(dict.fromkeys([*primary.notes, "api_sports_stats_failed"]))
            return replace(primary, notes=notes)

        enrich = self.parse_statistics_payload(
            payload,
            home_team_id=home_team_id,
            home_team_name=home_name,
            away_team_id=away_team_id,
            away_team_name=away_name,
            prefer_half=prefer_half,
        )
        return self.merge_fill_shot_stats(primary, enrich)


def maybe_client() -> ApiSportsClient | None:
    """Return a client when configured; ``None`` if key missing (no raise)."""
    if not api_sports_key_configured():
        return None
    try:
        return ApiSportsClient()
    except ApiSportsError:
        return None
