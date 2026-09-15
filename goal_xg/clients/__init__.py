"""HTTP / data clients — GOAL (live/fixtures) + football-data.org (coach identity)."""

from goal_xg.clients.football_data import (
    BIG5_COMPETITION_CODES,
    CoachIdentity,
    FootballDataClient,
)
from goal_xg.clients.goal_api import BIG5_LEAGUE_CODES, GoalApiClient
from goal_xg.clients.goal_ws import GoalWsClient

__all__ = [
    "GoalApiClient",
    "GoalWsClient",
    "FootballDataClient",
    "CoachIdentity",
    "BIG5_LEAGUE_CODES",
    "BIG5_COMPETITION_CODES",
]
