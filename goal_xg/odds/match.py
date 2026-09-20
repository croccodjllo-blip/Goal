"""Fixture ↔ odds-event matcher (stub for later odss wiring).

Goal spine stays GOAL API fixture ids. Aggregators use their own event IDs.
Recommended match_key (see docs/odds-platform.md):

    hash(league_code, kickoff_utc_bucket, normalize(home), normalize(away))
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class OddsEventMapRow:
    """Persisted Goal fixture → provider event mapping (future store)."""

    goal_fixture_id: str
    provider: str
    provider_event_id: str
    confidence: str  # high | low
    matched_at: str | None = None


def normalize_team_name(name: str | None) -> str:
    """Lowercase, strip accents / punctuation — align with API-Sports style."""
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\b(fc|cf|ac|as|ss|sc|calcio|club)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def build_match_key(
    *,
    league_code: str,
    kickoff_utc: datetime | str | None,
    home_name: str,
    away_name: str,
) -> str:
    """Stable key for fuzzy fixture↔event join (tolerance applied at match time)."""
    kick = ""
    if isinstance(kickoff_utc, datetime):
        kick = kickoff_utc.replace(minute=0, second=0, microsecond=0).isoformat()
    elif kickoff_utc:
        kick = str(kickoff_utc).strip()[:16]
    raw = "|".join(
        [
            str(league_code or "").upper(),
            kick,
            normalize_team_name(home_name),
            normalize_team_name(away_name),
        ]
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def resolve_provider_event(
    goal_fixture_id: str,
    *,
    map_store: dict[str, Any] | None = None,
) -> OddsEventMapRow | None:
    """Lookup mapped provider event — stub returns None until ingest job exists."""
    store = map_store or {}
    row = store.get(str(goal_fixture_id))
    if not isinstance(row, dict):
        return None
    return OddsEventMapRow(
        goal_fixture_id=str(goal_fixture_id),
        provider=str(row.get("provider") or ""),
        provider_event_id=str(row.get("provider_event_id") or ""),
        confidence=str(row.get("confidence") or "low"),
        matched_at=str(row["matched_at"]) if row.get("matched_at") else None,
    )
