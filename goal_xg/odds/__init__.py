"""Odds comparison — book registry, providers, compare service (scaffold).

v1 books: Bet365 · Snai · Sisal via pluggable registry.
Primary provider later: odss-api.com (``ODSS_API_KEY``). Without a key the
app uses the mock provider — no live HTTP to odds aggregators.
"""

from goal_xg.odds.cache import InMemoryOddsCache, OddsCache, OddsDiskCache
from goal_xg.odds.compare import OddsCompareService, compare_fixture_odds
from goal_xg.odds.models import (
    BestPrice,
    BookOdds1X2,
    FixtureOddsSnapshot,
    Market,
    Outcome,
)
from goal_xg.odds.provider import OddsProvider, resolve_provider
from goal_xg.odds.registry import BookEntry, default_registry, enabled_books

__all__ = [
    "BestPrice",
    "BookEntry",
    "BookOdds1X2",
    "FixtureOddsSnapshot",
    "InMemoryOddsCache",
    "Market",
    "OddsCache",
    "OddsCompareService",
    "OddsDiskCache",
    "OddsProvider",
    "Outcome",
    "compare_fixture_odds",
    "default_registry",
    "enabled_books",
    "resolve_provider",
]
