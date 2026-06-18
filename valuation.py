"""Value-based auction pricing and draft analytics.

Given projected fantasy points, this produces dollar values using value-based
drafting (VBD) and a set of decision metrics for draft day:

* **VORP** — value over replacement (the best player who *isn't* a starter).
* **VOLS** — value over last starter (points above the worst starter).
* **Pos rank / overall rank**, **points/game**, and **value efficiency**
  (VORP per dollar — the bargain signal).
* League-level **budget allocation** and **positional scarcity** summaries.

The dollar pool (``teams * budget``) minus a $1 minimum bid per roster slot is
distributed proportionally to VORP, so assigned dollars sum back to the pool.
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
        return sum(self.starters.values()) + self.bench

    @property
    def total_spots(self) -> int:
        return self.teams * self.roster_size


@dataclass
class ValuedPlayer:
    projection: Projection
    points: float
    games: float = 17.0
    pos_rank: int = 0          # rank within position by points (1 = best)
    overall_rank: int = 0      # rank across all players by $ value
    replacement_pts: float = 0.0
    last_starter_pts: float = 0.0
    vorp: float = 0.0          # points - replacement (first non-starter)
    vols: float = 0.0          # points - last starter
    value: float = 1.0         # auction $
    is_starter: bool = False
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

    @property
    def pos_label(self) -> str:
        """e.g. 'RB3'."""
        return f"{self.position}{self.pos_rank}" if self.pos_rank else self.position

    @property
    def ppg(self) -> float:
        return round(self.points / self.games, 2) if self.games else self.points

    @property
    def vorp_per_dollar(self) -> float:
        """Bargain signal: VORP bought per dollar. Higher = better value."""
        return round(self.vorp / self.value, 2) if self.value > 0 else 0.0


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


def assign_tiers(players: List[ValuedPlayer], gap_factor: float = 1.6) -> None:
    """Group a single position's players into tiers by value drop-offs."""
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

    valued = []
    for p in projections:
        games = float(p.stats.get("games", 17.0)) or 17.0
        valued.append(
            ValuedPlayer(
                projection=p,
                points=scoring.score(p.stats, p.position),
                games=games,
            )
        )

    by_pos: Dict[str, List[ValuedPlayer]] = {}
    for vp in valued:
        by_pos.setdefault(vp.position, []).append(vp)
    for players in by_pos.values():
        players.sort(key=lambda vp: vp.points, reverse=True)
        for i, vp in enumerate(players, start=1):
            vp.pos_rank = i

    counts = _starter_counts(by_pos, settings)
    for pos, players in by_pos.items():
        n = counts.get(pos, 0)
        if not players:
            continue
        repl = players[n].points if n < len(players) else players[-1].points
        last_starter = players[min(max(n - 1, 0), len(players) - 1)].points
        for vp in players:
            vp.replacement_pts = repl
            vp.last_starter_pts = last_starter
            vp.vorp = round(vp.points - repl, 2)
            vp.vols = round(vp.points - last_starter, 2)
            vp.is_starter = vp.pos_rank <= n

    # Only the top ``total_spots`` players by VORP get more than the $1 min.
    ranked = sorted(valued, key=lambda vp: vp.vorp, reverse=True)
    draftable = ranked[: settings.total_spots]
    draftable_ids = {id(vp) for vp in draftable}

    total_pool = settings.teams * settings.budget
    marginal_pool = total_pool - len(draftable)
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
    for i, vp in enumerate(valued, start=1):
        vp.overall_rank = i
    return valued


# ---------------------------------------------------------------------------
# League-level analysis
# ---------------------------------------------------------------------------
@dataclass
class PositionSummary:
    position: str
    players: int
    starters: int
    tier1: int
    best_value: float
    replacement_pts: float
    last_starter_pts: float
    starter_cliff: float       # last starter - replacement (scarcity at the edge)
    total_value: float         # sum of positive $ across the position
    per_team_spend: float      # total_value / teams
    budget_share: float        # per_team_spend / budget (fraction)


def positional_summary(
    valued: List[ValuedPlayer], settings: Optional[LeagueSettings] = None
) -> List[PositionSummary]:
    """Per-position scarcity + recommended budget allocation."""
    settings = settings or LeagueSettings()
    by_pos: Dict[str, List[ValuedPlayer]] = {}
    for vp in valued:
        by_pos.setdefault(vp.position, []).append(vp)

    counts = _starter_counts(
        {p: sorted(v, key=lambda x: x.points, reverse=True) for p, v in by_pos.items()},
        settings,
    )

    out: List[PositionSummary] = []
    for pos, players in by_pos.items():
        players = sorted(players, key=lambda vp: vp.value, reverse=True)
        total_value = round(sum(vp.value for vp in players if vp.value > 0), 1)
        per_team = round(total_value / settings.teams, 1) if settings.teams else 0.0
        repl = players[0].replacement_pts if players else 0.0
        last = players[0].last_starter_pts if players else 0.0
        out.append(
            PositionSummary(
                position=pos,
                players=len(players),
                starters=counts.get(pos, 0),
                tier1=sum(1 for vp in players if vp.tier == 1),
                best_value=players[0].value if players else 0.0,
                replacement_pts=round(repl, 1),
                last_starter_pts=round(last, 1),
                starter_cliff=round(last - repl, 1),
                total_value=total_value,
                per_team_spend=per_team,
                budget_share=round(per_team / settings.budget, 3)
                if settings.budget else 0.0,
            )
        )
    order = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "K": 4, "DST": 5}
    out.sort(key=lambda s: order.get(s.position, 9))
    return out
