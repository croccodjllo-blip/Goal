"""Pluggable bookmaker registry (Bet365 / Snai / Sisal v1).

Adding book N = one ``BookEntry`` + provider_key mapping. UI and compare
iterate ``enabled`` books only — never hardcode book lists in templates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True, slots=True)
class BookEntry:
    """One bookmaker in the Goal odds registry."""

    id: str
    """Stable Goal id: ``bet365`` | ``snai`` | ``sisal`` | …"""

    name: str
    """Display name."""

    enabled: bool
    """When False, omit from UI / compare."""

    provider: str
    """Aggregator id: ``odss`` | ``odds_api_io`` | ``mock``."""

    provider_key: str
    """Key the provider expects (e.g. odss ``snai``, Odds-API.io ``Snai IT``)."""

    priority: int = 100
    """Sort / fallback order (lower = first)."""

    region_note: str = ""
    """e.g. ``ADM IT``."""


def default_registry() -> tuple[BookEntry, ...]:
    """v1 registry — three Italian-facing books enabled on odss keys."""
    return (
        BookEntry(
            id="bet365",
            name="Bet365",
            enabled=True,
            provider="odss",
            provider_key="bet365",
            priority=10,
            region_note="partner feed",
        ),
        BookEntry(
            id="snai",
            name="Snai",
            enabled=True,
            provider="odss",
            provider_key="snai",
            priority=20,
            region_note="ADM IT",
        ),
        BookEntry(
            id="sisal",
            name="Sisal",
            enabled=True,
            provider="odss",
            provider_key="sisal",
            priority=30,
            region_note="ADM IT",
        ),
    )


def enabled_books(
    registry: Sequence[BookEntry] | None = None,
) -> list[BookEntry]:
    """Return enabled books sorted by priority then id."""
    books = list(registry) if registry is not None else list(default_registry())
    return sorted(
        (b for b in books if b.enabled),
        key=lambda b: (b.priority, b.id),
    )


def book_by_id(
    book_id: str,
    registry: Sequence[BookEntry] | None = None,
) -> BookEntry | None:
    """Lookup by stable Goal id."""
    books = registry if registry is not None else default_registry()
    needle = book_id.strip().lower()
    for book in books:
        if book.id == needle:
            return book
    return None


def registry_as_dicts(
    registry: Sequence[BookEntry] | None = None,
    *,
    enabled_only: bool = False,
) -> list[dict[str, object]]:
    """JSON-serializable registry rows for ``GET /api/odds/books``."""
    books = enabled_books(registry) if enabled_only else list(
        registry if registry is not None else default_registry()
    )
    if not enabled_only:
        books = sorted(books, key=lambda b: (b.priority, b.id))
    return [
        {
            "id": b.id,
            "name": b.name,
            "enabled": b.enabled,
            "provider": b.provider,
            "provider_key": b.provider_key,
            "priority": b.priority,
            "region_note": b.region_note,
        }
        for b in books
    ]
