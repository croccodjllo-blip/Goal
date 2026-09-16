"""Parse GOAL live statistics / events into live @30′ shot features."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class LiveVolumeStats:
    """Shot counts / rates at the ≈30′ snapshot (prefer firstHalf)."""

    shots_total_home: float | None = None
    shots_total_away: float | None = None
    sot_home: float | None = None
    sot_away: float | None = None
    shot_xg_home: float | None = None
    shot_xg_away: float | None = None
    xgot_home: float | None = None
    xgot_away: float | None = None
    woodwork_home: float | None = None
    woodwork_away: float | None = None
    shots_off_home: float | None = None
    shots_off_away: float | None = None
    shots_blocked_home: float | None = None
    shots_blocked_away: float | None = None
    shots_inside_box_home: float | None = None
    shots_inside_box_away: float | None = None
    shots_outside_box_home: float | None = None
    shots_outside_box_away: float | None = None
    # Kept for event merge / formation (not in shot index blend).
    yellows_home: float | None = None
    yellows_away: float | None = None
    def_yellows_home: float | None = None
    def_yellows_away: float | None = None
    red_home: float | None = None
    red_away: float | None = None
    subs_home: int | None = None
    subs_away: int | None = None
    formation_home_ko: str | None = None
    formation_away_ko: str | None = None
    formation_home_now: str | None = None
    formation_away_now: str | None = None
    source_half: str | None = None
    notes: tuple[str, ...] = ()

    @property
    def shots_total(self) -> float | None:
        return _sum_opt(self.shots_total_home, self.shots_total_away)

    @property
    def sot_total(self) -> float | None:
        return _sum_opt(self.sot_home, self.sot_away)

    @property
    def shot_xg_total(self) -> float | None:
        return _sum_opt(self.shot_xg_home, self.shot_xg_away)

    @property
    def xgot_total(self) -> float | None:
        return _sum_opt(self.xgot_home, self.xgot_away)

    @property
    def woodwork_total(self) -> float | None:
        return _sum_opt(self.woodwork_home, self.woodwork_away)

    @property
    def shots_off_total(self) -> float | None:
        return _sum_opt(self.shots_off_home, self.shots_off_away)

    @property
    def shots_blocked_total(self) -> float | None:
        return _sum_opt(self.shots_blocked_home, self.shots_blocked_away)

    @property
    def shots_inside_box_total(self) -> float | None:
        return _sum_opt(self.shots_inside_box_home, self.shots_inside_box_away)

    @property
    def shots_outside_box_total(self) -> float | None:
        return _sum_opt(self.shots_outside_box_home, self.shots_outside_box_away)

    @property
    def def_yellows_total(self) -> float | None:
        return _sum_opt(self.def_yellows_home, self.def_yellows_away)

    @property
    def subs_total(self) -> int | None:
        if self.subs_home is None and self.subs_away is None:
            return None
        return int(self.subs_home or 0) + int(self.subs_away or 0)

    @property
    def has_red(self) -> bool:
        return (self.red_home or 0) > 0 or (self.red_away or 0) > 0


def _sum_opt(a: float | None, b: float | None) -> float | None:
    if a is None and b is None:
        return None
    return float(a or 0.0) + float(b or 0.0)


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("%", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _unwrap_data(payload: Any) -> Any:
    if isinstance(payload, dict):
        for key in ("data", "response", "statistics", "match"):
            if key in payload and payload[key] is not None:
                return payload[key]
    return payload


# Provider type-name aliases → internal field prefix (shot index + cards).
_STAT_ALIASES: dict[str, str] = {
    # Tiri totali
    "total shots": "shots_total",
    "shots total": "shots_total",
    "total shot": "shots_total",
    "shots": "shots_total",
    "shot attempts": "shots_total",
    # Tiri in porta
    "shots on goal": "sot",
    "shots on target": "sot",
    "shot on target": "sot",
    "sot": "sot",
    "on target": "sot",
    # Goal attesi (xG)
    "expected goals": "shot_xg",
    "expected goal": "shot_xg",
    "xg": "shot_xg",
    "xgoals": "shot_xg",
    "goals expected": "shot_xg",
    # xGOT
    "expected goals on target": "xgot",
    "xg on target": "xgot",
    "xgot": "xgot",
    "expected goals on target (xgot)": "xgot",
    # Pali e traverse
    "hit woodwork": "woodwork",
    "woodwork": "woodwork",
    "goal post": "woodwork",
    "posts and bars": "woodwork",
    "pali e traverse": "woodwork",
    # Tiri fuori
    "shots off goal": "shots_off",
    "shots off target": "shots_off",
    "shot off target": "shots_off",
    "off target": "shots_off",
    "shots off": "shots_off",
    # Tiri respinti
    "blocked shots": "shots_blocked",
    "shots blocked": "shots_blocked",
    "blocked shot": "shots_blocked",
    # Tiri in area
    "shots insidebox": "shots_inside_box",
    "shots inside box": "shots_inside_box",
    "shots in the box": "shots_inside_box",
    "inside box": "shots_inside_box",
    "shots inside the box": "shots_inside_box",
    # Tiri da fuori
    "shots outsidebox": "shots_outside_box",
    "shots outside box": "shots_outside_box",
    "shots out of the box": "shots_outside_box",
    "outside box": "shots_outside_box",
    "shots outside the box": "shots_outside_box",
    # Cards (event enrichment only)
    "yellow cards": "yellows",
    "yellow card": "yellows",
    "red cards": "red",
    "red card": "red",
}

# Prefer more specific aliases when fuzzy-matching (longer first).
_ALIAS_FUZZY = sorted(_STAT_ALIASES.items(), key=lambda kv: len(kv[0]), reverse=True)


def _pick_half_block(match_block: Mapping[str, Any]) -> tuple[Mapping[str, Any] | None, str | None]:
    for key, label in (
        ("firstHalf", "firstHalf"),
        ("1half", "firstHalf"),
        ("first_half", "firstHalf"),
        ("fullTime", "fullTime"),
        ("full", "fullTime"),
        ("full_time", "fullTime"),
    ):
        block = match_block.get(key)
        if isinstance(block, dict) and block:
            return block, label
        if isinstance(block, list) and block:
            return {"rows": block}, label
    return None, None


def _rows_from_block(block: Mapping[str, Any] | Sequence[Any]) -> list[dict[str, Any]]:
    if isinstance(block, list):
        return [r for r in block if isinstance(r, dict)]
    if not isinstance(block, dict):
        return []
    for key in ("statistics", "stats", "rows", "items", "data"):
        val = block.get(key)
        if isinstance(val, list):
            return [r for r in val if isinstance(r, dict)]
    rows: list[dict[str, Any]] = []
    for k, v in block.items():
        if isinstance(v, dict) and ("home" in v or "away" in v or "homeValue" in v):
            rows.append({"type": k, **v})
    return rows


def _home_away_from_row(row: Mapping[str, Any]) -> tuple[float | None, float | None]:
    home = row.get("home")
    if home is None:
        home = row.get("homeValue") or row.get("home_value") or row.get("h")
    away = row.get("away")
    if away is None:
        away = row.get("awayValue") or row.get("away_value") or row.get("a")
    if home is None and isinstance(row.get("teams"), dict):
        teams = row["teams"]
        th = teams.get("home") if isinstance(teams.get("home"), dict) else {}
        ta = teams.get("away") if isinstance(teams.get("away"), dict) else {}
        home = th.get("value") or th.get("statistics")
        away = ta.get("value") or ta.get("statistics")
    return _to_float(home), _to_float(away)


def _map_stat_type(typ: str) -> str | None:
    key = _STAT_ALIASES.get(typ)
    if key is not None:
        return key
    for alias, mapped in _ALIAS_FUZZY:
        if alias in typ:
            return mapped
    return None


def parse_statistics_payload(payload: Any) -> LiveVolumeStats:
    """Best-effort parse of ``GET /fixtures/:id/statistics`` (prefer firstHalf)."""
    notes: list[str] = []
    data = _unwrap_data(payload)
    half_label: str | None = None
    rows: list[dict[str, Any]] = []

    if isinstance(data, dict):
        match = data.get("match") if isinstance(data.get("match"), dict) else data
        if isinstance(match, dict):
            block, half_label = _pick_half_block(match)
            if block is not None:
                rows = _rows_from_block(block)
            elif any(k in match for k in ("statistics", "stats", "rows")):
                rows = _rows_from_block(match)
                half_label = half_label or "unknown"
        if not rows:
            rows = _rows_from_block(data)
    elif isinstance(data, list):
        rows = [r for r in data if isinstance(r, dict)]

    fields: dict[str, float | None] = {}
    for row in rows:
        typ = str(row.get("type") or row.get("name") or row.get("stat") or "").strip().lower()
        key = _map_stat_type(typ)
        if key is None:
            continue
        # Avoid mapping bare "shots" over more specific rows already set —
        # first match wins; specific aliases are exact-matched first.
        home, away = _home_away_from_row(row)
        hk, ak = f"{key}_home", f"{key}_away"
        if hk not in fields:
            fields[hk] = home
            fields[ak] = away

    if not fields:
        return LiveVolumeStats(
            source_half=half_label,
            notes=("no_parseable_statistics",),
        )

    return LiveVolumeStats(
        shots_total_home=fields.get("shots_total_home"),
        shots_total_away=fields.get("shots_total_away"),
        sot_home=fields.get("sot_home"),
        sot_away=fields.get("sot_away"),
        shot_xg_home=fields.get("shot_xg_home"),
        shot_xg_away=fields.get("shot_xg_away"),
        xgot_home=fields.get("xgot_home"),
        xgot_away=fields.get("xgot_away"),
        woodwork_home=fields.get("woodwork_home"),
        woodwork_away=fields.get("woodwork_away"),
        shots_off_home=fields.get("shots_off_home"),
        shots_off_away=fields.get("shots_off_away"),
        shots_blocked_home=fields.get("shots_blocked_home"),
        shots_blocked_away=fields.get("shots_blocked_away"),
        shots_inside_box_home=fields.get("shots_inside_box_home"),
        shots_inside_box_away=fields.get("shots_inside_box_away"),
        shots_outside_box_home=fields.get("shots_outside_box_home"),
        shots_outside_box_away=fields.get("shots_outside_box_away"),
        yellows_home=fields.get("yellows_home"),
        yellows_away=fields.get("yellows_away"),
        red_home=fields.get("red_home"),
        red_away=fields.get("red_away"),
        source_half=half_label,
        notes=tuple(notes),
    )


def merge_events_into_stats(
    stats: LiveVolumeStats,
    *,
    cards: Sequence[Mapping[str, Any]] | None = None,
    substitutions: Any = None,
    lineups: Mapping[str, Any] | None = None,
    goalscorer: Sequence[Mapping[str, Any]] | None = None,
) -> LiveVolumeStats:
    """Enrich with cards / subs / formation from WS or REST (not shot-index)."""
    yellow_h = stats.yellows_home
    yellow_a = stats.yellows_away
    red_h = stats.red_home
    red_a = stats.red_away
    def_y_h = stats.def_yellows_home
    def_y_a = stats.def_yellows_away

    role_by_name: dict[str, str] = {}
    if isinstance(lineups, dict):
        for side_key in ("home", "away"):
            block = lineups.get(side_key) or lineups.get(f"{side_key}Team")
            if not isinstance(block, dict):
                continue
            players = block.get("startXI") or block.get("players") or block.get("lineup") or []
            if isinstance(players, list):
                for p in players:
                    if not isinstance(p, dict):
                        continue
                    player = p.get("player") if isinstance(p.get("player"), dict) else p
                    name = str(player.get("name") or "").strip().lower()
                    pos = str(player.get("pos") or player.get("position") or "").upper()
                    if name:
                        role_by_name[name] = pos

    if cards:
        yh = ya = rh = ra = dyh = dya = 0
        for card in cards:
            if not isinstance(card, dict):
                continue
            kind = str(card.get("card") or card.get("type") or "").lower()
            is_yellow = "yellow" in kind
            is_red = "red" in kind
            home_fault = card.get("home_fault") or card.get("homeFault")
            away_fault = card.get("away_fault") or card.get("awayFault")
            name = str(home_fault or away_fault or card.get("player") or "").strip().lower()
            side_home = home_fault is not None or str(card.get("team") or "").lower() == "home"
            if away_fault is not None:
                side_home = False
            if is_yellow:
                if side_home:
                    yh += 1
                else:
                    ya += 1
                pos = role_by_name.get(name, "")
                if any(tag in pos for tag in ("D", "CB", "FB", "WB", "LB", "RB", "DEF")):
                    if side_home:
                        dyh += 1
                    else:
                        dya += 1
            if is_red:
                if side_home:
                    rh += 1
                else:
                    ra += 1
        yellow_h = float(yh)
        yellow_a = float(ya)
        red_h = float(rh)
        red_a = float(ra)
        if role_by_name:
            def_y_h = float(dyh)
            def_y_a = float(dya)

    subs_h = stats.subs_home
    subs_a = stats.subs_away
    if substitutions is not None:
        if isinstance(substitutions, dict):
            home_subs = substitutions.get("home") or substitutions.get("homeTeam") or []
            away_subs = substitutions.get("away") or substitutions.get("awayTeam") or []
            if isinstance(home_subs, list):
                subs_h = len(home_subs)
            if isinstance(away_subs, list):
                subs_a = len(away_subs)
            if not home_subs and not away_subs and substitutions == {}:
                subs_h = 0
                subs_a = 0
        elif isinstance(substitutions, list):
            sh = sa = 0
            for row in substitutions:
                if not isinstance(row, dict):
                    continue
                team = str(row.get("team") or row.get("side") or "").lower()
                if team in {"home", "h"}:
                    sh += 1
                elif team in {"away", "a"}:
                    sa += 1
                else:
                    if row.get("home_player") or row.get("homePlayer"):
                        sh += 1
                    elif row.get("away_player") or row.get("awayPlayer"):
                        sa += 1
            subs_h, subs_a = sh, sa

    form_h_ko = stats.formation_home_ko
    form_a_ko = stats.formation_away_ko
    form_h_now = stats.formation_home_now
    form_a_now = stats.formation_away_now
    if isinstance(lineups, dict):
        for side in ("home", "away"):
            block = lineups.get(side) or lineups.get(f"{side}Team")
            if not isinstance(block, dict):
                continue
            formation = block.get("formation") or block.get("system") or block.get("teamSystem")
            if formation:
                if side == "home":
                    form_h_ko = form_h_ko or str(formation)
                    form_h_now = form_h_now or str(formation)
                else:
                    form_a_ko = form_a_ko or str(formation)
                    form_a_now = form_a_now or str(formation)
        if lineups.get("homeTeamSystem"):
            form_h_ko = form_h_ko or str(lineups["homeTeamSystem"])
            form_h_now = form_h_now or str(lineups["homeTeamSystem"])
        if lineups.get("awayTeamSystem"):
            form_a_ko = form_a_ko or str(lineups["awayTeamSystem"])
            form_a_now = form_a_now or str(lineups["awayTeamSystem"])

    _ = goalscorer

    return LiveVolumeStats(
        shots_total_home=stats.shots_total_home,
        shots_total_away=stats.shots_total_away,
        sot_home=stats.sot_home,
        sot_away=stats.sot_away,
        shot_xg_home=stats.shot_xg_home,
        shot_xg_away=stats.shot_xg_away,
        xgot_home=stats.xgot_home,
        xgot_away=stats.xgot_away,
        woodwork_home=stats.woodwork_home,
        woodwork_away=stats.woodwork_away,
        shots_off_home=stats.shots_off_home,
        shots_off_away=stats.shots_off_away,
        shots_blocked_home=stats.shots_blocked_home,
        shots_blocked_away=stats.shots_blocked_away,
        shots_inside_box_home=stats.shots_inside_box_home,
        shots_inside_box_away=stats.shots_inside_box_away,
        shots_outside_box_home=stats.shots_outside_box_home,
        shots_outside_box_away=stats.shots_outside_box_away,
        yellows_home=yellow_h if yellow_h is not None else stats.yellows_home,
        yellows_away=yellow_a if yellow_a is not None else stats.yellows_away,
        def_yellows_home=def_y_h,
        def_yellows_away=def_y_a,
        red_home=red_h if red_h is not None else stats.red_home,
        red_away=red_a if red_a is not None else stats.red_away,
        subs_home=subs_h,
        subs_away=subs_a,
        formation_home_ko=form_h_ko,
        formation_away_ko=form_a_ko,
        formation_home_now=form_h_now,
        formation_away_now=form_a_now,
        source_half=stats.source_half,
        notes=stats.notes,
    )


def formation_shift_label(stats: LiveVolumeStats) -> str | None:
    """Crude KO→now formation shift (metadata only; not in shot index)."""

    def _def_count(formation: str | None) -> int | None:
        if not formation:
            return None
        parts = str(formation).replace("-", " ").split()
        digits = [int(p) for p in parts if p.isdigit()]
        if not digits:
            return None
        return digits[0]

    shifts: list[str] = []
    for ko, now in (
        (stats.formation_home_ko, stats.formation_home_now),
        (stats.formation_away_ko, stats.formation_away_now),
    ):
        a, b = _def_count(ko), _def_count(now)
        if a is None or b is None or a == b:
            continue
        shifts.append("defensive" if b > a else "offensive")
    if not shifts:
        return None
    if shifts.count("defensive") > shifts.count("offensive"):
        return "defensive"
    if shifts.count("offensive") > shifts.count("defensive"):
        return "offensive"
    return None
