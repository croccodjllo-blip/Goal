"""Prematch / live extra-signal builders for BASE_WEIGHTS terms.

Builds ``form``, ``goal_minutes_last5``, ``streaks``, ``matchup``,
``standings``, ``fatigue``, ``club_h2h``, ``goals_scored_last5_ha`` as
[0,1] signals from GOAL history / standings / H2H payloads. Missing
inputs → omit (caller renorms).

CSV football-data.co.uk is not wired yet (not used in this package) —
builders accept the same finished-fixture shapes GOAL returns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from goal_xg.features.prematch import FinishedFixture, _as_finished, shrink_rate

# Keys that belong in extra_signals for scoring.
EXTRA_SIGNAL_KEYS: frozenset[str] = frozenset(
    {
        "form",
        "goal_minutes_last5",
        "streaks",
        "matchup",
        "standings",
        "fatigue",
        "club_h2h",
        "goals_scored_last5_ha",
    }
)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        # Heuristic: ms vs s epoch.
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    # ISO-ish
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            dt = datetime.strptime(text[:19], fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


@dataclass(frozen=True)
class RichFinished:
    """Finished fixture with optional kickoff + goal minutes for extractors."""

    home_team_id: int | str
    away_team_id: int | str
    goals_home: int
    goals_away: int
    league_id: int | str | None = None
    kickoff: datetime | None = None
    goal_minutes: tuple[int, ...] = ()

    @property
    def total_goals(self) -> int:
        return self.goals_home + self.goals_away

    @property
    def over05(self) -> bool:
        return self.total_goals >= 1


def _as_rich(rows: Iterable[Any]) -> list[RichFinished]:
    out: list[RichFinished] = []
    for row in rows:
        if isinstance(row, RichFinished):
            out.append(row)
            continue
        if isinstance(row, FinishedFixture):
            out.append(
                RichFinished(
                    home_team_id=row.home_team_id,
                    away_team_id=row.away_team_id,
                    goals_home=row.goals_home,
                    goals_away=row.goals_away,
                    league_id=row.league_id,
                )
            )
            continue
        if not isinstance(row, dict):
            continue
        base = _as_finished([row])
        if not base:
            continue
        f = base[0]
        kick = _parse_dt(
            row.get("kickoff")
            or row.get("date")
            or row.get("starting_at")
            or row.get("startingAt")
            or row.get("match_date")
            or row.get("matchDate")
        )
        minutes: list[int] = []
        raw_mins = row.get("goal_minutes") or row.get("goalMinutes")
        if isinstance(raw_mins, (list, tuple)):
            for m in raw_mins:
                try:
                    minutes.append(int(m))
                except (TypeError, ValueError):
                    continue
        else:
            events = row.get("events") or row.get("goalscorer") or row.get("goals")
            if isinstance(events, list):
                for ev in events:
                    if not isinstance(ev, dict):
                        continue
                    typ = str(ev.get("type") or ev.get("event") or "").lower()
                    if typ and "goal" not in typ and typ not in ("g", "score"):
                        # Still accept time-only goalscorer rows without type.
                        if "time" not in ev and "minute" not in ev and "elapsed" not in ev:
                            continue
                    raw_m = ev.get("minute") or ev.get("time") or ev.get("elapsed")
                    if raw_m is None:
                        continue
                    try:
                        minutes.append(int(str(raw_m).split("+")[0]))
                    except (TypeError, ValueError):
                        continue
        out.append(
            RichFinished(
                home_team_id=f.home_team_id,
                away_team_id=f.away_team_id,
                goals_home=f.goals_home,
                goals_away=f.goals_away,
                league_id=f.league_id,
                kickoff=kick,
                goal_minutes=tuple(minutes),
            )
        )
    return out


def _team_matches(
    fixtures: Sequence[RichFinished], team_id: int | str, *, last_n: int
) -> list[RichFinished]:
    tid = team_id
    rows = [
        f
        for f in fixtures
        if f.home_team_id == tid or f.away_team_id == tid
    ]
    # Prefer chronological if kickoffs present.
    dated = [f for f in rows if f.kickoff is not None]
    if len(dated) == len(rows) and rows:
        rows = sorted(dated, key=lambda f: f.kickoff or datetime.min.replace(tzinfo=timezone.utc))
    return rows[-last_n:] if last_n > 0 else rows


def signal_form(
    finished: Sequence[Any],
    home_team_id: int | str,
    away_team_id: int | str,
    *,
    last_n: int = 5,
    league_baseline: float = 0.92,
) -> float | None:
    """Last-N Over 0.5 form (both teams), shrunk toward league baseline."""
    rows = _as_rich(finished)
    home_m = _team_matches(rows, home_team_id, last_n=last_n)
    away_m = _team_matches(rows, away_team_id, last_n=last_n)
    if not home_m and not away_m:
        return None

    def _rate(ms: list[RichFinished]) -> tuple[float, int]:
        if not ms:
            return league_baseline, 0
        over = sum(1 for m in ms if m.over05)
        return over / len(ms), len(ms)

    hr, hn = _rate(home_m)
    ar, an = _rate(away_m)
    hs = shrink_rate(hr, league_baseline, hn, k=4.0) if hn else league_baseline
    as_ = shrink_rate(ar, league_baseline, an, k=4.0) if an else league_baseline
    return _clamp01(0.5 * hs + 0.5 * as_)


def signal_goal_minutes_last5(
    finished: Sequence[Any],
    home_team_id: int | str,
    away_team_id: int | str,
    *,
    last_n: int = 5,
    after_minute: int = 30,
) -> float | None:
    """Share of recent goals scored after ``after_minute`` (post-30′ hazard).

    Omit if no goal-minute timestamps are available.
    """
    rows = _as_rich(finished)
    home_m = _team_matches(rows, home_team_id, last_n=last_n)
    away_m = _team_matches(rows, away_team_id, last_n=last_n)
    mins: list[int] = []
    for m in home_m + away_m:
        mins.extend(m.goal_minutes)
    if not mins:
        return None
    late = sum(1 for t in mins if t > after_minute)
    # Map late share to signal: more late goals → higher residual Over chance.
    share = late / len(mins)
    return _clamp01(0.40 + 0.45 * share)


def signal_streaks(
    finished: Sequence[Any],
    home_team_id: int | str,
    away_team_id: int | str,
    *,
    lookback: int = 10,
) -> float | None:
    """CS / scoring streaks → Over 0.5 signal (scoring streak ↑, CS streak ↓)."""
    rows = _as_rich(finished)
    home_m = _team_matches(rows, home_team_id, last_n=lookback)
    away_m = _team_matches(rows, away_team_id, last_n=lookback)
    if not home_m and not away_m:
        return None

    def _scoring_run(ms: list[RichFinished], tid: int | str) -> int:
        run = 0
        for m in reversed(ms):
            gf = m.goals_home if m.home_team_id == tid else m.goals_away
            if gf >= 1:
                run += 1
            else:
                break
        return run

    def _cs_run(ms: list[RichFinished], tid: int | str) -> int:
        run = 0
        for m in reversed(ms):
            ga = m.goals_away if m.home_team_id == tid else m.goals_home
            if ga == 0:
                run += 1
            else:
                break
        return run

    score_run = max(
        _scoring_run(home_m, home_team_id) if home_m else 0,
        _scoring_run(away_m, away_team_id) if away_m else 0,
    )
    cs_run = max(
        _cs_run(home_m, home_team_id) if home_m else 0,
        _cs_run(away_m, away_team_id) if away_m else 0,
    )
    # Neutral 0.50; scoring streak lifts; mutual CS pressure lowers.
    signal = 0.50 + 0.06 * min(5, score_run) - 0.07 * min(5, cs_run)
    return _clamp01(signal)


def signal_matchup(
    finished: Sequence[Any],
    home_team_id: int | str,
    away_team_id: int | str,
    *,
    last_n: int = 8,
    league_baseline: float = 0.92,
) -> float | None:
    """Home attack vs away blank profile (side-specific last-N)."""
    rows = _as_rich(finished)
    home_home = [
        f for f in rows if f.home_team_id == home_team_id
    ][-last_n:]
    away_away = [
        f for f in rows if f.away_team_id == away_team_id
    ][-last_n:]
    if not home_home and not away_away:
        return None

    def _side_over(ms: list[RichFinished]) -> tuple[float, int]:
        if not ms:
            return league_baseline, 0
        return sum(1 for m in ms if m.over05) / len(ms), len(ms)

    hr, hn = _side_over(home_home)
    ar, an = _side_over(away_away)
    hs = shrink_rate(hr, league_baseline, hn, k=5.0) if hn else league_baseline
    as_ = shrink_rate(ar, league_baseline, an, k=5.0) if an else league_baseline
    # Geometric-ish blend of side rates.
    geo = (hs * as_) ** 0.5 if hs > 0 and as_ > 0 else league_baseline
    return _clamp01(geo)


def _side_matches(
    fixtures: Sequence[RichFinished],
    team_id: int | str,
    *,
    side: str,
    last_n: int,
) -> list[RichFinished]:
    """Last-N finished matches for ``team_id`` on home or away side only."""
    tid = team_id
    if side == "home":
        rows = [f for f in fixtures if f.home_team_id == tid]
    elif side == "away":
        rows = [f for f in fixtures if f.away_team_id == tid]
    else:
        raise ValueError(f"side must be 'home' or 'away', got {side!r}")
    dated = [f for f in rows if f.kickoff is not None]
    if len(dated) == len(rows) and rows:
        rows = sorted(
            dated, key=lambda f: f.kickoff or datetime.min.replace(tzinfo=timezone.utc)
        )
    return rows[-last_n:] if last_n > 0 else rows


def raw_goals_scored_last5_ha(
    finished: Sequence[Any],
    home_team_id: int | str,
    away_team_id: int | str,
    *,
    last_n: int = 5,
    min_n: int = 1,
) -> dict[str, float | int] | None:
    """Side-specific last-N GF averages (UI / features). Omit if either side missing."""
    rows = _as_rich(finished)
    home_home = _side_matches(rows, home_team_id, side="home", last_n=last_n)
    away_away = _side_matches(rows, away_team_id, side="away", last_n=last_n)
    if len(home_home) < min_n or len(away_away) < min_n:
        return None
    home_avg = sum(m.goals_home for m in home_home) / len(home_home)
    away_avg = sum(m.goals_away for m in away_away) / len(away_away)
    combined = 0.5 * home_avg + 0.5 * away_avg
    return {
        "home_avg": float(home_avg),
        "away_avg": float(away_avg),
        "combined": float(combined),
        "home_n": len(home_home),
        "away_n": len(away_away),
    }


def signal_goals_scored_last5_ha(
    finished: Sequence[Any],
    home_team_id: int | str,
    away_team_id: int | str,
    *,
    last_n: int = 5,
    min_n: int = 1,
) -> float | None:
    """Match-level attack rate from side-specific last-N goals scored.

    - Home team: mean goals scored in their last ``last_n`` **home** matches
    - Away team: mean goals scored in their last ``last_n`` **away** matches
    - Combine: simple average of both side means (match-level attack rate)

    Fail-closed: omit if either side has fewer than ``min_n`` qualifying
    finished matches (no invented history).
    """
    raw = raw_goals_scored_last5_ha(
        finished,
        home_team_id,
        away_team_id,
        last_n=last_n,
        min_n=min_n,
    )
    if raw is None:
        return None
    combined = float(raw["combined"])

    # Map combined GF/match avg → [0,1]. Mid≈1.2 (typical Big-5 side GF),
    # high≈2.5+ (strong dual attack). Sterile 0 → mild down.
    mid, high = 1.2, 2.5
    if combined <= 0:
        return 0.28
    if combined >= high:
        return 0.90
    if combined <= mid:
        return _clamp01(0.28 + (0.55 - 0.28) * (combined / mid))
    return _clamp01(0.55 + (0.90 - 0.55) * min(1.0, (combined - mid) / (high - mid)))


def signal_club_h2h(
    h2h_rows: Sequence[Any],
    *,
    min_n: int = 3,
    league_baseline: float = 0.92,
) -> float | None:
    """Club H2H % Over 0.5; omit if N < min_n."""
    rows = _as_rich(h2h_rows)
    if len(rows) < min_n:
        return None
    over = sum(1 for r in rows if r.over05)
    raw = over / len(rows)
    return _clamp01(shrink_rate(raw, league_baseline, len(rows), k=4.0))


def _standings_table(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("data", "response", "standings", "table", "results"):
            val = payload.get(key)
            if isinstance(val, list):
                # Nested [[...]] (multi-group)
                if val and isinstance(val[0], list):
                    flat: list[dict[str, Any]] = []
                    for group in val:
                        flat.extend(r for r in group if isinstance(r, dict))
                    return flat
                return [r for r in val if isinstance(r, dict)]
            if isinstance(val, dict):
                for k2 in ("table", "standings", "rows"):
                    inner = val.get(k2)
                    if isinstance(inner, list):
                        return [r for r in inner if isinstance(r, dict)]
        # Single team row
        if "rank" in payload or "position" in payload or "team" in payload:
            return [payload]
    return []


def _team_rank(row: Mapping[str, Any]) -> tuple[str | None, int | None, int | None]:
    team = row.get("team") if isinstance(row.get("team"), dict) else {}
    tid = (
        row.get("team_id")
        or row.get("teamId")
        or team.get("id")
        or row.get("id")
    )
    rank = row.get("rank") or row.get("position") or row.get("overall_league_position")
    played = row.get("played") or row.get("playedGames") or row.get("matches") or row.get("overall_league_pay")
    try:
        rank_i = int(rank) if rank is not None else None
    except (TypeError, ValueError):
        rank_i = None
    try:
        played_i = int(played) if played is not None else None
    except (TypeError, ValueError):
        played_i = None
    tid_s = str(tid).strip() if tid is not None and str(tid).strip() else None
    return tid_s, rank_i, played_i


def raw_standings(
    standings_payload: Any,
    home_team_id: int | str,
    away_team_id: int | str,
) -> dict[str, int] | None:
    """Home/away table ranks for UI. Omit if either rank missing."""
    table = _standings_table(standings_payload)
    if not table:
        return None
    ranks: dict[str, int] = {}
    n_teams = 0
    for row in table:
        tid, rank, _ = _team_rank(row)
        if tid is None or rank is None:
            continue
        ranks[tid] = rank
        n_teams = max(n_teams, rank)
    hid, aid = str(home_team_id), str(away_team_id)
    if hid not in ranks or aid not in ranks:
        return None
    if n_teams <= 1:
        n_teams = max(ranks.values()) if ranks else 20
    return {
        "home_rank": int(ranks[hid]),
        "away_rank": int(ranks[aid]),
        "n_teams": int(n_teams),
        "gap": abs(int(ranks[hid]) - int(ranks[aid])),
    }


def signal_standings(
    standings_payload: Any,
    home_team_id: int | str,
    away_team_id: int | str,
) -> float | None:
    """Rank-gap / table incentive signal. Omit if ranks missing."""
    raw = raw_standings(standings_payload, home_team_id, away_team_id)
    if raw is None:
        return None
    rh, ra, n_teams = raw["home_rank"], raw["away_rank"], raw["n_teams"]
    gap = abs(rh - ra)
    # Mid-table scrapes and large gaps both tend to open games more than
    # two ultra-defensive top sides; keep a mild U-shape vs tight mid gap.
    # Higher gap → slightly higher Over; both bottom-3 → higher.
    gap_term = min(1.0, gap / max(8.0, n_teams / 2.5))
    bottom = (rh >= n_teams - 2) or (ra >= n_teams - 2)
    top_clash = rh <= 3 and ra <= 3
    signal = 0.48 + 0.18 * gap_term
    if bottom:
        signal += 0.08
    if top_clash:
        signal -= 0.04
    return _clamp01(signal)


def signal_fatigue(
    finished: Sequence[Any],
    home_team_id: int | str,
    away_team_id: int | str,
    *,
    kickoff: datetime | str | int | float | None = None,
) -> float | None:
    """Congestion signal: recent matches → lower Over (tired legs / rotation).

    Also returns a boolean flag via :func:`fatigue_flag_from_signal` for
    dynamic weights. Omit if kickoff or dated schedule unavailable.
    """
    ko = _parse_dt(kickoff)
    if ko is None:
        return None
    rows = _as_rich(finished)
    dated = [r for r in rows if r.kickoff is not None]
    if not dated:
        return None

    def _team_load(tid: int | str) -> tuple[int, int]:
        """Return (matches_in_72h, matches_in_7d) before kickoff."""
        in_72 = 0
        in_7 = 0
        for r in dated:
            if r.home_team_id != tid and r.away_team_id != tid:
                continue
            assert r.kickoff is not None
            if r.kickoff >= ko:
                continue
            delta = ko - r.kickoff
            if delta <= timedelta(hours=72):
                in_72 += 1
            if delta <= timedelta(days=7):
                in_7 += 1
        return in_72, in_7

    h72, h7 = _team_load(home_team_id)
    a72, a7 = _team_load(away_team_id)
    # If we have no prior matches in window for either side, still return
    # a neutral-ish signal only when schedule context exists overall —
    # but with zero congestion treat as fresh (slightly higher).
    congested = (h72 + a72) >= 1 or (h7 >= 3) or (a7 >= 3)
    load = (h72 + a72) * 1.5 + max(0, h7 - 1) * 0.4 + max(0, a7 - 1) * 0.4
    if not congested and h7 == 0 and a7 == 0:
        # Schedule known but no recent games → mild fresh.
        return 0.55
    # More load → lower Over probability signal.
    return _clamp01(0.58 - 0.10 * min(4.0, load))


def fatigue_flag_from_signal(fatigue_signal: float | None) -> bool:
    """True when fatigue signal indicates meaningful congestion."""
    if fatigue_signal is None:
        return False
    return float(fatigue_signal) <= 0.45


@dataclass(frozen=True)
class ExtraSignalsResult:
    signals: dict[str, float]
    fatigue_flag: bool
    notes: tuple[str, ...]
    # Raw inputs for fixture UI (only present when the matching signal is).
    raw_features: dict[str, Any] = field(default_factory=dict)


def build_extra_signals(
    *,
    home_team_id: int | str,
    away_team_id: int | str,
    finished: Sequence[Any] | None = None,
    h2h_rows: Sequence[Any] | None = None,
    standings_payload: Any | None = None,
    kickoff: datetime | str | int | float | None = None,
    league_baseline: float = 0.92,
) -> ExtraSignalsResult:
    """Build available extra signals; omit missing keys (fail-closed)."""
    hist = list(finished or [])
    signals: dict[str, float] = {}
    raw_features: dict[str, Any] = {}
    notes: list[str] = []

    form = signal_form(
        hist, home_team_id, away_team_id, league_baseline=league_baseline
    )
    if form is not None:
        signals["form"] = form
    else:
        notes.append("omit:form")

    gm = signal_goal_minutes_last5(hist, home_team_id, away_team_id)
    if gm is not None:
        signals["goal_minutes_last5"] = gm
    else:
        notes.append("omit:goal_minutes_last5")

    st = signal_streaks(hist, home_team_id, away_team_id)
    if st is not None:
        signals["streaks"] = st
    else:
        notes.append("omit:streaks")

    mu = signal_matchup(
        hist, home_team_id, away_team_id, league_baseline=league_baseline
    )
    if mu is not None:
        signals["matchup"] = mu
    else:
        notes.append("omit:matchup")

    gf5_raw = raw_goals_scored_last5_ha(hist, home_team_id, away_team_id)
    gf5 = signal_goals_scored_last5_ha(hist, home_team_id, away_team_id)
    if gf5 is not None and gf5_raw is not None:
        signals["goals_scored_last5_ha"] = gf5
        raw_features["goals_scored_last5_ha_home_avg"] = gf5_raw["home_avg"]
        raw_features["goals_scored_last5_ha_away_avg"] = gf5_raw["away_avg"]
        raw_features["goals_scored_last5_ha_avg"] = gf5_raw["combined"]
        raw_features["goals_scored_last5_ha_home_n"] = gf5_raw["home_n"]
        raw_features["goals_scored_last5_ha_away_n"] = gf5_raw["away_n"]
    else:
        notes.append("omit:goals_scored_last5_ha")

    h2h = signal_club_h2h(h2h_rows or [], league_baseline=league_baseline)
    if h2h is not None:
        signals["club_h2h"] = h2h
    else:
        notes.append("omit:club_h2h")

    stand_raw = raw_standings(standings_payload, home_team_id, away_team_id)
    stand = signal_standings(standings_payload, home_team_id, away_team_id)
    if stand is not None and stand_raw is not None:
        signals["standings"] = stand
        raw_features["standings_home_rank"] = stand_raw["home_rank"]
        raw_features["standings_away_rank"] = stand_raw["away_rank"]
        raw_features["standings_n_teams"] = stand_raw["n_teams"]
        raw_features["standings_gap"] = stand_raw["gap"]
        raw_features["standings_label"] = (
            f"{stand_raw['home_rank']}ª–{stand_raw['away_rank']}ª"
        )
    else:
        notes.append("omit:standings")

    fat = signal_fatigue(hist, home_team_id, away_team_id, kickoff=kickoff)
    fat_flag = False
    if fat is not None:
        signals["fatigue"] = fat
        fat_flag = fatigue_flag_from_signal(fat)
    else:
        notes.append("omit:fatigue")

    return ExtraSignalsResult(
        signals=signals,
        fatigue_flag=fat_flag,
        notes=tuple(notes),
        raw_features=raw_features,
    )
