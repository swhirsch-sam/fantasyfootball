"""Aggregate the five sources into one ranked draft board.

Pipeline (brief §6–§8):

1. Score every source's row through the one shared formula (offense), or take
   the site FPTS (K/DEF).
2. Pivot wide — one column of points per source — and take the **median** per
   player (robust to a single source's outlier model).
3. Keep ``n_sources`` (non-null source count) as a confidence flag.
4. VORP via the iterative lineup allocation, then within-position tiers.
5. Rank the board by VORP and left-join FFC ADP as a market cross-check
   (``value_vs_adp = market_adp - overall_rank``).

Everything here is pure (DataFrame in -> DataFrame out) so the whole board is
reproducible offline from the bundled sample, with no scraping required.
"""

from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd

from league2.scoring import (
    LEAGUE_CONFIG,
    RAW_STAT_KEYS,
    SPECIAL_POSITIONS,
    normalize_name,
    score_player,
)
from league2.vorp import assign_tiers, compute_vorp

__all__ = [
    "score_long_frame",
    "aggregate_sources",
    "attach_adp",
    "build_board",
    "BOARD_COLUMNS",
]

# Columns surfaced in the committed CSV / Streamlit page, in display order.
BOARD_COLUMNS = [
    "overall_rank", "name", "pos", "pos_rank", "team", "tier",
    "agg_points", "n_sources", "vorp", "replacement_pts", "is_starter",
    "market_adp", "value_vs_adp",
]


def score_long_frame(long_df: pd.DataFrame) -> pd.DataFrame:
    """Add a ``points`` column to a long frame of per-source rows.

    Offense is scored from raw stats through the shared formula.  K/DEF (and any
    offensive row that exposed no raw stats) fall back to the site ``fpts``.
    """
    df = long_df.copy()
    for k in RAW_STAT_KEYS:
        if k not in df.columns:
            df[k] = 0.0
    df[list(RAW_STAT_KEYS)] = df[list(RAW_STAT_KEYS)].apply(
        pd.to_numeric, errors="coerce"
    ).fillna(0.0)
    if "fpts" not in df.columns:
        df["fpts"] = np.nan
    fpts = pd.to_numeric(df["fpts"], errors="coerce")

    raw_total = df[list(RAW_STAT_KEYS)].abs().sum(axis=1)
    offense_scores = df[list(RAW_STAT_KEYS)].apply(
        lambda r: score_player(r.to_dict()), axis=1
    )

    is_special = df["pos"].isin(SPECIAL_POSITIONS)
    has_raw = raw_total > 0
    points = offense_scores.where(has_raw & ~is_special)
    # K/DEF, or any row with no raw stats, use the site fantasy points.
    points = points.where(points.notna(), fpts)
    df["points"] = points.fillna(0.0).round(2)
    return df


def _mode_str(series: pd.Series) -> str:
    """Most common non-empty string in a series (stable tie-break)."""
    vals = [str(v).strip() for v in series if str(v).strip()]
    if not vals:
        return ""
    counts: Dict[str, int] = {}
    for v in vals:
        counts[v] = counts.get(v, 0) + 1
    return max(vals, key=lambda v: (counts[v], -vals.index(v)))


def aggregate_sources(scored_long: pd.DataFrame) -> pd.DataFrame:
    """Pivot per-source points wide and take the median per player.

    The join key is ``(normalize_name(name), pos)``.  Returns one row per player
    with ``agg_points`` (median across sources), ``n_sources`` (confidence), the
    per-source ``pts_<source>`` columns, and a display name/team.
    """
    df = scored_long.copy()
    df["key"] = df["name"].map(normalize_name)

    wide = df.pivot_table(
        index=["key", "pos"], columns="source", values="points", aggfunc="median"
    )
    src_cols = [f"pts_{c}" for c in wide.columns]
    wide.columns = src_cols
    wide["agg_points"] = wide[src_cols].median(axis=1, skipna=True).round(2)
    wide["n_sources"] = wide[src_cols].notna().sum(axis=1).astype(int)

    meta = (
        df.groupby(["key", "pos"])
        .agg(name=("name", _mode_str), team=("team", _mode_str))
        .reset_index()
    )
    board = wide.reset_index().merge(meta, on=["key", "pos"], how="left")
    return board


def attach_adp(board: pd.DataFrame, adp_df: pd.DataFrame) -> pd.DataFrame:
    """Left-join FFC market ADP onto the board by ``(normalized name, pos)``."""
    adp = adp_df.copy()
    adp["key"] = adp["name"].map(normalize_name)
    adp = adp.groupby(["key", "pos"], as_index=False)["market_adp"].min()
    out = board.merge(adp, on=["key", "pos"], how="left")
    return out


def build_board(
    long_df: pd.DataFrame,
    adp_df: Optional[pd.DataFrame] = None,
    league_config: Dict = LEAGUE_CONFIG,
) -> pd.DataFrame:
    """Run the full pipeline and return the ranked draft board."""
    scored = score_long_frame(long_df)
    board = aggregate_sources(scored)

    if adp_df is not None and len(adp_df):
        board = attach_adp(board, adp_df)
    if "market_adp" not in board.columns:
        board["market_adp"] = np.nan

    board = compute_vorp(board, league_config)
    board = assign_tiers(board)

    board["pos_rank"] = (
        board.groupby("pos")["agg_points"]
        .rank(ascending=False, method="first")
        .astype(int)
    )
    board = board.sort_values(
        ["vorp", "agg_points"], ascending=False
    ).reset_index(drop=True)
    board["overall_rank"] = np.arange(1, len(board) + 1)
    board["value_vs_adp"] = board["market_adp"] - board["overall_rank"]

    # Stable, friendly column order: the headline columns first, then any
    # per-source point columns for transparency.
    src_cols = [c for c in board.columns if c.startswith("pts_")]
    ordered = [c for c in BOARD_COLUMNS if c in board.columns] + sorted(src_cols)
    remaining = [c for c in board.columns if c not in ordered and c != "key"]
    return board[ordered + remaining]
