"""Pure normalizers that turn each site's scraped table into one long format.

Design mirrors the dynasty tool's ``data_sources``: **network and parsing are
separated**.  The ``fetch_*`` calls live in ``scripts/scrape_league2_projections``;
everything here is a pure ``DataFrame -> DataFrame`` transform, so the column
maps are verifiable offline (see ``tests/test_league2_sources.py``) without ever
hitting a site that 403s datacenter IPs.

Every normalizer returns a **long frame** with the standard columns::

    name, pos, team, source, <raw stat keys that were present>, [fpts]

Missing raw keys are simply absent and default to 0 at scoring time.  For K/DEF
the site FPTS is carried through as ``fpts`` (we don't recompute kicker/defense
scoring — the site defaults are close enough across sources).

Verification status (these sites block datacenter IPs, so layouts marked
"assumed" need one real run to confirm — the scraper prints every table's
shape and detected columns on each run to make that a 30-second check):

* **Razzball**   — columns fully documented in the brief -> precise map. ✅
* **FantasyPros**— QB layout documented; RB/WR/TE follow the same nested-header
  pattern -> group-aware map. ✅ (offense)  K/DST take FPTS.
* **CBS**        — native PPR; per-position headers not documented -> tolerant
  synonym map. ⚙ verify columns on first live run.
* **FFToday**    — classic static table; headers not documented -> tolerant
  synonym map. ⚙ verify columns on first live run.
* **FFC ADP**    — market ADP only (not stats); name/pos/ADP extraction. ✅
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

import pandas as pd

from league2.scoring import (
    RAW_STAT_KEYS,
    SPECIAL_POSITIONS,
    normalize_position,
)

__all__ = [
    "LONG_COLUMNS",
    "pick_data_table",
    "normalize_fantasypros",
    "normalize_razzball",
    "normalize_cbs",
    "normalize_fftoday",
    "parse_ffc_adp",
]

LONG_COLUMNS = ["name", "pos", "team", "source", *RAW_STAT_KEYS, "fpts"]


# ---------------------------------------------------------------------------
# Header synonyms -> canonical raw stat key
# ---------------------------------------------------------------------------
def _norm_header(h) -> str:
    """Lowercase a header and collapse non-alphanumerics to single spaces."""
    if isinstance(h, tuple):  # flattened MultiIndex passed straight through
        h = " ".join(str(x) for x in h if x and "unnamed" not in str(x).lower())
    return re.sub(r"[^a-z0-9]+", " ", str(h).lower()).strip()

# Maps a normalized header string to a canonical key.  Group-qualified spellings
# ("passing yds", "rushing yds") disambiguate the bare ones that collide.
_SYNONYMS: Dict[str, str] = {
    # passing
    "passing yds": "pass_yds", "pass yds": "pass_yds", "pass yards": "pass_yds",
    "pass yard": "pass_yds", "pyds": "pass_yds", "pass yd": "pass_yds",
    "passing tds": "pass_td", "pass tds": "pass_td", "pass td": "pass_td",
    "ptd": "pass_td", "passing td": "pass_td",
    "passing ints": "int", "passing int": "int", "pass int": "int",
    "ints": "int", "int": "int", "interceptions": "int", "pass ints": "int",
    # rushing
    "rushing yds": "rush_yds", "rush yds": "rush_yds", "rush yards": "rush_yds",
    "rush yard": "rush_yds", "ryds": "rush_yds", "rush yd": "rush_yds",
    "rushing tds": "rush_td", "rush tds": "rush_td", "rush td": "rush_td",
    "run td": "rush_td", "rushing td": "rush_td", "rtd": "rush_td",
    # receiving
    "rec": "rec", "receptions": "rec", "receiving rec": "rec", "catches": "rec",
    "receiving yds": "rec_yds", "rec yds": "rec_yds", "rec yards": "rec_yds",
    "rec yard": "rec_yds", "receiving yards": "rec_yds", "rec yd": "rec_yds",
    "receiving tds": "rec_td", "rec tds": "rec_td", "rec td": "rec_td",
    "receiving td": "rec_td", "rectd": "rec_td",
    # fumbles
    "fl": "fumbles_lost", "fum lost": "fumbles_lost", "fumbles lost": "fumbles_lost",
    "fmb": "fumbles_lost", "fumbles": "fumbles_lost", "fum": "fumbles_lost",
    # fantasy points (only used directly for K/DEF)
    "fpts": "fpts", "fantasy pts": "fpts", "fan pts": "fpts", "pts": "fpts",
    "points": "fpts", "proj pts": "fpts",
}

# Headers that name/identify a player rather than carry a stat.
_NAME_HEADERS = {"player", "name", "players"}
_TEAM_HEADERS = {"team", "tm", "nfl team"}
_POS_HEADERS = {"pos", "position"}

# When a synonym-mapped source prefixes a stat with its group (FantasyPros nests
# fumbles under e.g. "MISC FL" / fantasy points under "MISC FPTS"), fall back to
# the trailing token — but only for tokens that are unambiguous on their own.
# Bare "yds"/"tds"/"att" are deliberately excluded (they collide passing vs
# rushing vs receiving), which is exactly why group-qualified spellings exist.
_SAFE_BARE = {"fl": "fumbles_lost", "fpts": "fpts", "rec": "rec"}


def _resolve_key(header: str, stat_map: Dict[str, str]) -> Optional[str]:
    """Map a normalized header to a canonical key via the source's map.

    Exact match wins; for the tolerant synonym map only, an unambiguous trailing
    token (see ``_SAFE_BARE``) is the fallback for group-prefixed headers.
    """
    if header in stat_map:
        return stat_map[header]
    if stat_map is _SYNONYMS and header:
        last = header.split()[-1]
        if last in _SAFE_BARE:
            return _SAFE_BARE[last]
    return None


def pick_data_table(tables: List[pd.DataFrame]) -> pd.DataFrame:
    """Choose the real data table from a ``pd.read_html`` result.

    FFToday wraps its grid in layout tables and several sites prepend tiny
    header/marketing tables, so the right one is *not* reliably ``tables[0]`` —
    pick the table with the most rows (ties broken by column count).
    """
    if not tables:
        raise ValueError("no tables to pick from")
    return max(tables, key=lambda t: (len(t), t.shape[1]))


def _flatten_columns(df: pd.DataFrame) -> List[str]:
    """Flatten possibly-MultiIndex columns to normalized header strings."""
    cols = []
    for c in df.columns:
        if isinstance(c, tuple):
            cols.append(_norm_header(c))
        else:
            cols.append(_norm_header(c))
    return cols


def _split_name_team_pos(value: str):
    """Best-effort pull of (name, team, pos) from a combined player cell.

    Some sites pack everything into one cell, e.g. ``"Patrick Mahomes KC QB"`` or
    ``"Justin Jefferson MIN - WR"``.  Returns (name, team, pos) with team/pos
    blank when not embedded.
    """
    s = re.sub(r"\s+", " ", str(value)).strip()
    pos = ""
    team = ""
    m = re.search(r"\b(QB|RB|WR|TE|K|PK|DST|D/ST|DEF)\b\s*$", s, re.IGNORECASE)
    if m:
        pos = m.group(1)
        s = s[: m.start()].strip(" -•|,")
    m = re.search(r"\b([A-Z]{2,3})\b\s*$", s)
    if m and m.group(1) not in {"II", "III", "IV", "SR", "JR"}:
        team = m.group(1)
        s = s[: m.start()].strip(" -•|,")
    return s.strip(" -•|,"), team, pos


def _to_long(
    df: pd.DataFrame,
    source: str,
    *,
    name_col: str,
    stat_map: Dict[str, str],
    pos: Optional[str] = None,
    pos_col: Optional[str] = None,
    team_col: Optional[str] = None,
    fpts_only: bool = False,
) -> pd.DataFrame:
    """Assemble a normalized long frame from a column map.

    ``stat_map`` maps *normalized header* -> canonical key.  ``pos`` forces a
    single position (FantasyPros-style per-position pages); otherwise ``pos_col``
    supplies it per row (Razzball-style combined table).  ``fpts_only`` keeps
    just the fantasy-points column (K/DEF).
    """
    work = df.copy()
    work.columns = _flatten_columns(work)
    out = pd.DataFrame()

    names, teams, poss = [], [], []
    for raw in _col(work, name_col).astype(str):
        nm, tm, ps = _split_name_team_pos(raw)
        names.append(nm or raw.strip())
        teams.append(tm)
        poss.append(ps)
    out["name"] = names

    if pos is not None:
        out["pos"] = normalize_position(pos)
    elif pos_col and pos_col in work.columns:
        out["pos"] = _col(work, pos_col).map(normalize_position)
    else:
        out["pos"] = [normalize_position(p) for p in poss]

    if team_col and team_col in work.columns:
        out["team"] = _col(work, team_col).astype(str).str.strip().str.upper()
    else:
        out["team"] = [t.upper() for t in teams]

    out["source"] = source

    wanted = {"fpts"} if fpts_only else set(RAW_STAT_KEYS) | {"fpts"}
    for header in work.columns:
        if header in (name_col, pos_col, team_col):
            continue
        key = _resolve_key(header, stat_map)
        if key is None or key not in wanted:
            continue
        series = pd.to_numeric(_col(work, header), errors="coerce")
        if key in out.columns:
            out[key] = out[key].combine_first(series)  # keep first non-null
        else:
            out[key] = series

    # Drop rows that are clearly not players (blank or repeated header rows).
    out = out[out["name"].astype(str).str.strip().astype(bool)]
    out = out[~out["name"].str.lower().isin(_NAME_HEADERS)]
    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
# FantasyPros — nested (group, stat) headers; per-position pages
# ---------------------------------------------------------------------------
def normalize_fantasypros(df: pd.DataFrame, pos: str, source: str = "fantasypros") -> pd.DataFrame:
    """Normalize one FantasyPros projections table for position ``pos``.

    FantasyPros nests two header rows (group + stat).  We flatten to
    ``"group stat"`` so colliding bare names ("YDS" appears under both Passing
    and Rushing) resolve correctly.  K/DST keep the site FPTS.
    """
    pos_u = normalize_position(pos)
    work = df.copy()
    work.columns = _flatten_columns(work)
    name_col = _first_present(work.columns, _NAME_HEADERS) or work.columns[0]

    if pos_u in SPECIAL_POSITIONS:
        return _to_long(df, source, name_col=name_col, stat_map=_SYNONYMS,
                        pos=pos_u, fpts_only=True)
    return _to_long(df, source, name_col=name_col, stat_map=_SYNONYMS, pos=pos_u)


# ---------------------------------------------------------------------------
# Razzball — one combined table, raw columns fully documented
# ---------------------------------------------------------------------------
# Documented live columns:
#   Name, Pos, Team, STD PTS, 1/2PPR PTS, PPR PTS, Cmp, Att, Pass Yds, Pass TD,
#   Int, Rush, Rush Yds, Run TD, Tgt, Rec, Rec Yds, Rec TD
_RAZZBALL_MAP = {
    "pass yds": "pass_yds", "pass td": "pass_td", "int": "int",
    "rush yds": "rush_yds", "run td": "rush_td",
    "rec": "rec", "rec yds": "rec_yds", "rec td": "rec_td",
}


def normalize_razzball(df: pd.DataFrame, source: str = "razzball") -> pd.DataFrame:
    """Normalize the single combined Razzball projections table (all positions).

    We recompute points from the raw columns rather than using their ``PPR PTS``
    column, for consistency with the other sources.  K/DEF rows (which lack raw
    offensive stats) fall back to their site points at scoring time.
    """
    work = df.copy()
    work.columns = _flatten_columns(work)
    name_col = _first_present(work.columns, _NAME_HEADERS) or "name"
    pos_col = _first_present(work.columns, _POS_HEADERS)
    team_col = _first_present(work.columns, _TEAM_HEADERS)
    stat_map = dict(_RAZZBALL_MAP)
    # carry a points column so K/DEF still get a value
    for h in ("ppr pts", "std pts", "fpts"):
        if h in work.columns:
            stat_map[h] = "fpts"
            break
    return _to_long(df, source, name_col=name_col, stat_map=stat_map,
                    pos_col=pos_col, team_col=team_col)


# ---------------------------------------------------------------------------
# CBS / FFToday — tolerant synonym maps (headers not documented live)
# ---------------------------------------------------------------------------
def normalize_cbs(df: pd.DataFrame, pos: str, source: str = "cbs") -> pd.DataFrame:
    """Normalize a CBS per-position PPR projections table (tolerant)."""
    return _normalize_generic(df, pos, source)


def normalize_fftoday(df: pd.DataFrame, pos: str, source: str = "fftoday") -> pd.DataFrame:
    """Normalize an FFToday per-position projections table (tolerant)."""
    return _normalize_generic(df, pos, source)


def _normalize_generic(df: pd.DataFrame, pos: str, source: str) -> pd.DataFrame:
    pos_u = normalize_position(pos)
    work = df.copy()
    work.columns = _flatten_columns(work)
    name_col = _first_present(work.columns, _NAME_HEADERS) or work.columns[0]
    team_col = _first_present(work.columns, _TEAM_HEADERS)
    fpts_only = pos_u in SPECIAL_POSITIONS
    return _to_long(df, source, name_col=name_col, stat_map=_SYNONYMS, pos=pos_u,
                    team_col=team_col, fpts_only=fpts_only)


# ---------------------------------------------------------------------------
# Fantasy Football Calculator — ADP market cross-check (not stats)
# ---------------------------------------------------------------------------
def parse_ffc_adp(df: pd.DataFrame, source: str = "ffc_adp") -> pd.DataFrame:
    """Extract ``name, pos, market_adp`` from the FFC PPR rankings table.

    ADP is used only as a market sanity-check column downstream (not blended
    into the points average).
    """
    work = df.copy()
    work.columns = _flatten_columns(work)
    name_col = _first_present(work.columns, _NAME_HEADERS) or work.columns[0]
    pos_col = _first_present(work.columns, _POS_HEADERS)
    adp_col = None
    for h in ("adp", "overall", "rank", "pick"):
        if h in work.columns:
            adp_col = h
            break

    out = pd.DataFrame()
    names, teams, poss = zip(*(_split_name_team_pos(v) for v in work[name_col].astype(str)))
    out["name"] = [n or r for n, r in zip(names, work[name_col].astype(str))]
    if pos_col:
        out["pos"] = work[pos_col].map(normalize_position)
    else:
        out["pos"] = [normalize_position(p) for p in poss]
    out["market_adp"] = (
        pd.to_numeric(work[adp_col], errors="coerce") if adp_col else pd.NA
    )
    out["source"] = source
    out = out[out["name"].astype(str).str.strip().astype(bool)]
    return out.reset_index(drop=True)


def _first_present(columns, candidates) -> Optional[str]:
    """Return the first column whose normalized name is in ``candidates``."""
    for c in columns:
        if c in candidates:
            return c
    return None


def _col(df: pd.DataFrame, label: str) -> pd.Series:
    """Fetch a column as a Series, taking the first if the label is duplicated."""
    obj = df[label]
    return obj.iloc[:, 0] if isinstance(obj, pd.DataFrame) else obj
