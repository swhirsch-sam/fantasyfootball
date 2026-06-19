"""Projection data sources for the dynasty auction tool.

Design
------
Network access and parsing are deliberately separated:

* ``fetch_*``   -> perform the HTTP request, return the raw decoded JSON.
* ``parse_*``   -> pure functions that turn raw JSON into ``Projection``s.

Keeping the parsers pure is what makes the stat mapping *verifiable* without
hitting a live API: the tests push realistic payloads straight through the
parsers (see ``tests/test_data_sources.py``).

Stat-mapping provenance
-----------------------
* ESPN offensive stat IDs were cross-checked 1:1 against the canonical
  ``espn-api`` ``STATS_MAP`` (3=passYds, 4=passTD, 20=INT, 24=rushYds,
  25=rushTD, 53=receptions, 42=recYds, 43=recTD, 72=lostFumbles,
  19/26/44=2pt pass/rush/rec).
* Sleeper keys were checked against the Sleeper projection schema
  (pass_yd/rush_yd/rec_yd/rec/fum_lost, fgm_*/xpm/xpmiss, pts_allow/sack/
  int/fum_rec/def_td/safe/blk_kick).  Sleeper only buckets *missed* field
  goals at 30-39 / 40-49 / 50+, so those collapse into a single ``fgmiss``.
* ESPN kicker & defense stats are intentionally **not** mapped: ESPN's
  points-allowed and FG buckets use ranges that don't line up with this
  league's tiers, so the blend takes K/DST from Sleeper instead.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

try:  # requests is optional for the offline/SAMPLE path
    import requests
except Exception:  # pragma: no cover - exercised only when requests missing
    requests = None  # type: ignore

__all__ = [
    "Projection", "Diagnostics", "get_projections", "sample_projections",
    "blend_projections", "describe_blend", "parse_espn_players",
    "parse_sleeper_week", "aggregate_sleeper_weeks", "fetch_espn",
    "fetch_sleeper_season", "load_espn_snapshot", "load_sleeper_snapshot",
    "snapshot_paths", "normalize_position",
    "OFFENSE_POSITIONS", "SPECIAL_POSITIONS", "ESPN_STAT_MAP", "SLEEPER_STAT_MAP",
]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
OFFENSE_POSITIONS = ("QB", "RB", "WR", "TE")
SPECIAL_POSITIONS = ("K", "DST")
ALL_POSITIONS = OFFENSE_POSITIONS + SPECIAL_POSITIONS

POSITION_ALIASES = {"DEF": "DST", "D/ST": "DST", "DST/D": "DST", "PK": "K"}


def normalize_position(pos: Optional[str]) -> str:
    pos = (pos or "").upper().strip()
    return POSITION_ALIASES.get(pos, pos)


@dataclass
class Projection:
    """A single player's season-long projection in canonical stat keys."""

    player_id: str
    name: str
    position: str
    team: str
    stats: Dict[str, float] = field(default_factory=dict)
    source: str = "sample"


@dataclass
class Diagnostics:
    """What happened while loading data — surfaced in the UI and CLI."""

    source_requested: str = ""
    source_used: str = ""
    counts: Dict[str, int] = field(default_factory=dict)
    source_counts: Dict[str, int] = field(default_factory=dict)  # players per API
    unmapped: Dict[str, Set[str]] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def add_unmapped(self, source: str, keys: Iterable[str]) -> None:
        keys = {str(k) for k in keys}
        if keys:
            self.unmapped.setdefault(source, set()).update(keys)


# ---------------------------------------------------------------------------
# ESPN
# ---------------------------------------------------------------------------
# Verified against the canonical espn-api STATS_MAP. Offense only — see module
# docstring for why K/DST are excluded.
ESPN_STAT_MAP: Dict[str, str] = {
    "3": "pass_yd",
    "4": "pass_td",
    "20": "pass_int",
    "19": "pass_2pt",
    "24": "rush_yd",
    "25": "rush_td",
    "26": "rush_2pt",
    "53": "rec",
    "42": "rec_yd",
    "43": "rec_td",
    "44": "rec_2pt",
    "72": "fum_lost",
}

ESPN_POSITION_MAP = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DST"}

# proTeamId -> abbreviation (cosmetic only; never affects scoring).
ESPN_TEAM_MAP = {
    0: "FA", 1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL",
    7: "DEN", 8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV",
    14: "LAR", 15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG",
    20: "NYJ", 21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC", 25: "SF",
    26: "SEA", 27: "TB", 28: "WAS", 29: "CAR", 30: "JAX", 33: "BAL",
    34: "HOU",
}

# ESPN's projection split: statSourceId 1 == projected, 0 == actual.
ESPN_PROJECTED_SOURCE_ID = 1
ESPN_SEASON_SPLIT_ID = 0

ESPN_BASE_URL = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/"
    "{season}/segments/0/leaguedefaults/3"
)


def _select_espn_stat_block(stats: List[dict], season: Optional[int]) -> Optional[dict]:
    """Pick the projected, season-split stat block from a player's stat list."""
    projected = [s for s in stats if s.get("statSourceId") == ESPN_PROJECTED_SOURCE_ID]
    if not projected:
        return None
    if season is not None:
        seasonal = [s for s in projected if s.get("seasonId") == season]
        if seasonal:
            projected = seasonal
    season_split = [
        s for s in projected if s.get("statSplitTypeId") == ESPN_SEASON_SPLIT_ID
    ]
    return (season_split or projected)[0]


