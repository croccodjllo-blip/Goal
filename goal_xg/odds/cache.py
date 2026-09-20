"""Odds cache interface — in-memory + optional disk stub under ``.cache/odds/``."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from goal_xg.odds.models import FixtureOddsSnapshot, snapshot_from_mapping


@runtime_checkable
class OddsCache(Protocol):
    """Cache layer for fixture odds snapshots."""

    def get(self, fixture_id: str, market: str = "1X2") -> FixtureOddsSnapshot | None:
        ...

    def set(
        self,
        snapshot: FixtureOddsSnapshot,
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        ...

    def clear(self) -> None:
        ...


class InMemoryOddsCache:
    """Process-local TTL cache (default for web/tests)."""

    def __init__(self, default_ttl_seconds: float = 60.0) -> None:
        self.default_ttl = default_ttl_seconds
        self._store: dict[str, tuple[float, dict[str, Any]]] = {}

    def _key(self, fixture_id: str, market: str) -> str:
        return f"{fixture_id}|{market}"

    def get(self, fixture_id: str, market: str = "1X2") -> FixtureOddsSnapshot | None:
        key = self._key(fixture_id, market)
        row = self._store.get(key)
        if row is None:
            return None
        expires_at, payload = row
        if expires_at < time.time():
            self._store.pop(key, None)
            return None
        snap = snapshot_from_mapping(payload)
        snap.source = "cache"
        return snap

    def set(
        self,
        snapshot: FixtureOddsSnapshot,
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        ttl = self.default_ttl if ttl_seconds is None else ttl_seconds
        key = self._key(snapshot.fixture_id, snapshot.market.value)
        self._store[key] = (time.time() + max(0.0, ttl), snapshot.to_dict())

    def clear(self) -> None:
        self._store.clear()


def default_odds_cache_dir() -> Path:
    """Resolve odds cache root (env ``ODDS_CACHE_DIR`` or ``.cache/odds``)."""
    base = os.environ.get("ODDS_CACHE_DIR", "").strip()
    if base:
        return Path(base)
    return Path.cwd() / ".cache" / "odds"


class OddsDiskCache:
    """JSON files under ``.cache/odds/`` — stub for future worker writes."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        default_ttl_seconds: float = 120.0,
    ) -> None:
        self.root = Path(root) if root is not None else default_odds_cache_dir()
        self.default_ttl = default_ttl_seconds

    def ensure(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def _path(self, fixture_id: str, market: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in fixture_id)
        return self.root / f"{safe}_{market}.json"

    def get(self, fixture_id: str, market: str = "1X2") -> FixtureOddsSnapshot | None:
        path = self._path(fixture_id, market)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        expires_at = raw.get("expires_at")
        if isinstance(expires_at, (int, float)) and expires_at < time.time():
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        payload = raw.get("snapshot")
        if not isinstance(payload, dict):
            return None
        snap = snapshot_from_mapping(payload)
        snap.source = "cache"
        return snap

    def set(
        self,
        snapshot: FixtureOddsSnapshot,
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        self.ensure()
        ttl = self.default_ttl if ttl_seconds is None else ttl_seconds
        path = self._path(snapshot.fixture_id, snapshot.market.value)
        envelope = {
            "expires_at": time.time() + max(0.0, ttl),
            "snapshot": snapshot.to_dict(),
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(envelope, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)

    def clear(self) -> None:
        if not self.root.is_dir():
            return
        for path in self.root.glob("*.json"):
            try:
                path.unlink()
            except OSError:
                pass
