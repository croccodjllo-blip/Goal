"""Odds registry, compare logic, and API shape — mock provider only."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from goal_xg.clients.odss import OdssClient, OdssError, maybe_client, odss_api_key_configured
from goal_xg.odds.cache import InMemoryOddsCache, OddsDiskCache
from goal_xg.odds.compare import OddsCompareService, compare_fixture_odds
from goal_xg.odds.mock import MockOddsProvider, SAMPLE_FIXTURES
from goal_xg.odds.models import BookOdds1X2, Market, Outcome, compute_best_1x2
from goal_xg.odds.provider import provider_status, resolve_provider
from goal_xg.odds.registry import (
    BookEntry,
    book_by_id,
    default_registry,
    enabled_books,
    registry_as_dicts,
)
from goal_xg.web.app import create_app


def test_default_registry_three_enabled_books() -> None:
    books = default_registry()
    assert len(books) == 3
    ids = [b.id for b in enabled_books(books)]
    assert ids == ["bet365", "snai", "sisal"]
    for b in books:
        assert b.enabled is True
        assert b.provider == "odss"
        assert b.provider_key in {"bet365", "snai", "sisal"}


def test_registry_pluggable_extra_book() -> None:
    base = list(default_registry())
    base.append(
        BookEntry(
            id="eurobet",
            name="Eurobet",
            enabled=True,
            provider="odss",
            provider_key="eurobet",
            priority=40,
            region_note="ADM IT",
        )
    )
    ids = [b.id for b in enabled_books(base)]
    assert "eurobet" in ids
    assert book_by_id("snai", base) is not None
    assert book_by_id("missing", base) is None


def test_registry_as_dicts_shape() -> None:
    rows = registry_as_dicts(enabled_only=True)
    assert len(rows) == 3
    assert set(rows[0].keys()) >= {
        "id",
        "name",
        "enabled",
        "provider",
        "provider_key",
        "priority",
        "region_note",
    }


def test_compute_best_1x2() -> None:
    books = [
        BookOdds1X2("bet365", home=2.10, draw=3.20, away=3.40, available=True),
        BookOdds1X2("snai", home=2.25, draw=3.10, away=3.50, available=True),
        BookOdds1X2("sisal", home=2.05, draw=3.40, away=3.30, available=True),
    ]
    best = compute_best_1x2(books)
    assert best[Outcome.HOME.value].book_id == "snai"
    assert best[Outcome.HOME.value].price == 2.25
    assert best[Outcome.DRAW.value].book_id == "sisal"
    assert best[Outcome.AWAY.value].book_id == "snai"


def test_mock_provider_deterministic() -> None:
    provider = MockOddsProvider()
    books = default_registry()
    a = provider.fetch_1x2("demo-sa-1", books)
    b = provider.fetch_1x2("demo-sa-1", books)
    assert a.api_connected is False
    assert a.source == "mock"
    assert a.market is Market.ONE_X_TWO
    assert len(a.books) == 3
    assert a.home_name == "Inter"
    prices_a = [(x.book_id, x.home, x.draw, x.away) for x in a.books]
    prices_b = [(x.book_id, x.home, x.draw, x.away) for x in b.books]
    assert prices_a == prices_b
    assert set(a.best.keys()) == {"HOME", "DRAW", "AWAY"}


def test_compare_service_uses_cache() -> None:
    cache = InMemoryOddsCache(default_ttl_seconds=60)
    svc = OddsCompareService(provider=MockOddsProvider(), cache=cache)
    first = svc.compare("demo-pl-1")
    assert first.source == "mock"
    second = svc.compare("demo-pl-1")
    assert second.source == "cache"
    assert second.fixture_id == "demo-pl-1"


def test_compare_fixture_odds_helper() -> None:
    snap = compare_fixture_odds("any-fixture-99", provider=MockOddsProvider())
    assert snap.fixture_id == "any-fixture-99"
    assert len(snap.books) == 3


def test_disk_cache_roundtrip(tmp_path) -> None:
    cache = OddsDiskCache(root=tmp_path, default_ttl_seconds=120)
    snap = MockOddsProvider().fetch_1x2("demo-pd-1", default_registry())
    cache.set(snap)
    hit = cache.get("demo-pd-1", "1X2")
    assert hit is not None
    assert hit.fixture_id == "demo-pd-1"
    assert hit.source == "cache"
    assert len(hit.books) == 3


def test_resolve_provider_always_mock_scaffold(monkeypatch) -> None:
    monkeypatch.delenv("ODSS_API_KEY", raising=False)
    p = resolve_provider()
    assert p.name == "mock"
    monkeypatch.setenv("ODSS_API_KEY", "odss_live_fake_not_used")
    p2 = resolve_provider(prefer_live=True)
    assert p2.name == "mock"
    status = provider_status()
    assert status["live_calls_enabled"] is False
    assert status["active_provider"] == "mock"


def test_odss_client_fail_closed_without_key(monkeypatch) -> None:
    monkeypatch.delenv("ODSS_API_KEY", raising=False)
    assert odss_api_key_configured() is False
    assert maybe_client() is None
    with pytest.raises(OdssError):
        OdssClient()


def test_odss_client_constructs_but_fetch_not_implemented(monkeypatch) -> None:
    monkeypatch.setenv("ODSS_API_KEY", "odss_live_test_scaffold_only")
    client = OdssClient()
    assert client.api_key.startswith("odss_live_")
    with pytest.raises(NotImplementedError):
        client.fetch_bookmakers()
    with pytest.raises(NotImplementedError):
        client.fetch_odds_1x2(bookmakers=["snai"])
    client.close()


def test_api_odds_books_shape(monkeypatch) -> None:
    monkeypatch.delenv("ODSS_API_KEY", raising=False)
    monkeypatch.delenv("GOAL_API_KEY", raising=False)
    client = TestClient(create_app())
    resp = client.get("/api/odds/books")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["books"]) == 3
    assert body["books"][0]["id"] == "bet365"
    assert body["provider"]["live_calls_enabled"] is False
    assert body["provider"]["active_provider"] == "mock"


def test_api_odds_fixture_mock_shape(monkeypatch) -> None:
    monkeypatch.delenv("ODSS_API_KEY", raising=False)
    client = TestClient(create_app())
    resp = client.get("/api/odds/demo-sa-1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["fixture_id"] == "demo-sa-1"
    assert body["market"] == "1X2"
    assert body["api_connected"] is False
    assert body["source"] in {"mock", "cache"}
    assert len(body["books"]) == 3
    assert set(body["best"].keys()) == {"HOME", "DRAW", "AWAY"}
    for book in body["books"]:
        assert book["book_id"] in {"bet365", "snai", "sisal"}
        assert book["home"] is not None
        assert book["available"] is True
    assert body["provider"]["live_calls_enabled"] is False


def test_odds_page_html(monkeypatch) -> None:
    monkeypatch.delenv("ODSS_API_KEY", raising=False)
    monkeypatch.delenv("GOAL_API_KEY", raising=False)
    client = TestClient(create_app())
    resp = client.get("/odds")
    assert resp.status_code == 200
    assert "API non collegata" in resp.text
    assert "Bet365" in resp.text
    assert "Snai" in resp.text
    assert "Sisal" in resp.text
    assert "Quote 1X2" in resp.text
    assert 'href="/odds"' in resp.text
    for fid in SAMPLE_FIXTURES:
        assert fid in resp.text or SAMPLE_FIXTURES[fid]["home_name"] in resp.text


def test_health_reports_odss_flag(monkeypatch) -> None:
    monkeypatch.delenv("ODSS_API_KEY", raising=False)
    monkeypatch.delenv("GOAL_API_KEY", raising=False)
    client = TestClient(create_app())
    body = client.get("/health").json()
    assert body["odss_api_key_configured"] is False
    assert body["odds_live_calls_enabled"] is False
    assert body["version"] == "0.5.2"


def test_index_still_has_nav(monkeypatch) -> None:
    monkeypatch.delenv("GOAL_API_KEY", raising=False)
    client = TestClient(create_app())
    resp = client.get("/")
    assert resp.status_code == 200
    assert 'href="/odds"' in resp.text
    assert "Board" in resp.text
    assert "Quote" in resp.text
