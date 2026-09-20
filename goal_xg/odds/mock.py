"""Deterministic mock odds provider — no network, fixed sample prices."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Sequence

from goal_xg.odds.models import (
    BookOdds1X2,
    FixtureOddsSnapshot,
    Market,
    compute_best_1x2,
)
from goal_xg.odds.registry import BookEntry, enabled_books


# Stable demo fixtures for UI / API without GOAL live.
SAMPLE_FIXTURES: dict[str, dict[str, str]] = {
    "demo-sa-1": {
        "home_name": "Inter",
        "away_name": "Milan",
        "league_code": "SA",
    },
    "demo-pl-1": {
        "home_name": "Arsenal",
        "away_name": "Chelsea",
        "league_code": "PL",
    },
    "demo-pd-1": {
        "home_name": "Barcelona",
        "away_name": "Real Madrid",
        "league_code": "PD",
    },
}


def _seed_prices(fixture_id: str, book_id: str) -> tuple[float, float, float]:
    """Deterministic 1X2 decimals from fixture+book (stable across runs)."""
    digest = hashlib.sha256(f"{fixture_id}:{book_id}".encode()).hexdigest()
    # Map hex slices into plausible football odds bands.
    h = 1.55 + (int(digest[0:4], 16) % 180) / 100.0  # 1.55–3.34
    d = 2.80 + (int(digest[4:8], 16) % 160) / 100.0  # 2.80–4.39
    a = 1.70 + (int(digest[8:12], 16) % 220) / 100.0  # 1.70–3.89
    return (round(h, 2), round(d, 2), round(a, 2))


class MockOddsProvider:
    """In-process sample data for scaffolding UI and tests."""

    name = "mock"

    def fetch_1x2(
        self,
        fixture_id: str,
        books: Sequence[BookEntry],
    ) -> FixtureOddsSnapshot:
        now = datetime.now(timezone.utc)
        active = enabled_books(books)
        meta = SAMPLE_FIXTURES.get(fixture_id, {})
        rows: list[BookOdds1X2] = []
        for book in active:
            home, draw, away = _seed_prices(fixture_id, book.id)
            rows.append(
                BookOdds1X2(
                    book_id=book.id,
                    home=home,
                    draw=draw,
                    away=away,
                    last_updated=now,
                    available=True,
                )
            )
        return FixtureOddsSnapshot(
            fixture_id=str(fixture_id),
            market=Market.ONE_X_TWO,
            books=rows,
            best=compute_best_1x2(rows),
            last_updated=now,
            source="mock",
            api_connected=False,
            home_name=meta.get("home_name"),
            away_name=meta.get("away_name"),
            league_code=meta.get("league_code"),
        )


def list_sample_fixture_ids() -> list[str]:
    """Fixture ids that have friendly demo labels on the Quote page."""
    return list(SAMPLE_FIXTURES.keys())
