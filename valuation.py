"""Value-based auction pricing.

Given projected fantasy points, this module produces dollar values using the
standard value-based-drafting (VBD) approach:

1. Score every player with the league's scoring rules.
2. Work out how many players at each position are *starters* across the whole
   league (including the FLEX, which is filled by the best leftover RB/WR/TE).
3. The "replacement level" for a position is the best player who is *not* a
   starter. A player's value over replacement (VORP) is points above that.
4. Convert the entire league's marginal VORP into dollars: the auction pool is
   ``teams * budget`` minus the $1 minimum bid reserved for every roster slot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from data_sources import Projection
from scoring import Scoring


DEFAULT_STARTERS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1}


@dataclass
class LeagueSettings:
    teams: int = 12
    budget: int = 200
    starters: Dict[str, int] = field(default_factory=lambda: dict(DEFAULT_STARTERS))
    bench: int = 6
    flex_positions: Tuple[str, ...] = ("RB", "WR", "TE")

    @property
    def roster_size(self) -> int:
        """Total roster spots per team (starters incl. FLEX, plus bench)."""
        return sum(self.starters.values()) + self.bench

    @property
    def total_spots(self) -> int:
        return self.teams * self.roster_size


@dataclass
class ValuedPlayer:
    projection: Projection
    points: float
    vorp: float = 0.0
    value: float = 1.0
    tier: int = 1

    @property
    def name(self) -> str:
        return self.projection.name

    @property
    def position(self) -> str:
        return self.projection.position

    @property
    def team(self) -> str:
        return self.projection.team


def _starter_counts(
    by_pos: Dict[str, List[ValuedPlayer]], settings: LeagueSettings
) -> Dict[str, int]:
    """How many players at each position start, FLEX distributed by points."""
    counts: Dict[str, int] = {}
    for pos, per_team in settings.starters.items():
        if pos == "FLEX":
            continue
        counts[pos] = settings.teams * per_team

    flex_slots = settings.teams * settings.starters.get("FLEX", 0)
    if flex_slots:
        pool: List[ValuedPlayer] = []
        for pos in settings.flex_positions:
            base = counts.get(pos, 0)
            pool.extend(by_pos.get(pos, [])[base:])
        pool.sort(key=lambda vp: vp.points, reverse=True)
        for vp in pool[:flex_slots]:
            counts[vp.position] = counts.get(vp.position, 0) + 1
    return counts


def _replacement_levels(
    by_pos: Dict[str, List[ValuedPlayer]], counts: Dict[str, int]
) -> Dict[str, float]:
    """Replacement = points of the best non-starter at each position."""
    levels: Dict[str, float] = {}
    for pos, players in by_pos.items():
        n = counts.get(pos, 0)
        if not players:
            levels[pos] = 0.0
        elif n < len(players):
            levels[pos] = players[n].points  # first non-starter
        else:
            levels[pos] = players[-1].points  # everyone starts; use the last
    return levels


def assign_tiers(players: List[ValuedPlayer], gap_factor: float = 1.6) -> None:
    """Group a single position's players into tiers by value drop-offs.

    A new tier starts whenever the value gap to the previous player is more
    than ``gap_factor`` times the average gap — i.e. at the natural cliffs.
    """
    ranked = sorted(players, key=lambda vp: vp.value, reverse=True)
    if not ranked:
        return
    gaps = [ranked[i - 1].value - ranked[i].value for i in range(1, len(ranked))]
    avg_gap = sum(gaps) / len(gaps) if gaps else 0.0
    tier = 1
    ranked[0].tier = 1
    for i in range(1, len(ranked)):
        gap = ranked[i - 1].value - ranked[i].value
        if avg_gap > 0 and gap > gap_factor * avg_gap:
            tier += 1
        ranked[i].tier = tier


def compute_values(
    projections: List[Projection],
    scoring: Optional[Scoring] = None,
    settings: Optional[LeagueSettings] = None,
) -> List[ValuedPlayer]:
    """Score, rank, and price every projection. Returns players sorted by $."""
    scoring = scoring or Scoring()
    settings = settings or LeagueSettings()

    valued = [
        ValuedPlayer(projection=p, points=scoring.score(p.stats, p.position))
        for p in projections
    ]

    by_pos: Dict[str, List[ValuedPlayer]] = {}
    for vp in valued:
        by_pos.setdefault(vp.position, []).append(vp)
    for players in by_pos.values():
        players.sort(key=lambda vp: vp.points, reverse=True)

    counts = _starter_counts(by_pos, settings)
    replacement = _replacement_levels(by_pos, counts)

    for vp in valued:
        vp.vorp = round(vp.points - replacement.get(vp.position, 0.0), 2)

    # Only the top ``total_spots`` players by VORP get more than the $1 min.
    ranked = sorted(valued, key=lambda vp: vp.vorp, reverse=True)
    draftable = ranked[: settings.total_spots]
    draftable_ids = {id(vp) for vp in draftable}

    total_pool = settings.teams * settings.budget
    marginal_pool = total_pool - len(draftable)  # reserve $1 per drafted slot
    total_vorp = sum(vp.vorp for vp in draftable if vp.vorp > 0)
    dollars_per_vorp = marginal_pool / total_vorp if total_vorp > 0 else 0.0

    for vp in valued:
        if id(vp) in draftable_ids and vp.vorp > 0:
            vp.value = round(1.0 + vp.vorp * dollars_per_vorp, 1)
        elif id(vp) in draftable_ids:
            vp.value = 1.0
        else:
            vp.value = 0.0

    for players in by_pos.values():
        assign_tiers(players)

    valued.sort(key=lambda vp: (vp.value, vp.points), reverse=True)
    return valued
