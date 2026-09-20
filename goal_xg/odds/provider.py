"""Odds provider protocol + resolver (mock by default; odss when keyed)."""

from __future__ import annotations

import os
from typing import Protocol, Sequence, runtime_checkable

from goal_xg.odds.models import FixtureOddsSnapshot
from goal_xg.odds.registry import BookEntry, default_registry


@runtime_checkable
class OddsProvider(Protocol):
    """Fetch 1X2 (and later markets) for a Goal fixture_id."""

    name: str

    def fetch_1x2(
        self,
        fixture_id: str,
        books: Sequence[BookEntry],
    ) -> FixtureOddsSnapshot:
        """Return a snapshot; never raise for missing quotes — mark unavailable."""


def odss_api_key_configured() -> bool:
    """True when ``ODSS_API_KEY`` is non-empty."""
    return bool(os.environ.get("ODSS_API_KEY", "").strip())


def resolve_provider(*, prefer_live: bool = True) -> OddsProvider:
    """Pick provider for the compare path.

    - With ``ODSS_API_KEY`` and ``prefer_live``: still returns **mock** until the
      real OdssClient HTTP path is wired (scaffold). The live client exists as
      a TODO stub and is **not** called here — avoids accidental live traffic.
    - Without key: ``MockOddsProvider`` (deterministic fixtures).
    """
    from goal_xg.odds.mock import MockOddsProvider

    # Scaffold lock: never call live odss in the prod/web path yet.
    # When HTTP is implemented, gate on odss_api_key_configured() and return
    # OdssClient-backed provider. Until then always mock.
    _ = prefer_live
    _ = odss_api_key_configured
    return MockOddsProvider()


def provider_status() -> dict[str, object]:
    """Health/debug shape for API + UI banner."""
    key_ok = odss_api_key_configured()
    return {
        "odss_api_key_configured": key_ok,
        "active_provider": "mock",
        "live_calls_enabled": False,
        "message": (
            "API non collegata — dati mock"
            if not key_ok
            else "ODSS_API_KEY presente ma client live non ancora collegato (mock)"
        ),
        "enabled_books": [b.id for b in default_registry() if b.enabled],
    }
