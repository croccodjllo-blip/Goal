"""Parse GOAL live statistics / events into live @30′ feature counts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class LiveVolumeStats:
    """Counts / rates at the ≈30′ snapshot (prefer firstHalf when available)."""

    sot_home: float | None = None
    sot_away: float | None = None
    attacks_home: float | None = None
    attacks_away: float | None = None
    dangerous_attacks_home: float | None = None
    dangerous_attacks_away: float | None = None
    corners_home: float | None = None
    corners_away: float | None = None
    possession_home: float | None = None
    possession_away: float | None = None
    saves_home: float | None = None
    saves_away: float | None = None
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
    source_half: str | None = None  # "firstHalf" | "fullTime" | …
    notes: tuple[str, ...] = ()

    @property
    def sot_total(self) -> float | None:
        return _sum_opt(self.sot_home, self.sot_away)

    @property
    def corners_total(self) -> float | None:
        return _sum_opt(self.corners_home, self.corners_away)

    @property
    def saves_total(self) -> float | None:
        return _sum_opt(self.saves_home, self.saves_away)

    @property
    def attacks_total(self) -> float | None:
        # Prefer dangerous attacks when both sides present.
        dang = _sum_opt(self.dangerous_attacks_home, self.dangerous_attacks_away)
        if dang is not None:
            return dang
        return _sum_opt(self.attacks_home, self.attacks_away)

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


# Provider type-name aliases → internal field prefix.
_STAT_ALIASES: dict[str, str] = {
    "shots on goal": "sot",
    "shots on target": "sot",
    "shot on target": "sot",
    "sot": "sot",
    "on target": "sot",
    "corners": "corners",
    "corner kicks": "corners",
    "ball possession": "possession",
    "possession": "possession",
    "possession %": "possession",
    "attacks": "attacks",
    "attack": "attacks",
    "dangerous attacks": "dangerous_attacks",
    "dangerous attack": "dangerous_attacks",
    "goalkeeper saves": "saves",
    "saves": "saves",
    "gk saves": "saves",
    "yellow cards": "yellows",
    "yellow card": "yellows",
    "red cards": "red",
    "red card": "red",
}


def _pick_half_block(match_block: Mapping[str, Any]) -> tuple[Mapping[str, Any] | None, str | None]:
    """Prefer firstHalf / 1half for the ~30′ snapshot."""
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
    # Flat {type: {home, away}} map.
    rows: list[dict[str, Any]] = []
    for k, v in block.items():
        if isinstance(v, dict) and ("home" in v or "away" in v or "homeValue" in v):
            rows.append({"type": k, **v})
        elif isinstance(v, (int, float, str)) and k.lower() not in {"period", "half"}:
            # Skip non-stat scalars.
            continue
    return rows


def _home_away_from_row(row: Mapping[str, Any]) -> tuple[float | None, float | None]:
    home = row.get("home")
    if home is None:
        home = row.get("homeValue") or row.get("home_value") or row.get("h")
    away = row.get("away")
    if away is None:
        away = row.get("awayValue") or row.get("away_value") or row.get("a")
    # Nested team blocks.
    if home is None and isinstance(row.get("teams"), dict):
        teams = row["teams"]
        th = teams.get("home") if isinstance(teams.get("home"), dict) else {}
        ta = teams.get("away") if isinstance(teams.get("away"), dict) else {}
        home = th.get("value") or th.get("statistics")
        away = ta.get("value") or ta.get("statistics")
    return _to_float(home), _to_float(away)


def parse_statistics_payload(payload: Any) -> LiveVolumeStats:
    """Best-effort parse of ``GET /fixtures/:id/statistics`` (prefer firstHalf)."""
    notes: list[str] = []
    data = _unwrap_data(payload)
    half_label: str | None = None
    rows: list[dict[str, Any]] = []

    if isinstance(data, dict):
        # Shape: { match: { firstHalf: ..., fullTime: ... } } or direct halves.
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
        key = _STAT_ALIASES.get(typ)
        if key is None:
            # Fuzzy contains.
            for alias, mapped in _STAT_ALIASES.items():
                if alias in typ:
                    key = mapped
                    break
        if key is None:
            continue
        home, away = _home_away_from_row(row)
        fields[f"{key}_home"] = home
        fields[f"{key}_away"] = away

    if not fields:
        return LiveVolumeStats(
            source_half=half_label,
            notes=("no_parseable_statistics",),
        )

    return LiveVolumeStats(
        sot_home=fields.get("sot_home"),
        sot_away=fields.get("sot_away"),
        attacks_home=fields.get("attacks_home"),
        attacks_away=fields.get("attacks_away"),
        dangerous_attacks_home=fields.get("dangerous_attacks_home"),
        dangerous_attacks_away=fields.get("dangerous_attacks_away"),
        corners_home=fields.get("corners_home"),
        corners_away=fields.get("corners_away"),
        possession_home=fields.get("possession_home"),
        possession_away=fields.get("possession_away"),
        saves_home=fields.get("saves_home"),
        saves_away=fields.get("saves_away"),
        yellows_home=fields.get("yellows_home"),
        yellows_away=fields.get("yellows_away"),
        red_home=fields.get("red_home"),
        red_away=fields.get("red_away"),
        source_half=half_label,
        notes=tuple(notes) if isinstance(notes, list) else (),
    )


def merge_events_into_stats(
    stats: LiveVolumeStats,
    *,
    cards: Sequence[Mapping[str, Any]] | None = None,
    substitutions: Any = None,
    lineups: Mapping[str, Any] | None = None,
    goalscorer: Sequence[Mapping[str, Any]] | None = None,
) -> LiveVolumeStats:
    """Enrich volume stats with cards / subs / formation from WS or REST."""
    yellow_h = stats.yellows_home
    yellow_a = stats.yellows_away
    red_h = stats.red_home
    red_a = stats.red_away
    def_y_h = stats.def_yellows_home
    def_y_a = stats.def_yellows_away

    role_by_name: dict[str, str] = {}
    if isinstance(lineups, dict):
        for side_key, side in (("home", "home"), ("away", "away")):
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
        yellow_h = float(yh) if cards else yellow_h
        yellow_a = float(ya) if cards else yellow_a
        red_h = float(rh) if cards else red_h
        red_a = float(ra) if cards else red_a
        # Defender yellows only when lineup roles known; else leave None (omit).
        if role_by_name:
            def_y_h = float(dyh)
            def_y_a = float(dya)
        elif yellow_h is None and yellow_a is None:
            pass

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
            # Empty dict → 0 subs known.
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
                    # Ambiguous — count toward total via home bucket heuristic.
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
        for side, ko_attr, now_attr in (
            ("home", "formation_home_ko", "formation_home_now"),
            ("away", "formation_away_ko", "formation_away_now"),
        ):
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
        # GOAL-style top-level systems.
        if lineups.get("homeTeamSystem"):
            form_h_ko = form_h_ko or str(lineups["homeTeamSystem"])
            form_h_now = form_h_now or str(lineups["homeTeamSystem"])
        if lineups.get("awayTeamSystem"):
            form_a_ko = form_a_ko or str(lineups["awayTeamSystem"])
            form_a_now = form_a_now or str(lineups["awayTeamSystem"])

    # goalscorer unused for volume (score comes from clock) — silence unused.
    _ = goalscorer

    return LiveVolumeStats(
        sot_home=stats.sot_home,
        sot_away=stats.sot_away,
        attacks_home=stats.attacks_home,
        attacks_away=stats.attacks_away,
        dangerous_attacks_home=stats.dangerous_attacks_home,
        dangerous_attacks_away=stats.dangerous_attacks_away,
        corners_home=stats.corners_home,
        corners_away=stats.corners_away,
        possession_home=stats.possession_home,
        possession_away=stats.possession_away,
        saves_home=stats.saves_home,
        saves_away=stats.saves_away,
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
    """Crude KO→now formation shift: more defenders ⇒ defensive."""
    def _def_count(formation: str | None) -> int | None:
        if not formation:
            return None
        parts = str(formation).replace("-", " ").split()
        digits = [int(p) for p in parts if p.isdigit()]
        if not digits:
            return None
        return digits[0]  # first number ≈ defenders

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
