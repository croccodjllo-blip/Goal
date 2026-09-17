"""Background / scheduled jobs (daily fixtures + team stats)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from goal_xg.jobs.daily_refresh import DailyRefreshResult
    from goal_xg.jobs.store import DailyStore

__all__ = [
    "DailyRefreshResult",
    "DailyStore",
    "default_daily_cache_dir",
    "run_daily_refresh",
]


def __getattr__(name: str) -> Any:
    if name in ("DailyRefreshResult", "run_daily_refresh"):
        from goal_xg.jobs import daily_refresh as _dr

        return getattr(_dr, name)
    if name in ("DailyStore", "default_daily_cache_dir"):
        from goal_xg.jobs import store as _st

        return getattr(_st, name)
    raise AttributeError(f"module {__name!r} has no attribute {name!r}")
