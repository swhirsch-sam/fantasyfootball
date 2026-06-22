"""League #2 — 8-team, 2QB, full-PPR snake draft tool.

A separate tool from the dynasty auction app at the repo root: snake draft (so
the output is a ranked VORP board with tiers, not auction dollars), built by
recomputing every source's points from raw stats through one shared PPR formula
and taking the median across sources.

Public surface:

* :func:`league2.scoring.score_player` / :func:`~league2.scoring.normalize_name`
* :data:`league2.scoring.LEAGUE_CONFIG` / :data:`~league2.scoring.PPR_SCORING`
* :func:`league2.vorp.compute_vorp`
* :func:`league2.aggregate.build_board` — the full scrape-output -> board pipeline
"""

from league2.aggregate import aggregate_sources, build_board, score_long_frame
from league2.scoring import (
    LEAGUE_CONFIG,
    PPR_SCORING,
    normalize_name,
    normalize_position,
    score_player,
)
from league2.vorp import assign_tiers, compute_vorp

__all__ = [
    "LEAGUE_CONFIG",
    "PPR_SCORING",
    "score_player",
    "normalize_name",
    "normalize_position",
    "compute_vorp",
    "assign_tiers",
    "build_board",
    "aggregate_sources",
    "score_long_frame",
]
