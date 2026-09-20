"""Domain models for fixture odds snapshots (1X2 first; markets extensible)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


class Market(str, Enum):
    """Normalized Goal market codes."""

    ONE_X_TWO = "1X2"
    OU = "OU"
    BTTS = "BTTS"


class Outcome(str, Enum):
    """Normalized outcome codes within a market."""

    HOME = "HOME"
    DRAW = "DRAW"
    AWAY = "AWAY"
    OVER = "OVER"
    UNDER = "UNDER"
    YES = "YES"
    NO = "NO"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class BookOdds1X2:
    """Per-book 1X2 prices for one fixture."""

    book_id: str
    home: float | None
    draw: float | None
    away: float | None
    last_updated: datetime | None = None
    """Provider-reported freshness; None if unknown / missing."""

    available: bool = True
    """False when the book has no quote for this fixture."""

    def price_for(self, outcome: Outcome) -> float | None:
        if outcome is Outcome.HOME:
            return self.home
        if outcome is Outcome.DRAW:
            return self.draw
        if outcome is Outcome.AWAY:
            return self.away
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "book_id": self.book_id,
            "home": self.home,
            "draw": self.draw,
            "away": self.away,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
            "available": self.available,
        }


@dataclass(frozen=True, slots=True)
class BestPrice:
    """Best decimal price across books for one outcome."""

    outcome: Outcome
    price: float
    book_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "price": self.price,
            "book_id": self.book_id,
        }


@dataclass(slots=True)
class FixtureOddsSnapshot:
    """Same-match odds comparison for one Goal fixture_id."""

    fixture_id: str
    market: Market = Market.ONE_X_TWO
    books: list[BookOdds1X2] = field(default_factory=list)
    best: dict[str, BestPrice] = field(default_factory=dict)
    """Keys: outcome value (HOME/DRAW/AWAY)."""

    last_updated: datetime | None = None
    """Max book last_updated, or fetch time."""

    source: str = "mock"
    """``mock`` | ``odss`` | ``cache`` | …"""

    api_connected: bool = False
    """True only when a live aggregator key was used successfully."""

    home_name: str | None = None
    away_name: str | None = None
    league_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture_id": self.fixture_id,
            "market": self.market.value,
            "books": [b.to_dict() for b in self.books],
            "best": {k: v.to_dict() for k, v in self.best.items()},
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
            "source": self.source,
            "api_connected": self.api_connected,
            "home_name": self.home_name,
            "away_name": self.away_name,
            "league_code": self.league_code,
        }


def compute_best_1x2(books: list[BookOdds1X2]) -> dict[str, BestPrice]:
    """Pick highest decimal price per 1X2 outcome among available books."""
    best: dict[str, BestPrice] = {}
    for outcome in (Outcome.HOME, Outcome.DRAW, Outcome.AWAY):
        winner: BestPrice | None = None
        for book in books:
            if not book.available:
                continue
            price = book.price_for(outcome)
            if price is None:
                continue
            if winner is None or price > winner.price:
                winner = BestPrice(outcome=outcome, price=price, book_id=book.book_id)
        if winner is not None:
            best[outcome.value] = winner
    return best


def snapshot_from_mapping(data: Mapping[str, Any]) -> FixtureOddsSnapshot:
    """Rehydrate a snapshot from cache JSON (best-effort)."""
    books_raw = data.get("books") if isinstance(data.get("books"), list) else []
    books: list[BookOdds1X2] = []
    for row in books_raw:
        if not isinstance(row, dict):
            continue
        lu = row.get("last_updated")
        last_updated: datetime | None = None
        if isinstance(lu, str) and lu:
            try:
                last_updated = datetime.fromisoformat(lu.replace("Z", "+00:00"))
            except ValueError:
                last_updated = None
        books.append(
            BookOdds1X2(
                book_id=str(row.get("book_id") or ""),
                home=_as_float(row.get("home")),
                draw=_as_float(row.get("draw")),
                away=_as_float(row.get("away")),
                last_updated=last_updated,
                available=bool(row.get("available", True)),
            )
        )
    market_raw = str(data.get("market") or Market.ONE_X_TWO.value)
    try:
        market = Market(market_raw)
    except ValueError:
        market = Market.ONE_X_TWO
    snap_lu = data.get("last_updated")
    last_updated: datetime | None = None
    if isinstance(snap_lu, str) and snap_lu:
        try:
            last_updated = datetime.fromisoformat(snap_lu.replace("Z", "+00:00"))
        except ValueError:
            last_updated = None
    best_raw = data.get("best") if isinstance(data.get("best"), dict) else {}
    best: dict[str, BestPrice] = {}
    for key, val in best_raw.items():
        if not isinstance(val, dict):
            continue
        try:
            outcome = Outcome(str(val.get("outcome") or key))
        except ValueError:
            continue
        price = _as_float(val.get("price"))
        book_id = str(val.get("book_id") or "")
        if price is None or not book_id:
            continue
        best[outcome.value] = BestPrice(outcome=outcome, price=price, book_id=book_id)
    if not best:
        best = compute_best_1x2(books)
    return FixtureOddsSnapshot(
        fixture_id=str(data.get("fixture_id") or ""),
        market=market,
        books=books,
        best=best,
        last_updated=last_updated,
        source=str(data.get("source") or "cache"),
        api_connected=bool(data.get("api_connected", False)),
        home_name=_as_opt_str(data.get("home_name")),
        away_name=_as_opt_str(data.get("away_name")),
        league_code=_as_opt_str(data.get("league_code")),
    )


def _as_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _as_opt_str(val: Any) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    return s or None
