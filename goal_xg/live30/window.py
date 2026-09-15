"""Live @ ≈30′ 1H window helpers (locked product trigger)."""

from __future__ import annotations

from typing import Any

# Operative clock window for the primary live emit (minutes elapsed in 1H).
LIVE_WINDOW_MIN = 28
LIVE_WINDOW_MAX = 32
LIVE_TARGET_MINUTE = 30

_FIRST_HALF_PERIODS = frozenset(
    {
        "1H",
        "1",
        "FIRST_HALF",
        "FIRST",
        "1ST",
        "1ST_HALF",
        "FIRSTHALF",
    }
)


def normalize_period(period: str | None) -> str:
    if period is None:
        return "OTHER"
    raw = str(period).strip().upper().replace(" ", "_").replace("-", "_")
    if raw in _FIRST_HALF_PERIODS:
        return "1H"
    if raw in {"HT", "HALF_TIME", "HALFTIME"}:
        return "HT"
    if raw in {"2H", "2", "SECOND_HALF", "SECOND", "2ND", "2ND_HALF"}:
        return "2H"
    if raw in {"NS", "NOT_STARTED", "NSY"}:
        return "NS"
    if raw in {"FT", "FINISHED", "AET", "PEN"}:
        return "FT"
    return raw or "OTHER"


def parse_minute(value: Any) -> int | None:
    """Parse clock minute from int / ``\"30\"`` / ``\"45+2\"`` / ``\"30'\"``."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    # Strip trailing apostrophe / min markers.
    text = text.replace("'", "").replace("′", "").upper()
    if text in {"HT", "HALF TIME", "HALF_TIME", "FT", "FINISHED"}:
        return None
    if "+" in text:
        left, _, right = text.partition("+")
        try:
            base = int("".join(ch for ch in left if ch.isdigit()) or "0")
        except ValueError:
            return None
        extra_digits = "".join(ch for ch in right if ch.isdigit())
        extra = int(extra_digits) if extra_digits else 0
        return base + extra
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def in_live30_window(minute: int | None, *, period: str | None = "1H") -> bool:
    """True if clock is in the 28–32′ 1H operative window."""
    if minute is None:
        return False
    if period is not None and normalize_period(period) != "1H":
        return False
    return LIVE_WINDOW_MIN <= int(minute) <= LIVE_WINDOW_MAX


def is_score_00(home: int | None, away: int | None) -> bool:
    return home is not None and away is not None and int(home) == 0 and int(away) == 0