def parse_espn_players(
    payload: dict, season: Optional[int] = None, source: str = "espn"
) -> Tuple[List[Projection], Set[str]]:
    """Turn an ESPN ``kona_player_info`` payload into projections.

    Returns ``(projections, unmapped_stat_ids)``.  Unmapped IDs are stat
    numbers ESPN returned that we don't translate — the signal that the map
    may have drifted.
    """
    projections: List[Projection] = []
    unmapped: Set[str] = set()

    for entry in payload.get("players", []):
        player = entry.get("player") or entry
        name = player.get("fullName") or "Unknown"
        position = ESPN_POSITION_MAP.get(player.get("defaultPositionId"), "")
        team = ESPN_TEAM_MAP.get(player.get("proTeamId"), "")
        block = _select_espn_stat_block(player.get("stats", []) or [], season)
        if block is None:
            continue

        raw = block.get("stats", {}) or {}
        stats: Dict[str, float] = {}
        for stat_id, value in raw.items():
            key = ESPN_STAT_MAP.get(str(stat_id))
            if key is None:
                unmapped.add(str(stat_id))
                continue
            try:
                stats[key] = stats.get(key, 0.0) + float(value)
            except (TypeError, ValueError):
                continue

        pid = player.get("id", entry.get("id", name))
        projections.append(
            Projection(
                player_id=f"espn:{pid}",
                name=name,
                position=normalize_position(position),
                team=team,
                stats=stats,
                source=source,
            )
        )
    return projections, unmapped


