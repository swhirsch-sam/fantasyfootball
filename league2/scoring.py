"""Scoring + identity normalization for the 8-team 2QB PPR snake league.

This is **League #2** — a deliberately separate tool from the dynasty auction
app at the repo root.  It is its own league with its own structural facts:

* 8 teams, full PPR, standard **snake** draft (output is a ranked board /
  tiers, not auction dollars).
* Roster: ``2 QB · 2 RB · 3 WR · 3 FLEX · 1 K · 1 DEF`` — 12 starters/team,
  96 league-wide.  No dedicated TE slot; TEs only start via FLEX.
* The headline fact: **16 of the 96 starting spots are QB**, so QB replacement
  level sits much deeper than a 1-QB league and backup-caliber QBs carry real
  startable value.  The VORP math in :mod:`league2.vorp` reflects that rather
  than reusing the dynasty league's positional curves.

Why a separate scoring module from the root ``scoring.py``?  The dynasty tool
normalizes every site to its own canonical keys (``pass_yd``, ``pass_int`` …)
and scores K/DST through custom bucket tiers.  League #2 instead recomputes
points from each *site's raw stat columns* through one shared formula so the
five sources aggregate apples-to-apples — a different pipeline with different
keys, so it lives in its own namespace.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Dict

__all__ = [
    "LEAGUE_CONFIG",
    "PPR_SCORING",
    "RAW_STAT_KEYS",
    "ROSTER_POSITIONS",
    "OFFENSE_POSITIONS",
    "SPECIAL_POSITIONS",
    "score_player",
    "normalize_name",
    "normalize_position",
]

# ---------------------------------------------------------------------------
# League configuration
# ---------------------------------------------------------------------------
# ``flex_eligible`` is the one genuine assumption — flip it to include "QB" if
# this turns out to be a superflex league.
LEAGUE_CONFIG: Dict = {
    "teams": 8,
    "draft_type": "snake",
    "scoring": "ppr",  # 1 pt / reception
    "roster": {
        "QB": 2,
        "RB": 2,
        "WR": 3,
        "FLEX": 3,   # shared pool
        "K": 1,
        "DEF": 1,
    },
    "flex_eligible": ["RB", "WR", "TE"],  # ASSUMPTION — confirm vs superflex
}

# ---------------------------------------------------------------------------
# Scoring — full PPR, recomputed from raw stats for every source
# ---------------------------------------------------------------------------
# Editing these numbers re-scores the whole board.  Sites disagree on default
# scoring (FantasyPros' free tables are Standard, not PPR), so we never trust a
# source's own FPTS for offense — we pull the raw stat columns and run every
# source through this one formula.  K/DEF are the exception: their site FPTS is
# taken directly (see ``league2.aggregate``).
PPR_SCORING: Dict[str, float] = {
    "pass_yds": 1 / 25,
    "pass_td": 4.0,
    "int": -2.0,
    "rush_yds": 1 / 10,
    "rush_td": 6.0,
    "rec": 1.0,          # full PPR
    "rec_yds": 1 / 10,
    "rec_td": 6.0,
    "fumbles_lost": -2.0,
}

# The canonical raw-stat columns every offensive source is normalized to.
RAW_STAT_KEYS = tuple(PPR_SCORING.keys())

ROSTER_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
OFFENSE_POSITIONS = ("QB", "RB", "WR", "TE")
SPECIAL_POSITIONS = ("K", "DEF")

# Sources spell the defense position a few different ways; the roster and the
# VORP math both use "DEF" (matching LEAGUE_CONFIG), so everything funnels here.
_POSITION_ALIASES = {
    "DST": "DEF",
    "D/ST": "DEF",
    "DST/D": "DEF",
    "D": "DEF",
    "DEF": "DEF",
    "PK": "K",
}


def score_player(stats: Dict[str, float], scoring: Dict[str, float] = PPR_SCORING) -> float:
    """Project fantasy points for one raw stat line.

    ``stats`` values default to 0 if a key is missing (e.g. WRs have no
    ``pass_yds``), so a partial line scores correctly.
    """
    return round(sum(stats.get(k, 0) * w for k, w in scoring.items()), 2)


def normalize_name(name: str) -> str:
    """Canonicalize a player name for cross-source joining.

    Strips accents, generational suffixes (Jr/Sr/II/III/IV) and punctuation so
    the five sites' differing formats collapse to one key.  The join key used
    downstream is ``(normalize_name(name), position)`` — team alone is unreliable
    across mid-season trades / stale source data.
    """
    name = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    name = re.sub(r"\b(Jr|Sr|II|III|IV)\.?\b", "", name, flags=re.IGNORECASE)
    name = re.sub(r"[^a-zA-Z ]", "", name)
    return re.sub(r"\s+", " ", name).strip().lower()


def normalize_position(pos: str) -> str:
    """Map a source's position string to a canonical roster position.

    Handles defense aliases (DST/D/ST -> DEF) and strips any trailing rank a
    source may append (e.g. an ADP page showing ``RB12`` -> ``RB``).
    """
    p = str(pos or "").upper().strip()
    p = re.sub(r"\d+$", "", p).strip()  # "RB12" -> "RB"
    return _POSITION_ALIASES.get(p, p)
