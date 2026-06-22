"""Replacement-level / VORP math for the exact League #2 lineup.

A flat per-position replacement rank (``RB17`` etc.) does not work here because
the FLEX pool is shared across RB/WR/TE and there is **no dedicated TE slot at
all**.  Instead we allocate starters iteratively:

1. Lock in every dedicated starter (QB/RB/WR/K/DEF) league-wide.
2. Fill the shared FLEX slots greedily from the best leftover RB/WR/TE.
3. Replacement level per position = the best player *still* unrostered.

That falls out naturally into the two effects this league is defined by,
without hardcoding either:

* **QB2s have real value** — 16 QB starters (2 × 8) push QB replacement far
  deeper than a 1-QB league.
* **Only elite TEs matter** — TEs reach the lineup only by beating RB/WR for
  FLEX, so TE replacement level is very low.
"""

from __future__ import annotations

from typing import Dict

import pandas as pd

from league2.scoring import LEAGUE_CONFIG, ROSTER_POSITIONS

__all__ = ["compute_vorp", "assign_tiers", "replacement_levels"]


def replacement_levels(
    players_df: pd.DataFrame, league_config: Dict = LEAGUE_CONFIG
) -> Dict[str, float]:
    """Replacement points per position under the iterative allocation.

    ``players_df`` needs ``pos`` and ``agg_points`` columns.  Returns a dict of
    ``{position: replacement_points}`` and is the single source of truth that
    :func:`compute_vorp` consumes — exposed separately so the analysis tab can
    show where each position's baseline lands.
    """
    teams = league_config["teams"]
    roster = league_config["roster"]
    flex_positions = league_config["flex_eligible"]

    # Dedicated starters first (no TE entry -> TEs only reach the lineup via FLEX).
    dedicated_starters = {
        "QB": roster["QB"] * teams,
        "RB": roster["RB"] * teams,
        "WR": roster["WR"] * teams,
        "K": roster["K"] * teams,
        "DEF": roster["DEF"] * teams,
    }
    flex_slots_total = roster["FLEX"] * teams

    df = players_df  # caller passes a clean RangeIndex frame
    locked_ids = set()
    for pos, n in dedicated_starters.items():
        top_n = df[df.pos == pos].nlargest(n, "agg_points")
        locked_ids.update(top_n.index)

    # Remaining flex-eligible pool fills the shared FLEX slots greedily.
    flex_candidates = df[
        df.pos.isin(flex_positions) & ~df.index.isin(locked_ids)
    ].sort_values("agg_points", ascending=False)
    locked_ids.update(flex_candidates.head(flex_slots_total).index)

    replacement: Dict[str, float] = {}
    for pos in ROSTER_POSITIONS:
        remaining = df[(df.pos == pos) & ~df.index.isin(locked_ids)]
        if remaining.empty:
            replacement[pos] = 0.0
        else:
            top = remaining["agg_points"].max()
            replacement[pos] = float(top) if pd.notna(top) else 0.0
    return replacement, locked_ids


def compute_vorp(
    players_df: pd.DataFrame, league_config: Dict = LEAGUE_CONFIG
) -> pd.DataFrame:
    """Add ``vorp`` / ``replacement_pts`` / ``is_starter`` columns.

    ``players_df`` is one row per rosterable player with ``pos`` and
    ``agg_points``.  Returns a copy sorted by VORP (best first); the caller does
    not need a particular index.
    """
    df = players_df.copy().reset_index(drop=True)
    replacement, locked_ids = replacement_levels(df, league_config)

    df["replacement_pts"] = df["pos"].map(lambda p: replacement.get(p, 0.0)).round(2)
    df["vorp"] = (df["agg_points"] - df["replacement_pts"]).round(2)
    df["is_starter"] = df.index.isin(locked_ids)
    return df.sort_values("vorp", ascending=False).reset_index(drop=True)


def assign_tiers(board: pd.DataFrame, gap_factor: float = 1.6) -> pd.DataFrame:
    """Tag each player with a within-position ``tier`` from VORP drop-offs.

    A new tier starts wherever the VORP gap to the next player exceeds
    ``gap_factor`` × the position's average gap — i.e. a draft-day *cliff*.
    """
    df = board.copy()
    df["tier"] = 1
    for pos, sub in df.groupby("pos"):
        ordered = sub.sort_values("vorp", ascending=False)
        idx = ordered.index.tolist()
        vals = ordered["vorp"].tolist()
        gaps = [vals[i - 1] - vals[i] for i in range(1, len(vals))]
        avg_gap = sum(gaps) / len(gaps) if gaps else 0.0
        tier = 1
        df.loc[idx[0], "tier"] = 1
        for i in range(1, len(vals)):
            if avg_gap > 0 and (vals[i - 1] - vals[i]) > gap_factor * avg_gap:
                tier += 1
            df.loc[idx[i], "tier"] = tier
    return df
