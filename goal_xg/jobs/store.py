"""Disk persistence for daily fixtures + season-to-date team stats.

Layout under ``GOAL_API_CACHE_DIR`` / ``daily/`` (default ``.cache/goal_api/daily``):

- ``meta.json`` — last successful run summary
- ``fixtures_{YYYY-MM-DD}.json`` — programme cards for that Rome calendar day
- ``history_{league_code}.json`` — finished (FT) fixtures season-to-date
- ``standings_{league_code}.json`` — raw standings payload
- ``team_stats_{YYYY-MM-DD}.json`` — computed per-team side stats + ranks
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def default_daily_cache_dir() -> Path:
    """Resolve daily cache root (env ``GOAL_API_CACHE_DIR`` / ``daily``)."""
    base = os.environ.get("GOAL_API_CACHE_DIR", "").strip()
    root = Path(base) if base else Path.cwd() / ".cache" / "goal_api"
    return root / "daily"


class DailyStore:
    """Read/write helpers for the daily refresh artifacts."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else default_daily_cache_dir()

    def ensure(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def _write_json(self, path: Path, payload: Any) -> Path:
        self.ensure()
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, default=str, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)
        return path

    def _read_json(self, path: Path) -> Any | None:
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def meta_path(self) -> Path:
        return self.root / "meta.json"

    def fixtures_path(self, day: str) -> Path:
        return self.root / f"fixtures_{day}.json"

    def history_path(self, league_code: str) -> Path:
        return self.root / f"history_{league_code.upper()}.json"

    def standings_path(self, league_code: str) -> Path:
        return self.root / f"standings_{league_code.upper()}.json"

    def team_stats_path(self, day: str) -> Path:
        return self.root / f"team_stats_{day}.json"

    def save_meta(self, meta: dict[str, Any]) -> Path:
        return self._write_json(self.meta_path(), meta)

    def load_meta(self) -> dict[str, Any] | None:
        raw = self._read_json(self.meta_path())
        return raw if isinstance(raw, dict) else None

    def save_fixtures(self, day: str, payload: dict[str, Any]) -> Path:
        return self._write_json(self.fixtures_path(day), payload)

    def load_fixtures(self, day: str) -> dict[str, Any] | None:
        raw = self._read_json(self.fixtures_path(day))
        return raw if isinstance(raw, dict) else None

    def save_history(self, league_code: str, payload: dict[str, Any]) -> Path:
        return self._write_json(self.history_path(league_code), payload)

    def load_history(self, league_code: str) -> dict[str, Any] | None:
        raw = self._read_json(self.history_path(league_code))
        return raw if isinstance(raw, dict) else None

    def load_history_rows(self, league_code: str) -> list[dict[str, Any]]:
        blob = self.load_history(league_code)
        if not blob:
            return []
        rows = blob.get("fixtures")
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

    def save_standings(self, league_code: str, payload: dict[str, Any]) -> Path:
        return self._write_json(self.standings_path(league_code), payload)

    def load_standings(self, league_code: str) -> dict[str, Any] | None:
        raw = self._read_json(self.standings_path(league_code))
        return raw if isinstance(raw, dict) else None

    def load_standings_payload(self, league_code: str) -> Any | None:
        blob = self.load_standings(league_code)
        if not blob:
            return None
        return blob.get("payload")

    def save_team_stats(self, day: str, payload: dict[str, Any]) -> Path:
        return self._write_json(self.team_stats_path(day), payload)

    def load_team_stats(self, day: str) -> dict[str, Any] | None:
        raw = self._read_json(self.team_stats_path(day))
        return raw if isinstance(raw, dict) else None

    def find_league_code_for_id(self, league_id: int | str) -> str | None:
        """Best-effort: scan history/meta for a matching league_id → code."""
        lid = str(league_id).strip()
        if not lid:
            return None
        meta = self.load_meta()
        if meta:
            for row in meta.get("leagues") or []:
                if isinstance(row, dict) and str(row.get("league_id") or "") == lid:
                    code = row.get("code")
                    if code:
                        return str(code).upper()
        if not self.root.is_dir():
            return None
        for path in sorted(self.root.glob("history_*.json")):
            blob = self._read_json(path)
            if isinstance(blob, dict) and str(blob.get("league_id") or "") == lid:
                code = blob.get("league_code") or path.stem.replace("history_", "")
                return str(code).upper()
        return None
