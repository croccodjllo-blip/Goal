"""Coach identity soft feature (football-data.org).

This is **not** coach-vs-coach H2H. True H2H stays omitted + renormed
(``coach_h2h`` in ``MVP_OMIT_TERMS``) because football-data.org has no
coach-vs-coach endpoint.

Soft use only: resolve current coach name/id/team for display or future
light priors — never invent H2H rates from identity alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from goal_xg.clients.football_data import CoachIdentity, FootballDataClient


@dataclass(frozen=True)
class MatchCoachIdentities:
    """Home/away coach identity pair (soft); H2H signal remains unavailable."""

    home: CoachIdentity | None
    away: CoachIdentity | None
    h2h_available: bool = False  # always False on football-data.org free API

    def as_dict(self) -> dict[str, Any]:
        def _row(c: CoachIdentity | None) -> dict[str, Any] | None:
            if c is None:
                return None
            return {
                "person_id": c.person_id,
                "name": c.name,
                "nationality": c.nationality,
                "team_id": c.team_id,
                "team_name": c.team_name,
                "competition_code": c.competition_code,
                "section": c.section,
                "source": c.source,
            }

        return {
            "home": _row(self.home),
            "away": _row(self.away),
            "h2h_available": self.h2h_available,
            "feature": "coach_identity",
            "note": "soft identity only; coach_h2h stays omit+renorm",
        }


def resolve_match_coaches(
    client: FootballDataClient,
    *,
    home_team_id: int | str,
    away_team_id: int | str,
) -> MatchCoachIdentities:
    """Fetch home/away coach identity; fail-closed (None) if coach null."""
    return MatchCoachIdentities(
        home=client.coach_for_team(home_team_id),
        away=client.coach_for_team(away_team_id),
        h2h_available=False,
    )


def identity_signal_pair(
    identities: MatchCoachIdentities,
) -> Mapping[str, float]:
    """Optional soft presence flags — never a substitute for coach_h2h rates.

    Returns empty mapping (no scoring weight) so callers do not inject fake H2H.
    Identity is metadata only until a real H2H source exists.
    """
    _ = identities  # documented no-op for scoring
    return {}
