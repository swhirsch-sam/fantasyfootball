"""Custom league scoring for the dynasty auction tool.

Every projection — whether it comes from ESPN, Sleeper, or the bundled
SAMPLE data — is normalized to a single canonical set of stat keys by
``data_sources``.  Scoring therefore only has to understand those canonical
keys, which keeps the league's rules in exactly one place and makes them
trivial to edit.

Canonical stat keys
-------------------
Passing : ``pass_yd`` ``pass_td`` ``pass_int`` ``pass_2pt``
Rushing : ``rush_yd`` ``rush_td`` ``rush_2pt``
Receiving (PPR) : ``rec`` ``rec_yd`` ``rec_td`` ``rec_2pt``
Turnovers : ``fum_lost``
Kicking : ``fgm_0_19`` ``fgm_20_29`` ``fgm_30_39`` ``fgm_40_49`` ``fgm_50p``
          ``xpm`` ``fgmiss`` ``xpmiss``
Defense/ST : ``pts_allow`` ``games`` ``sack`` ``int`` ``fum_rec`` ``def_td``
             ``safe`` ``blk_kick``
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

# --- Per-stat point values --------------------------------------------------
# Editing these numbers re-prices the entire tool. Defaults below are a
# full-PPR dynasty configuration; ``pts_allow`` is handled separately via the
# tier table because it is scored in bands, not per point.
DEFAULT_SCORING: Dict[str, float] = {
    # Passing
    "pass_yd": 0.04,   # 1 pt per 25 passing yards
    "pass_td": 4.0,
    "pass_int": -2.0,
    "pass_2pt": 2.0,
    # Rushing
    "rush_yd": 0.1,    # 1 pt per 10 rushing yards
    "rush_td": 6.0,
    "rush_2pt": 2.0,
    # Receiving (full PPR)
    "rec": 1.0,
    "rec_yd": 0.1,     # 1 pt per 10 receiving yards
    "rec_td": 6.0,
    "rec_2pt": 2.0,
    # Turnovers
    "fum_lost": -2.0,
    # Kicking — field goals are bucketed by distance
    "fgm_0_19": 3.0,
    "fgm_20_29": 3.0,
    "fgm_30_39": 3.0,
    "fgm_40_49": 4.0,
    "fgm_50p": 5.0,
    "xpm": 1.0,
    "fgmiss": -1.0,
    "xpmiss": -1.0,
    # Team defense / special teams (counting stats)
    "sack": 1.0,
    "int": 2.0,        # defensive interception
    "fum_rec": 2.0,
    "def_td": 6.0,
    "safe": 2.0,
    "blk_kick": 2.0,
}

# Defensive "points allowed" is scored in tiers, per game, rather than per
# point. Each tuple is (low, high_inclusive, points). These are the league's
# custom break points: 0 / 1-6 / 7-13 / 14-20 / 21-27 / 28-34 / 35+.
DEFAULT_DST_PA_TIERS: List[Tuple[int, int, float]] = [
    (0, 0, 10.0),
    (1, 6, 7.0),
    (7, 13, 4.0),
    (14, 20, 1.0),
    (21, 27, 0.0),
    (28, 34, -1.0),
    (35, 999, -4.0),
]

DST_POSITIONS = {"DST", "DEF", "D/ST"}

# Keys that participate in DST points-allowed math but must not be multiplied
# by a per-stat weight in the main loop.
_NON_WEIGHTED = {"pts_allow", "games"}


@dataclass
class Scoring:
    """A league's scoring rules.

    ``weights`` maps canonical stat keys to points. ``dst_pa_tiers`` is the
    per-game points-allowed band table. Copy + tweak to model any league.
    """

    weights: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_SCORING))
    dst_pa_tiers: List[Tuple[int, int, float]] = field(
        default_factory=lambda: list(DEFAULT_DST_PA_TIERS)
    )

    def points_allowed_points(self, points_allowed_per_game: float) -> float:
        """Points awarded for a single game's points-allowed total."""
        pa = int(round(points_allowed_per_game))
        if pa < 0:
            pa = 0
        for low, high, pts in self.dst_pa_tiers:
            if low <= pa <= high:
                return pts
        return self.dst_pa_tiers[-1][2]

    def score(self, stats: Dict[str, float], position: str = "") -> float:
        """Project fantasy points for one player's (season) stat line.

        For defenses, ``pts_allow`` is treated as the season total over
        ``games`` games: it is converted to a per-game average, scored through
        the tier table, then multiplied back up by ``games``.
        """
        total = 0.0
        for key, value in stats.items():
            if key in _NON_WEIGHTED or not value:
                continue
            weight = self.weights.get(key)
            if weight is not None:
                total += value * weight

        if position.upper() in DST_POSITIONS and "pts_allow" in stats:
            games = stats.get("games") or 17
            per_game = stats["pts_allow"] / games if games else stats["pts_allow"]
            total += self.points_allowed_points(per_game) * games

        return round(total, 2)


# A module-level default instance for convenience.
DEFAULT = Scoring()


def score(stats: Dict[str, float], position: str = "") -> float:
    """Score a stat line with the default league configuration."""
    return DEFAULT.score(stats, position)
