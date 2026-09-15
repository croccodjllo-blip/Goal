"""football-data.org v4 client — coach identity (not live/fixtures).

Auth: header ``X-Auth-Token`` from env ``FOOTBALL_DATA_API_KEY`` only — never hardcode.
Base: https://api.football-data.org/v4

Free tier ≈10 req/min — respect response headers and back off on 429.

Product split (locked):
- GOAL API = primary live / fixtures / historical scoring feed
- football-data.org = coach **identity** (name, id, currentTeam) for Big-5
- True coach-vs-coach H2H: **no endpoint** → keep ``coach_h2h`` omit + renorm
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

import httpx

DEFAULT_BASE_URL = "https://api.football-data.org/v4"

# Same Big-5 codes as GOAL client / football-data.org competition codes.
BIG5_COMPETITION_CODES: tuple[str, ...] = ("PL", "PD", "SA", "BL1", "FL1")

# Probe (2026-09): PL team.coach often null on free tier; PD/SA/BL1/FL1 filled.
PL_COACH_OFTEN_NULL = True


@dataclass(frozen=True)
class RateLimitInfo:
    """Parsed rate-limit / quota headers from football-data.org."""

    remaining_minute: int | None = None
    remaining_day: int | None = None
    reset: int | None = None
    retry_after: int | None = None
    raw: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CoachIdentity:
    """Soft coach identity feature (not coach-vs-coach H2H)."""

    person_id: int | None
    name: str | None
    nationality: str | None = None
    date_of_birth: str | None = None
    team_id: int | None = None
    team_name: str | None = None
    competition_code: str | None = None
    section: str | None = None  # e.g. "Coach" from /persons/{id}
    source: str = "football-data.org"
    raw_coach: Mapping[str, Any] | None = None

    @property
    def is_complete(self) -> bool:
        return self.person_id is not None and bool(self.name)


class FootballDataError(RuntimeError):
    """HTTP or configuration error talking to football-data.org."""


class FootballDataClient:
    """Thin X-Auth-Token REST client with free-tier rate awareness."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = 30.0,
        min_interval_s: float = 6.5,
        max_retries: int = 3,
        client: httpx.Client | None = None,
        sleep_fn: Any = time.sleep,
    ) -> None:
        key = (
            api_key
            if api_key is not None
            else (
                os.environ.get("FOOTBALL_DATA_API_KEY", "")
                or os.environ.get("FOOTBALL_DATA_TOKEN", "")
            )
        ).strip()
        if not key:
            raise FootballDataError(
                "FOOTBALL_DATA_API_KEY (or FOOTBALL_DATA_TOKEN) is missing. "
                "Set it in the environment or a local .env (see .env.example). "
                "Never commit the key."
            )
        self.base_url = (
            base_url or os.environ.get("FOOTBALL_DATA_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        # Free tier ~10/min → default ≥6s between calls unless headers say otherwise.
        self._min_interval_s = float(
            os.environ.get("FOOTBALL_DATA_MIN_INTERVAL_S", min_interval_s)
        )
        self._max_retries = max(0, int(max_retries))
        self._sleep = sleep_fn
        self._last_request_at = 0.0
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self.base_url,
            headers={
                "X-Auth-Token": key,
                "Accept": "application/json",
            },
            timeout=timeout,
        )
        self.last_rate_limit: RateLimitInfo | None = None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> FootballDataClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- HTTP / rate limit -------------------------------------------------

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

        remaining_minute = _int(
            "X-Requests-Available-Minute",
            "x-requests-available-minute",
            "X-RequestCounter-Remaining",
        )
        remaining_day = _int(
            "X-Requests-Available",
            "x-requests-available",
        )
        reset = _int("X-RequestCounter-Reset", "x-requestcounter-reset")
        retry_after = _int("Retry-After", "retry-after")
        raw = {
            k: v
            for k, v in headers.items()
            if any(
                tok in k.lower()
                for tok in ("request", "rate", "retry", "limit", "available")
            )
        }
        info = RateLimitInfo(
            remaining_minute=remaining_minute,
            remaining_day=remaining_day,
            reset=reset,
            retry_after=retry_after,
            raw=raw,
        )
        self.last_rate_limit = info
        return info

    def _throttle(self) -> None:
        if self._min_interval_s <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        if self._last_request_at > 0 and elapsed < self._min_interval_s:
            self._sleep(self._min_interval_s - elapsed)

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Perform a request; return parsed JSON or raise. Retries 429 with backoff."""
        url_path = path if path.startswith("/") else f"/{path}"
        attempt = 0
        while True:
            self._throttle()
            resp = self._client.request(method.upper(), url_path, **kwargs)
            self._last_request_at = time.monotonic()
            info = self._parse_rate_limit(resp.headers)

            if resp.status_code == 429:
                if attempt >= self._max_retries:
                    raise FootballDataError(
                        f"Rate limited (429) after {attempt + 1} attempts. "
                        f"last_rate_limit={info!r}"
                    )
                wait = info.retry_after
                if wait is None or wait < 1:
                    wait = min(60, int(self._min_interval_s * (2**attempt)) or 6)
                self._sleep(float(wait))
                attempt += 1
                continue

            if resp.status_code >= 400:
                body = resp.text[:500]
                raise FootballDataError(
                    f"football-data.org {resp.status_code} on {url_path}: {body}"
                )

            # If header says we are about to exhaust the minute budget, cool down.
            if info.remaining_minute is not None and info.remaining_minute <= 1:
                self._sleep(max(self._min_interval_s, float(info.reset or 6)))

            if not resp.content:
                return None
            return resp.json()

    def get(self, path: str, **params: Any) -> Any:
        clean = {k: v for k, v in params.items() if v is not None}
        return self.request("GET", path, params=clean or None)

    # --- Competitions / teams / persons ------------------------------------

    def competition(self, code: str) -> dict[str, Any]:
        """GET /competitions/{code} — metadata for PL/PD/SA/BL1/FL1."""
        payload = self.get(f"/competitions/{code}")
        if not isinstance(payload, dict):
            raise FootballDataError(f"Unexpected competition payload for {code}")
        return payload

    def competition_teams(self, code: str) -> list[dict[str, Any]]:
        """GET /competitions/{code}/teams — each team may include ``coach``."""
        payload = self.get(f"/competitions/{code}/teams")
        if not isinstance(payload, dict):
            raise FootballDataError(f"Unexpected teams payload for {code}")
        teams = payload.get("teams")
        if not isinstance(teams, list):
            return []
        return [t for t in teams if isinstance(t, dict)]

    def team(self, team_id: int | str) -> dict[str, Any]:
        """GET /teams/{id} — includes ``coach`` when available."""
        payload = self.get(f"/teams/{team_id}")
        if not isinstance(payload, dict):
            raise FootballDataError(f"Unexpected team payload for {team_id}")
        return payload

    def person(self, person_id: int | str) -> dict[str, Any]:
        """GET /persons/{id} — coaches return ``section: \"Coach\"`` + currentTeam."""
        payload = self.get(f"/persons/{person_id}")
        if not isinstance(payload, dict):
            raise FootballDataError(f"Unexpected person payload for {person_id}")
        return payload

    # --- Coach helpers -----------------------------------------------------

    @staticmethod
    def parse_coach_from_team(
        team_payload: Mapping[str, Any],
        *,
        competition_code: str | None = None,
    ) -> CoachIdentity | None:
        """Extract coach identity from a team object; None if coach missing/null."""
        coach = team_payload.get("coach")
        if not isinstance(coach, dict) or not coach:
            return None
        pid = coach.get("id")
        name = coach.get("name")
        if pid is None and not name:
            return None
        try:
            person_id = int(pid) if pid is not None else None
        except (TypeError, ValueError):
            person_id = None
        team_id = team_payload.get("id")
        try:
            tid = int(team_id) if team_id is not None else None
        except (TypeError, ValueError):
            tid = None
        return CoachIdentity(
            person_id=person_id,
            name=str(name) if name else None,
            nationality=str(coach["nationality"]) if coach.get("nationality") else None,
            date_of_birth=str(coach["dateOfBirth"]) if coach.get("dateOfBirth") else None,
            team_id=tid,
            team_name=str(team_payload["name"]) if team_payload.get("name") else None,
            competition_code=competition_code,
            section="Coach",
            raw_coach=dict(coach),
        )

    @staticmethod
    def parse_coach_from_person(person_payload: Mapping[str, Any]) -> CoachIdentity | None:
        """Build identity from /persons/{id}; require section Coach when present."""
        if not person_payload:
            return None
        section = person_payload.get("section")
        # Fail-closed if API labels a non-coach person explicitly.
        if section is not None and str(section).lower() not in {"coach", "coaching"}:
            return None
        pid = person_payload.get("id")
        name = person_payload.get("name")
        if pid is None and not name:
            return None
        try:
            person_id = int(pid) if pid is not None else None
        except (TypeError, ValueError):
            person_id = None
        current = person_payload.get("currentTeam")
        team_id: int | None = None
        team_name: str | None = None
        if isinstance(current, dict):
            try:
                team_id = int(current["id"]) if current.get("id") is not None else None
            except (TypeError, ValueError):
                team_id = None
            team_name = str(current["name"]) if current.get("name") else None
        return CoachIdentity(
            person_id=person_id,
            name=str(name) if name else None,
            nationality=(
                str(person_payload["nationality"])
                if person_payload.get("nationality")
                else None
            ),
            date_of_birth=(
                str(person_payload["dateOfBirth"])
                if person_payload.get("dateOfBirth")
                else None
            ),
            team_id=team_id,
            team_name=team_name,
            section=str(section) if section else "Coach",
            raw_coach=dict(person_payload),
        )

    def coach_for_team(self, team_id: int | str) -> CoachIdentity | None:
        """Resolve soft coach identity for a football-data.org team id."""
        return self.parse_coach_from_team(self.team(team_id))

    def coach_person(self, person_id: int | str) -> CoachIdentity | None:
        """Resolve coach via /persons/{id} (section Coach + currentTeam)."""
        return self.parse_coach_from_person(self.person(person_id))

    def coaches_for_competition(self, code: str) -> list[CoachIdentity]:
        """List coach identities for all teams in a Big-5 competition.

        Teams with null ``coach`` (common for PL on free tier) are skipped —
        fail-closed, no invented identity.
        """
        code = code.upper().strip()
        out: list[CoachIdentity] = []
        for team in self.competition_teams(code):
            identity = self.parse_coach_from_team(team, competition_code=code)
            if identity is not None and identity.is_complete:
                out.append(identity)
        return out

    def big5_coach_coverage(self) -> dict[str, dict[str, Any]]:
        """Per Big-5 competition: team count vs non-null coach count (slow; ~5 calls)."""
        summary: dict[str, dict[str, Any]] = {}
        for code in BIG5_COMPETITION_CODES:
            teams = self.competition_teams(code)
            with_coach = 0
            for t in teams:
                ident = self.parse_coach_from_team(t, competition_code=code)
                if ident is not None and ident.is_complete:
                    with_coach += 1
            summary[code] = {
                "teams": len(teams),
                "coaches_filled": with_coach,
                "coaches_null": len(teams) - with_coach,
                "note": (
                    "PL often null on free tier (probe 2026-09)"
                    if code == "PL"
                    else None
                ),
            }
        return summary
