"""Realistic offline data so League #2 works with zero scraping.

The five sites 403 datacenter IPs (CI, Streamlit Cloud, this sandbox), so —
exactly like the dynasty tool ships SAMPLE projections — League #2 ships a
committed board built from this synthetic-but-realistic data.  It reuses the
dynasty tool's 2026 player pool, fans each player out across the four stat
sources with small deterministic per-source jitter, and gives stars full
coverage while deep rookies/backups appear in only 1–2 sources, so the
``n_sources`` confidence flag is actually exercised in the demo.

This produces the *long frame* the real scrapers would emit, so the offline
board flows through the identical ``build_board`` pipeline as live data.
"""

from __future__ import annotations

import hashlib
import os
import sys
from typing import List, Tuple

import pandas as pd

# The dynasty tool's sample pool + its scoring (for K/DEF site points) live at
# the repo root; make sure it's importable however this module is loaded.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import data_sources as _ds  # noqa: E402
from scoring import score as _dynasty_score  # noqa: E402

from league2.scoring import (  # noqa: E402
    RAW_STAT_KEYS,
    SPECIAL_POSITIONS,
    normalize_position,
    score_player,
)

# Only the four stat sources contribute points; FFC is ADP-only.
STAT_SOURCES = ["fantasypros", "cbs", "fftoday", "razzball"]

# Dynasty canonical stat keys -> League #2 raw-stat keys.
_KEY_MAP = {
    "pass_yd": "pass_yds", "pass_td": "pass_td", "pass_int": "int",
    "rush_yd": "rush_yds", "rush_td": "rush_td",
    "rec": "rec", "rec_yd": "rec_yds", "rec_td": "rec_td",
    "fum_lost": "fumbles_lost",
}

# How many of the four sources cover the Nth-best player at each position.
# (count_threshold, n_sources) tiers, applied in order.
_COVERAGE = {
    "QB":  [(16, 4), (22, 3), (10 ** 9, 2)],
    "RB":  [(28, 4), (36, 3), (10 ** 9, 2)],
    "WR":  [(34, 4), (42, 3), (10 ** 9, 2)],
    "TE":  [(12, 4), (18, 3), (10 ** 9, 1)],
    "K":   [(12, 4), (10 ** 9, 3)],
    "DEF": [(12, 4), (10 ** 9, 3)],
}


def _hash01(text: str) -> float:
    """Deterministic float in [0, 1) from a string."""
    return int(hashlib.md5(text.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def _jitter(name: str, source: str, spread: float = 0.10) -> float:
    """Reproducible per-(player, source) multiplier in [1-spread, 1+spread)."""
    return 1.0 + (_hash01(f"{name}|{source}") * 2 - 1) * spread


def _coverage_count(pos: str, rank0: int) -> int:
    for threshold, n in _COVERAGE.get(pos, [(10 ** 9, 4)]):
        if rank0 < threshold:
            return n
    return 1


def _sources_for(name: str, n: int) -> List[str]:
    """Deterministically pick which ``n`` of the four sources cover a player."""
    return sorted(STAT_SOURCES, key=lambda s: _hash01(f"{s}|{name}"))[:n]


def _raw_stats(proj) -> dict:
    return {_KEY_MAP[k]: v for k, v in proj.stats.items() if k in _KEY_MAP}


def build_sample_long() -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Return ``(long_df, adp_df)`` mimicking a successful five-source scrape."""
    players = _ds.sample_projections()

    # True (un-jittered) points per player drive both coverage and ADP.
    enriched = []
    for p in players:
        pos = normalize_position(p.position)
        if pos in SPECIAL_POSITIONS:
            true_pts = _dynasty_score(p.stats, p.position)
        else:
            true_pts = score_player(_raw_stats(p))
        enriched.append((p, pos, true_pts))

    # Per-position rank (best -> 0) sets how many sources cover each player.
    by_pos: dict = {}
    for item in enriched:
        by_pos.setdefault(item[1], []).append(item)
    rank0: dict = {}
    for pos, items in by_pos.items():
        for r, (p, _pos, _pts) in enumerate(sorted(items, key=lambda x: -x[2])):
            rank0[p.player_id] = r

    rows: List[dict] = []
    for p, pos, _pts in enriched:
        n = _coverage_count(pos, rank0[p.player_id])
        for src in _sources_for(p.name, n):
            row = {"name": p.name, "pos": pos, "team": p.team, "source": src}
            f = _jitter(p.name, src)
            if pos in SPECIAL_POSITIONS:
                row["fpts"] = round(_dynasty_score(p.stats, p.position) * f, 1)
            else:
                for k, v in _raw_stats(p).items():
                    row[k] = round(v * f, 1)
            rows.append(row)
    long_df = pd.DataFrame(rows, columns=["name", "pos", "team", "source",
                                          *RAW_STAT_KEYS, "fpts"])

    # Market ADP: overall points rank + small deterministic noise, and only for
    # the well-covered (market-known) players — deep guys are missing from ADP.
    overall = sorted(enriched, key=lambda x: -x[2])
    adp_rows = []
    for rank, (p, pos, _pts) in enumerate(overall, start=1):
        if _coverage_count(pos, rank0[p.player_id]) < 3:
            continue  # rookies/backups the market hasn't priced
        noise = round((_hash01(f"adp|{p.name}") * 2 - 1) * 6)
        adp_rows.append({
            "name": p.name, "pos": pos,
            "market_adp": max(1, rank + noise), "source": "ffc_adp",
        })
    adp_df = pd.DataFrame(adp_rows, columns=["name", "pos", "market_adp", "source"])
    return long_df, adp_df


def build_sample_board() -> pd.DataFrame:
    """Convenience: the full ranked board built from the sample data."""
    from league2.aggregate import build_board

    long_df, adp_df = build_sample_long()
    return build_board(long_df, adp_df)
