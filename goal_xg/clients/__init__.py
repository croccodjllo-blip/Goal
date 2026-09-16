"""HTTP / data clients — GOAL + API-Sports enrich + football-data.org coaches."""

from goal_xg.clients.api_sports import (
    BIG5_LEAGUE_IDS,
    ApiSportsClient,
    api_sports_key_configured,
    maybe_client as maybe_api_sports_client,
)
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
    "ApiSportsClient",
    "api_sports_key_configured",
    "maybe_api_sports_client",
    "BIG5_LEAGUE_CODES",
    "BIG5_COMPETITION_CODES",
    "BIG5_LEAGUE_IDS",
]
