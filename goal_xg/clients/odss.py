"""odss-api.com client — stub only (no live HTTP until ODSS_API_KEY + greenlight).

Docs: https://odss-api.com — sport ``calcio``, market ``1x2``,
bookmakers CSV e.g. ``snai,sisal,bet365``.

Auth: header ``x-api-key`` from env ``ODSS_API_KEY`` only — never hardcode.

This module intentionally does **not** perform network calls from the web
compare path. ``resolve_provider()`` returns the mock provider until the
TODO HTTP methods below are implemented and explicitly wired.
"""

from __future__ import annotations

import os
from typing import Any, Sequence

DEFAULT_BASE_URL = "https://odss-api.com/api/v1"


class OdssError(RuntimeError):
    """Raised when the odss client cannot be constructed or used."""


def odss_api_key_configured() -> bool:
    """True when ``ODSS_API_KEY`` is non-empty."""
    return bool(os.environ.get("ODSS_API_KEY", "").strip())


def _read_api_key(explicit: str | None = None) -> str:
    if explicit is not None:
        return explicit.strip()
    return os.environ.get("ODSS_API_KEY", "").strip()


class OdssClient:
    """Thin client skeleton — HTTP left as TODO (fail-closed without key).

    When implementing:
    1. ``GET /bookmakers`` — confirm snai/sisal/bet365 metadata
    2. ``GET /odds?sport=calcio&market=1x2&bookmakers=…``
    3. Map events → Goal fixtures via ``goal_xg.odds.match``
    4. Wire into ``resolve_provider()`` only after Display license + key in store
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
    ) -> None:
        key = _read_api_key(api_key)
        if not key:
            raise OdssError(
                "ODSS_API_KEY missing — set env to enable odss-api.com client"
            )
        self.api_key = key
        self.base_url = (
            base_url
            or os.environ.get("ODSS_API_BASE_URL", "").strip()
            or DEFAULT_BASE_URL
        ).rstrip("/")
        self._http = None  # TODO: httpx.Client — do not open until fetch_* implemented

    def close(self) -> None:
        http = self._http
        if http is not None:
            http.close()
            self._http = None

    def __enter__(self) -> OdssClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- TODO: live endpoints (not called from web/compare yet) ---

    def fetch_bookmakers(self) -> list[dict[str, Any]]:
        """TODO: GET /bookmakers — requires key; not implemented."""
        raise NotImplementedError(
            "OdssClient.fetch_bookmakers not implemented — scaffold only"
        )

    def fetch_odds_1x2(
        self,
        *,
        bookmakers: Sequence[str],
        league: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """TODO: GET /odds?sport=calcio&market=1x2&bookmakers=… — not implemented."""
        raise NotImplementedError(
            "OdssClient.fetch_odds_1x2 not implemented — scaffold only"
        )


def maybe_client() -> OdssClient | None:
    """Return a client when ``ODSS_API_KEY`` set; else None (no raise).

    Construction alone does not hit the network. Do not call fetch_* from
    production paths until HTTP is implemented and licensed for display.
    """
    if not odss_api_key_configured():
        return None
    try:
        return OdssClient()
    except OdssError:
        return None
