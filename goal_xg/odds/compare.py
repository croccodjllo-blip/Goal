"""Compare service — fixture_id → same-match 1X2 across enabled books."""

from __future__ import annotations

from typing import Sequence

from goal_xg.odds.cache import InMemoryOddsCache, OddsCache
from goal_xg.odds.models import FixtureOddsSnapshot, Market
from goal_xg.odds.provider import OddsProvider, resolve_provider
from goal_xg.odds.registry import BookEntry, default_registry, enabled_books


class OddsCompareService:
    """Fetch (or cache-hit) odds for a fixture and compute best prices."""

    def __init__(
        self,
        provider: OddsProvider | None = None,
        cache: OddsCache | None = None,
        registry: Sequence[BookEntry] | None = None,
        *,
        cache_ttl_seconds: float = 60.0,
    ) -> None:
        self.provider = provider if provider is not None else resolve_provider()
        self.cache = cache if cache is not None else InMemoryOddsCache(cache_ttl_seconds)
        self.registry = list(registry) if registry is not None else list(default_registry())
        self.cache_ttl_seconds = cache_ttl_seconds

    def compare(
        self,
        fixture_id: str,
        *,
        market: Market = Market.ONE_X_TWO,
        use_cache: bool = True,
    ) -> FixtureOddsSnapshot:
        fid = str(fixture_id).strip()
        if not fid:
            raise ValueError("fixture_id required")
        if market is not Market.ONE_X_TWO:
            # Scaffold: only 1X2 implemented; keep enum ready for OU/BTTS.
            raise ValueError(f"market {market.value} not implemented yet")
        if use_cache:
            hit = self.cache.get(fid, market.value)
            if hit is not None:
                return hit
        books = enabled_books(self.registry)
        snap = self.provider.fetch_1x2(fid, books)
        if use_cache:
            self.cache.set(snap, ttl_seconds=self.cache_ttl_seconds)
        return snap


def compare_fixture_odds(
    fixture_id: str,
    *,
    provider: OddsProvider | None = None,
    cache: OddsCache | None = None,
) -> FixtureOddsSnapshot:
    """Module-level helper used by FastAPI routes."""
    return OddsCompareService(provider=provider, cache=cache).compare(fixture_id)