def fetch_espn(season: int, *, timeout: float = 12.0) -> dict:  # pragma: no cover
    """Fetch season-long projected player stats from ESPN.

    Uses the public read endpoint with an ``X-Fantasy-Filter`` asking for the
    projection stat source (statSourceId=1), season split (statSplitTypeId=0).
    No ``scoringPeriodId`` is sent — that param is for weekly views and made the
    season request 400. On any non-200, the response body is surfaced so the
    real reason (bad season, filter, etc.) is visible in logs.
    """
    if requests is None:
        raise RuntimeError("the 'requests' package is required for live ESPN data")

    url = ESPN_BASE_URL.format(season=season)
    fantasy_filter = (
        '{"players":{"limit":2000,'
        '"sortPercOwned":{"sortAsc":false,"sortPriority":1},'
        '"filterStatsForSourceIds":{"value":[1]},'
        '"filterStatsForSplitTypeIds":{"value":[0]}}}'
    )
    headers = {
        "X-Fantasy-Filter": fantasy_filter,
        "X-Fantasy-Source": "kona",
        "X-Fantasy-Platform": "kona",
        "User-Agent": "Mozilla/5.0 (dynasty-auction-tool)",
        "Accept": "application/json",
    }
    resp = requests.get(url, headers=headers, params={"view": "kona_player_info"},
                        timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(
            f"ESPN HTTP {resp.status_code} for {season}: {resp.text[:300]}")
    return resp.json()


# ---------------------------------------------------------------------------
# Sleeper
# ---------------------------------------------------------------------------
# Sleeper keys are already close to our canonical names. This map both
# whitelists the scoring-relevant keys and renames a few. Several missed-FG
# distance buckets collapse onto a single ``fgmiss``.
SLEEPER_STAT_MAP: Dict[str, str] = {
    # Passing
    "pass_yd": "pass_yd",
    "pass_td": "pass_td",
    "pass_int": "pass_int",
    "pass_2pt": "pass_2pt",
    # Rushing
    "rush_yd": "rush_yd",
    "rush_td": "rush_td",
    "rush_2pt": "rush_2pt",
    # Receiving
    "rec": "rec",
    "rec_yd": "rec_yd",
    "rec_td": "rec_td",
    "rec_2pt": "rec_2pt",
    # Turnovers
    "fum_lost": "fum_lost",
    # Kicking
    "fgm_0_19": "fgm_0_19",
    "fgm_20_29": "fgm_20_29",
    "fgm_30_39": "fgm_30_39",
    "fgm_40_49": "fgm_40_49",
    "fgm_50p": "fgm_50p",
    "xpm": "xpm",
    "xpmiss": "xpmiss",
    # Sleeper only buckets missed FGs at 30-39 / 40-49 / 50+; collapse them.
    "fgmiss": "fgmiss",
    "fgmiss_30_39": "fgmiss",
    "fgmiss_40_49": "fgmiss",
    "fgmiss_50p": "fgmiss",
    # Defense / special teams
    "pts_allow": "pts_allow",
    "sack": "sack",
    "int": "int",
    "fum_rec": "fum_rec",
    "def_td": "def_td",
    "def_st_td": "def_td",
    "st_td": "def_td",
    "safe": "safe",
    "blk_kick": "blk_kick",
}

# Common non-scoring keys Sleeper ships in projections. Filtered out of the
# "unmapped" diagnostic so the signal stays meaningful. Anything not here and
# not in SLEEPER_STAT_MAP surfaces for review.
SLEEPER_IGNORE_KEYS = {
    "gp", "gs", "gms_active", "off_snp", "def_snp", "st_snp", "tm_off_snp",
    "tm_def_snp", "tm_st_snp", "snp",
    "pass_att", "pass_cmp", "pass_inc", "pass_rtg", "cmp_pct", "pass_ypa",
    "pass_ypc", "pass_air_yd", "pass_sack", "pass_sack_yds",
    "rush_att", "rush_ypa", "rush_fd",
    "rec_tgt", "rec_yar", "rec_air_yd", "rec_ypr", "rec_ypt", "rec_fd",
    "bonus_rec_te", "anytime_tds",
    "pts_ppr", "pts_half_ppr", "pts_std",
    "adp_dd_ppr", "pos_adp_dd_ppr", "pass_fd", "fum", "penalty", "penalty_yd",
}


def parse_sleeper_week(
    week_payload: List[dict],
) -> Tuple[List[dict], Set[str]]:
    """Parse one week of Sleeper projections into canonical stat dicts.

    Returns ``(rows, unmapped_keys)`` where each row is a plain dict (it is
    aggregated across weeks before becoming a ``Projection``).
    """
    rows: List[dict] = []
    unmapped: Set[str] = set()

    for entry in week_payload or []:
        pid = str(entry.get("player_id", ""))
        player = entry.get("player") or {}
        name = (
            player.get("full_name")
            or " ".join(
                p for p in (player.get("first_name"), player.get("last_name")) if p
            ).strip()
            or pid
        )
        position = normalize_position(player.get("position"))
        team = player.get("team") or ""

        raw = entry.get("stats") or {}
        stats: Dict[str, float] = {}
        for key, value in raw.items():
            canon = SLEEPER_STAT_MAP.get(key)
            if canon is None:
                if key not in SLEEPER_IGNORE_KEYS:
                    unmapped.add(key)
                continue
            try:
                stats[canon] = stats.get(canon, 0.0) + float(value)
            except (TypeError, ValueError):
                continue

        rows.append(
            {
                "player_id": f"sleeper:{pid}",
                "name": name,
                "position": position,
                "team": team,
                "stats": stats,
            }
        )
    return rows, unmapped


def aggregate_sleeper_weeks(
    weeks: Iterable[List[dict]], source: str = "sleeper"
) -> Tuple[List[Projection], Set[str]]:
    """Sum a sequence of weekly Sleeper payloads into season projections.

    Counting stats are summed across weeks. For defenses, ``pts_allow`` is
    summed and ``games`` records how many weeks the team appeared, so scoring
    can recover a per-game average.
    """
    by_id: Dict[str, dict] = {}
    unmapped: Set[str] = set()

    for week_payload in weeks:
        rows, week_unmapped = parse_sleeper_week(week_payload)
        unmapped |= week_unmapped
        for row in rows:
            agg = by_id.get(row["player_id"])
            if agg is None:
                by_id[row["player_id"]] = {
                    "player_id": row["player_id"],
                    "name": row["name"],
                    "position": row["position"],
                    "team": row["team"],
                    "stats": dict(row["stats"]),
                    "weeks": 1,
                }
            else:
                agg["weeks"] += 1
                for key, value in row["stats"].items():
                    agg["stats"][key] = agg["stats"].get(key, 0.0) + value

    projections: List[Projection] = []
    for agg in by_id.values():
        stats = agg["stats"]
        if agg["position"] == "DST" and "pts_allow" in stats:
            stats.setdefault("games", float(agg["weeks"]))
        projections.append(
            Projection(
                player_id=agg["player_id"],
                name=agg["name"],
                position=agg["position"],
                team=agg["team"],
                stats=stats,
                source=source,
            )
        )
    return projections, unmapped


SLEEPER_PROJECTIONS_URL = (
    "https://api.sleeper.com/projections/nfl/{season}/{week}"
    "?season_type=regular"
)


def fetch_sleeper_week(
    season: int, week: int, *, timeout: float = 12.0
) -> List[dict]:  # pragma: no cover - network
    """Fetch a single week of Sleeper projections (raw JSON list)."""
    if requests is None:
        raise RuntimeError("the 'requests' package is required for live Sleeper data")
    url = SLEEPER_PROJECTIONS_URL.format(season=season, week=week)
    positions = "".join(f"&position[]={p}" for p in ("QB", "RB", "WR", "TE", "K", "DEF"))
    resp = requests.get(url + positions, timeout=timeout,
                        headers={"User-Agent": "dynasty-auction-tool"})
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


def fetch_sleeper_season(
    season: int, weeks: Iterable[int] = range(1, 18), *, timeout: float = 12.0
) -> List[List[dict]]:  # pragma: no cover - network
    """Fetch every requested week of Sleeper projections."""
    return [fetch_sleeper_week(season, w, timeout=timeout) for w in weeks]


# ---------------------------------------------------------------------------
# Blending
# ---------------------------------------------------------------------------
def blend_projections(
    espn: List[Projection], sleeper: List[Projection]
) -> List[Projection]:
    """Best-of-each blend, resilient to one source being unavailable.

    Offense prefers ESPN (cleanest projections) but falls back to Sleeper if
    ESPN returned nothing. Kickers & defenses prefer Sleeper (its buckets line
    up with this league's tiers) but fall back to ESPN. Nothing is averaged —
    and critically, a working source is never discarded because the other one
    failed.
    """
    espn_off = [p for p in espn if p.position in OFFENSE_POSITIONS]
    sleeper_off = [p for p in sleeper if p.position in OFFENSE_POSITIONS]
    espn_sp = [p for p in espn if p.position in SPECIAL_POSITIONS]
    sleeper_sp = [p for p in sleeper if p.position in SPECIAL_POSITIONS]

    offense, off_src = (espn_off, "blend:espn") if espn_off else (sleeper_off, "blend:sleeper")
    special, sp_src = (sleeper_sp, "blend:sleeper") if sleeper_sp else (espn_sp, "blend:espn")
    for p in offense:
        p.source = off_src
    for p in special:
        p.source = sp_src
    return offense + special


def describe_blend(espn: List[Projection], sleeper: List[Projection]) -> str:
    """Human-readable note of which source actually supplied each slot."""
    def src(have_espn, have_sleeper, prefer_sleeper=False):
        if prefer_sleeper:
            return "sleeper" if have_sleeper else ("espn" if have_espn else "none")
        return "espn" if have_espn else ("sleeper" if have_sleeper else "none")

    e_off = any(p.position in OFFENSE_POSITIONS for p in espn)
    s_off = any(p.position in OFFENSE_POSITIONS for p in sleeper)
    e_sp = any(p.position in SPECIAL_POSITIONS for p in espn)
    s_sp = any(p.position in SPECIAL_POSITIONS for p in sleeper)
    return f"blend (offense={src(e_off, s_off)}, K/DST={src(e_sp, s_sp, prefer_sleeper=True)})"


# ---------------------------------------------------------------------------
# SAMPLE data (offline fallback + tests + demos)
# ---------------------------------------------------------------------------
def _proj(name: str, team: str, pos: str, stats: Dict[str, float]) -> Projection:
    pid = "sample:" + name.lower().replace(" ", "_").replace(".", "").replace("'", "")
    return _proj_id(pid, name, team, pos, stats)


def _proj_id(pid, name, team, pos, stats) -> Projection:
    return Projection(pid, name, normalize_position(pos), team,
                      {k: float(v) for k, v in stats.items()}, "sample")


def _qb(name, team, py, ptd, pint, ry=0, rtd=0, fl=0):
    return _proj(name, team, "QB", {
        "pass_yd": py, "pass_td": ptd, "pass_int": pint,
        "rush_yd": ry, "rush_td": rtd, "fum_lost": fl})


def _rb(name, team, ry, rtd, rec, recy, rectd=0, fl=0):
    return _proj(name, team, "RB", {
        "rush_yd": ry, "rush_td": rtd, "rec": rec,
        "rec_yd": recy, "rec_td": rectd, "fum_lost": fl})


def _wr(name, team, rec, recy, rectd, ry=0, rtd=0, fl=0):
    return _proj(name, team, "WR", {
        "rec": rec, "rec_yd": recy, "rec_td": rectd,
        "rush_yd": ry, "rush_td": rtd, "fum_lost": fl})


def _te(name, team, rec, recy, rectd, fl=0):
    return _proj(name, team, "TE", {
        "rec": rec, "rec_yd": recy, "rec_td": rectd, "fum_lost": fl})


def _k(name, team, fg19, fg29, fg39, fg49, fg50, xpm, fgmiss=0, xpmiss=0):
    return _proj(name, team, "K", {
        "fgm_0_19": fg19, "fgm_20_29": fg29, "fgm_30_39": fg39,
        "fgm_40_49": fg49, "fgm_50p": fg50, "xpm": xpm,
        "fgmiss": fgmiss, "xpmiss": xpmiss})


def _dst(name, team, pa_total, sack, intc, fr, dtd, safe=0, blk=0, games=17):
    return _proj(name, team, "DST", {
        "pts_allow": pa_total, "games": games, "sack": sack, "int": intc,
        "fum_rec": fr, "def_td": dtd, "safe": safe, "blk_kick": blk})


def _build_sample() -> List[Projection]:
    players: List[Projection] = []

    # --- Quarterbacks -----------------------------------------------------
    players += [
        _qb("Josh Allen", "BUF", 4100, 32, 11, 580, 9, 4),
        _qb("Lamar Jackson", "BAL", 3800, 27, 8, 870, 6, 3),
        _qb("Jalen Hurts", "PHI", 3700, 24, 10, 640, 12, 4),
        _qb("Jayden Daniels", "WAS", 3900, 25, 9, 720, 7, 5),
        _qb("Patrick Mahomes", "KC", 4300, 29, 11, 380, 3, 3),
        _qb("Joe Burrow", "CIN", 4600, 34, 10, 220, 2, 4),
        _qb("Justin Herbert", "LAC", 4200, 26, 9, 300, 3, 3),
        _qb("Caleb Williams", "CHI", 3900, 23, 12, 420, 4, 6),
        _qb("C.J. Stroud", "HOU", 4100, 25, 11, 250, 3, 4),
        _qb("Dak Prescott", "DAL", 4400, 30, 10, 150, 2, 3),
        _qb("Jared Goff", "DET", 4300, 31, 12, 90, 1, 4),
        _qb("Brock Purdy", "SF", 4200, 28, 11, 280, 3, 3),
        _qb("Kyler Murray", "ARI", 3800, 22, 10, 560, 5, 4),
        _qb("Bo Nix", "DEN", 3700, 24, 11, 430, 4, 5),
        _qb("Baker Mayfield", "TB", 4000, 28, 13, 200, 3, 4),
        _qb("Trevor Lawrence", "JAX", 3900, 23, 12, 320, 3, 5),
        _qb("Drake Maye", "NE", 3700, 21, 12, 380, 3, 5),
        _qb("J.J. McCarthy", "MIN", 3500, 22, 12, 250, 3, 6),
        _qb("Michael Penix Jr.", "ATL", 3600, 21, 13, 150, 2, 5),
        _qb("Anthony Richardson", "IND", 3300, 19, 13, 620, 6, 7),
    ]

    # --- Running backs ----------------------------------------------------
    players += [
        _rb("Bijan Robinson", "ATL", 1350, 11, 62, 520, 3, 2),
        _rb("Saquon Barkley", "PHI", 1500, 13, 38, 290, 2, 2),
        _rb("Jahmyr Gibbs", "DET", 1100, 12, 58, 480, 4, 1),
        _rb("De'Von Achane", "MIA", 980, 8, 70, 600, 4, 2),
        _rb("Christian McCaffrey", "SF", 1150, 10, 55, 440, 3, 2),
        _rb("Ashton Jeanty", "LV", 1250, 11, 40, 300, 1, 3),
        _rb("Jonathan Taylor", "IND", 1300, 12, 30, 220, 1, 2),
        _rb("Derrick Henry", "BAL", 1450, 14, 18, 150, 1, 2),
        _rb("Josh Jacobs", "GB", 1150, 11, 42, 320, 2, 2),
        _rb("Kyren Williams", "LAR", 1180, 13, 35, 250, 1, 3),
        _rb("Bucky Irving", "TB", 1050, 8, 52, 420, 2, 2),
        _rb("Chase Brown", "CIN", 1000, 9, 48, 360, 2, 2),
        _rb("Kenneth Walker III", "SEA", 1020, 9, 40, 300, 1, 3),
        _rb("James Cook", "BUF", 1000, 11, 36, 280, 1, 2),
        _rb("Breece Hall", "NYJ", 970, 7, 50, 430, 2, 3),
        _rb("Alvin Kamara", "NO", 900, 6, 60, 520, 3, 2),
        _rb("Joe Mixon", "HOU", 1050, 10, 38, 300, 1, 2),
        _rb("Chuba Hubbard", "CAR", 980, 8, 40, 290, 1, 3),
        _rb("James Conner", "ARI", 1000, 9, 36, 280, 1, 2),
        _rb("David Montgomery", "DET", 850, 11, 28, 220, 1, 1),
        _rb("Aaron Jones", "MIN", 900, 6, 44, 360, 2, 2),
        _rb("Tony Pollard", "TEN", 950, 7, 40, 300, 1, 3),
        _rb("RJ Harvey", "DEN", 820, 7, 38, 290, 2, 2),
        _rb("Brian Robinson Jr.", "WAS", 880, 8, 24, 180, 1, 2),
        _rb("Rhamondre Stevenson", "NE", 800, 6, 34, 250, 1, 3),
        _rb("Najee Harris", "LAC", 900, 7, 30, 220, 1, 2),
        _rb("D'Andre Swift", "CHI", 870, 5, 42, 320, 1, 3),
        _rb("Isiah Pacheco", "KC", 850, 7, 30, 230, 1, 2),
        _rb("Jordan Mason", "MIN", 780, 6, 20, 150, 1, 2),
        _rb("Tyrone Tracy Jr.", "NYG", 760, 5, 36, 280, 1, 3),
        _rb("Jaylen Warren", "PIT", 720, 4, 38, 300, 1, 2),
        _rb("Travis Etienne Jr.", "JAX", 780, 5, 30, 220, 1, 3),
        _rb("Rachaad White", "TB", 640, 4, 44, 350, 1, 2),
        _rb("Zach Charbonnet", "SEA", 700, 6, 26, 200, 1, 2),
        _rb("Tyjae Spears", "TEN", 680, 4, 34, 280, 1, 2),
        _rb("Javonte Williams", "DAL", 700, 5, 28, 200, 1, 3),
        _rb("Austin Ekeler", "WAS", 520, 3, 40, 320, 2, 2),
        _rb("Trey Benson", "ARI", 650, 5, 18, 140, 1, 2),
        _rb("Jerome Ford", "CLE", 600, 4, 30, 240, 1, 2),
        _rb("Jaylen Wright", "MIA", 620, 4, 16, 130, 0, 2),
        _rb("Tank Bigsby", "JAX", 620, 5, 12, 90, 0, 2),
        _rb("Roschon Johnson", "CHI", 540, 5, 20, 150, 1, 2),
        _rb("Zamir White", "LV", 560, 3, 16, 120, 0, 3),
        _rb("Devin Singletary", "NYG", 580, 3, 22, 160, 0, 2),
    ]

    # --- Wide receivers ---------------------------------------------------
    players += [
        _wr("Ja'Marr Chase", "CIN", 115, 1550, 13, 30, 0, 1),
        _wr("Justin Jefferson", "MIN", 105, 1500, 10, 0, 0, 1),
        _wr("CeeDee Lamb", "DAL", 108, 1450, 9, 40, 0, 1),
        _wr("Amon-Ra St. Brown", "DET", 112, 1300, 11, 0, 0, 1),
        _wr("Puka Nacua", "LAR", 100, 1400, 8, 60, 0, 1),
        _wr("Malik Nabers", "NYG", 105, 1320, 8, 0, 0, 2),
        _wr("Nico Collins", "HOU", 88, 1280, 9, 0, 0, 1),
        _wr("Brian Thomas Jr.", "JAX", 92, 1250, 10, 20, 0, 1),
        _wr("Drake London", "ATL", 95, 1230, 9, 0, 0, 1),
        _wr("A.J. Brown", "PHI", 85, 1200, 9, 0, 0, 1),
        _wr("Tyreek Hill", "MIA", 90, 1180, 7, 40, 0, 1),
        _wr("Garrett Wilson", "NYJ", 96, 1150, 7, 0, 0, 1),
        _wr("Ladd McConkey", "LAC", 88, 1120, 7, 30, 0, 1),
        _wr("Davante Adams", "LAR", 84, 1100, 9, 0, 0, 1),
        _wr("Jaxon Smith-Njigba", "SEA", 90, 1130, 6, 0, 0, 1),
        _wr("Mike Evans", "TB", 76, 1080, 11, 0, 0, 1),
        _wr("Terry McLaurin", "WAS", 78, 1050, 10, 0, 0, 1),
        _wr("DK Metcalf", "PIT", 74, 1040, 8, 0, 0, 1),
        _wr("Marvin Harrison Jr.", "ARI", 82, 1100, 8, 0, 0, 1),
        _wr("DeVonta Smith", "PHI", 80, 1000, 6, 0, 0, 1),
        _wr("Rashee Rice", "KC", 90, 1020, 7, 0, 0, 1),
        _wr("Jaylen Waddle", "MIA", 78, 980, 6, 20, 0, 1),
        _wr("DJ Moore", "CHI", 82, 990, 6, 30, 0, 2),
        _wr("Zay Flowers", "BAL", 80, 960, 5, 40, 0, 1),
        _wr("Courtland Sutton", "DEN", 76, 980, 8, 0, 0, 1),
        _wr("Tee Higgins", "CIN", 72, 950, 8, 0, 0, 1),
        _wr("George Pickens", "DAL", 70, 1000, 6, 0, 0, 2),
        _wr("Jordan Addison", "MIN", 72, 920, 7, 0, 0, 1),
        _wr("Jameson Williams", "DET", 64, 940, 6, 80, 1, 1),
        _wr("Chris Olave", "NO", 78, 900, 5, 0, 0, 1),
        _wr("Jerry Jeudy", "CLE", 74, 920, 4, 0, 0, 1),
        _wr("Xavier Worthy", "KC", 72, 860, 6, 120, 1, 1),
        _wr("Rome Odunze", "CHI", 68, 900, 6, 0, 0, 1),
        _wr("Cooper Kupp", "SEA", 76, 880, 5, 0, 0, 1),
        _wr("Calvin Ridley", "TEN", 70, 880, 5, 0, 0, 1),
        _wr("Jakobi Meyers", "LV", 78, 860, 4, 0, 0, 1),
        _wr("Jayden Reed", "GB", 64, 820, 6, 80, 1, 1),
        _wr("Keon Coleman", "BUF", 60, 820, 6, 0, 0, 1),
        _wr("Khalil Shakir", "BUF", 72, 800, 4, 0, 0, 1),
        _wr("Diontae Johnson", "CAR", 70, 800, 5, 0, 0, 1),
        _wr("Christian Kirk", "JAX", 66, 780, 4, 0, 0, 1),
        _wr("Tank Dell", "HOU", 62, 760, 5, 0, 0, 1),
        _wr("Josh Downs", "IND", 68, 760, 4, 0, 0, 1),
        _wr("Rashod Bateman", "BAL", 56, 740, 6, 0, 0, 1),
        _wr("Wan'Dale Robinson", "NYG", 74, 700, 3, 0, 0, 1),
    ]

    # --- Tight ends -------------------------------------------------------
    players += [
        _te("Brock Bowers", "LV", 95, 1100, 7, 1),
        _te("Trey McBride", "ARI", 92, 1000, 6, 1),
        _te("George Kittle", "SF", 70, 920, 8, 1),
        _te("Sam LaPorta", "DET", 78, 880, 7, 1),
        _te("Travis Kelce", "KC", 80, 850, 5, 1),
        _te("Mark Andrews", "BAL", 60, 720, 8, 1),
        _te("T.J. Hockenson", "MIN", 68, 760, 5, 1),
        _te("David Njoku", "CLE", 64, 740, 5, 1),
        _te("Dalton Kincaid", "BUF", 62, 700, 5, 1),
        _te("Evan Engram", "DEN", 70, 680, 4, 1),
        _te("Jonnu Smith", "MIA", 58, 640, 5, 1),
        _te("Tucker Kraft", "GB", 54, 620, 6, 1),
        _te("Colston Loveland", "CHI", 56, 600, 4, 1),
        _te("Dallas Goedert", "PHI", 52, 580, 4, 1),
        _te("Jake Ferguson", "DAL", 60, 600, 4, 1),
        _te("Kyle Pitts", "ATL", 56, 640, 4, 1),
        _te("Hunter Henry", "NE", 56, 580, 3, 1),
        _te("Pat Freiermuth", "PIT", 52, 560, 4, 1),
        _te("Cade Otton", "TB", 54, 560, 3, 1),
        _te("Cole Kmet", "CHI", 50, 540, 4, 1),
    ]

    # --- Kickers ----------------------------------------------------------
    players += [
        _k("Brandon Aubrey", "DAL", 1, 6, 12, 10, 7, 42, 3, 1),
        _k("Cameron Dicker", "LAC", 1, 7, 11, 9, 5, 44, 2, 0),
        _k("Jake Bates", "DET", 0, 6, 10, 9, 6, 46, 3, 1),
        _k("Chris Boswell", "PIT", 1, 6, 11, 8, 6, 38, 2, 0),
        _k("Ka'imi Fairbairn", "HOU", 1, 7, 10, 8, 4, 40, 3, 1),
        _k("Tyler Bass", "BUF", 0, 6, 10, 8, 5, 45, 3, 1),
        _k("Harrison Butker", "KC", 1, 6, 9, 8, 5, 43, 2, 1),
        _k("Jason Sanders", "MIA", 1, 6, 10, 7, 4, 38, 3, 0),
        _k("Younghoe Koo", "ATL", 0, 6, 9, 8, 4, 40, 3, 1),
        _k("Wil Lutz", "DEN", 1, 6, 9, 7, 4, 42, 2, 1),
        _k("Matt Gay", "WAS", 1, 5, 9, 7, 4, 39, 3, 1),
        _k("Evan McPherson", "CIN", 0, 6, 8, 7, 5, 41, 3, 1),
        _k("Jake Elliott", "PHI", 1, 5, 9, 7, 4, 40, 3, 1),
        _k("Cairo Santos", "CHI", 0, 6, 9, 6, 3, 38, 3, 1),
        _k("Chase McLaughlin", "TB", 1, 6, 8, 6, 4, 39, 2, 0),
        _k("Will Reichard", "MIN", 1, 5, 8, 7, 3, 37, 2, 1),
        _k("Graham Gano", "NYG", 0, 5, 8, 6, 3, 35, 3, 1),
        _k("Nick Folk", "NYJ", 0, 5, 9, 6, 2, 36, 3, 1),
    ]

    # --- Team defenses ----------------------------------------------------
    # pa_total = season points allowed (per-game avg = total / 17).
    players += [
        _dst("Eagles", "PHI", 306, 48, 16, 9, 4, 1, 5),
        _dst("Broncos", "DEN", 316, 52, 15, 8, 3, 1, 4),
        _dst("Ravens", "BAL", 322, 44, 17, 10, 4, 0, 3),
        _dst("Texans", "HOU", 330, 46, 14, 8, 3, 1, 4),
        _dst("Vikings", "MIN", 334, 50, 15, 7, 3, 0, 3),
        _dst("Steelers", "PIT", 340, 45, 14, 9, 4, 1, 3),
        _dst("Bills", "BUF", 344, 42, 16, 8, 3, 0, 2),
        _dst("Chargers", "LAC", 348, 40, 13, 8, 2, 1, 3),
        _dst("Lions", "DET", 360, 41, 13, 7, 3, 0, 2),
        _dst("Packers", "GB", 364, 39, 14, 7, 2, 1, 2),
        _dst("Chiefs", "KC", 356, 38, 13, 8, 2, 0, 2),
        _dst("Seahawks", "SEA", 372, 40, 12, 6, 2, 1, 2),
        _dst("49ers", "SF", 366, 42, 13, 8, 3, 1, 3),
        _dst("Jets", "NYJ", 372, 41, 14, 7, 2, 0, 3),
        _dst("Browns", "CLE", 376, 43, 12, 7, 2, 1, 2),
        _dst("Cowboys", "DAL", 380, 44, 14, 7, 3, 0, 2),
        _dst("Buccaneers", "TB", 384, 40, 12, 8, 2, 1, 2),
        _dst("Colts", "IND", 392, 38, 13, 8, 2, 0, 2),
    ]

    return players


SAMPLE_PROJECTIONS: List[Projection] = _build_sample()


def sample_projections() -> List[Projection]:
    """A fresh copy of the bundled SAMPLE projections."""
    return [
        Projection(p.player_id, p.name, p.position, p.team, dict(p.stats), p.source)
        for p in SAMPLE_PROJECTIONS
    ]


# ---------------------------------------------------------------------------
# Snapshots — committed raw payloads (the robust path for a personal tool)
# ---------------------------------------------------------------------------
# Instead of hitting ESPN/Sleeper live (cloud IPs get 403'd, browsers hit
# CORS), fetch once and commit the raw JSON to ``data/``. The app then parses
# those files with the *same* verified parsers, fully offline. Refresh them
# with ``tools/fetch_snapshot.py`` or the "Refresh projections snapshot" GitHub
# Action.
SNAPSHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def snapshot_paths(season: int, data_dir: Optional[str] = None) -> Tuple[str, str]:
    d = data_dir or SNAPSHOT_DIR
    return (os.path.join(d, f"espn_{season}.json"),
            os.path.join(d, f"sleeper_{season}.json"))


def load_espn_snapshot(
    path: str, season: Optional[int] = None
) -> Tuple[List[Projection], Set[str]]:
    """Parse a saved raw ESPN payload (as written by fetch_snapshot)."""
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    return parse_espn_players(payload, season=season, source="snapshot")


def load_sleeper_snapshot(path: str) -> Tuple[List[Projection], Set[str]]:
    """Parse a saved Sleeper snapshot ({"weeks": [<week payloads>]})."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    weeks = data["weeks"] if isinstance(data, dict) and "weeks" in data else data
    return aggregate_sleeper_weeks(weeks, source="snapshot")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def _counts(projections: List[Projection]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for p in projections:
        out[p.position] = out.get(p.position, 0) + 1
    return out


def get_projections(
    source: str = "sample",
    *,
    season: int = 2026,
    weeks: Iterable[int] = range(1, 18),
    allow_fallback: bool = True,
    data_dir: Optional[str] = None,
) -> Tuple[List[Projection], Diagnostics]:
    """Load projections from ``source`` with graceful fallback to SAMPLE.

    ``source`` is one of ``snapshot`` / ``sleeper`` / ``espn`` / ``blend`` /
    ``sample``. ``snapshot`` reads committed ``data/`` files (the robust,
    offline path); the live sources hit the APIs. Failures or empty responses
    fall back to SAMPLE (when ``allow_fallback``) and are recorded in the
    returned ``Diagnostics``.
    """
    diag = Diagnostics(source_requested=source)
    source = (source or "sample").lower()
    weeks = list(weeks)
    projections: List[Projection] = []

    def _load_sleeper() -> List[Projection]:
        proj, unmapped = aggregate_sleeper_weeks(fetch_sleeper_season(season, weeks))
        diag.add_unmapped("sleeper", unmapped)
        return proj

    def _load_espn() -> List[Projection]:
        proj, unmapped = parse_espn_players(fetch_espn(season), season=season)
        diag.add_unmapped("espn", unmapped)
        return proj

    def _try(name: str, fn) -> List[Projection]:
        """Run one source, capturing its outcome independently of the others."""
        try:
            proj = fn()
        except Exception as exc:  # pragma: no cover - network/parse failures
            diag.errors.append(f"{name}: {type(exc).__name__}: {exc}")
            diag.source_counts[name] = 0
            return []
        diag.source_counts[name] = len(proj)
        if not proj:
            diag.notes.append(f"{name} returned 0 players.")
        return proj

    if source == "sample":
        projections = sample_projections()
        diag.source_used = "sample"

    elif source == "snapshot":
        espn_path, sleeper_path = snapshot_paths(season, data_dir)
        espn_proj = []
        sleeper_proj = []

        def _snap_espn() -> List[Projection]:
            proj, unmapped = load_espn_snapshot(espn_path, season)
            diag.add_unmapped("espn", unmapped)
            return proj

        def _snap_sleeper() -> List[Projection]:
            proj, unmapped = load_sleeper_snapshot(sleeper_path)
            diag.add_unmapped("sleeper", unmapped)
            return proj

        if os.path.exists(espn_path):
            espn_proj = _try("espn", _snap_espn)
        else:
            diag.notes.append(f"no ESPN snapshot (expected data/espn_{season}.json)")
        if os.path.exists(sleeper_path):
            sleeper_proj = _try("sleeper", _snap_sleeper)
        else:
            diag.notes.append(
                f"no Sleeper snapshot (expected data/sleeper_{season}.json)")

        projections = blend_projections(espn_proj, sleeper_proj)
        if projections:
            diag.source_used = describe_blend(espn_proj, sleeper_proj).replace(
                "blend ", "snapshot ")

    elif source == "sleeper":
        projections = _try("sleeper", _load_sleeper)
        if projections:
            diag.source_used = "sleeper"

    elif source == "espn":
        projections = _try("espn", _load_espn)
        if projections:
            diag.source_used = "espn"

    elif source == "blend":
        # Fetch both independently so one failing never discards the other.
        espn_proj = _try("espn", _load_espn)
        sleeper_proj = _try("sleeper", _load_sleeper)
        projections = blend_projections(espn_proj, sleeper_proj)
        if projections:
            diag.source_used = describe_blend(espn_proj, sleeper_proj)

    else:
        diag.errors.append(f"unknown source '{source}'")

    if not projections and allow_fallback and source != "sample":
        diag.notes.append(
            f"'{source}' returned no usable data; falling back to SAMPLE."
        )
        projections = sample_projections()
        diag.source_used = "sample (fallback)"

    diag.counts = _counts(projections)
    return projections, diag
