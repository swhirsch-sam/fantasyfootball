#!/usr/bin/env python3
"""Scrape the five League #2 sources and build ``data/league2_projections.csv``.

Run anywhere with open internet — locally or via the *refresh-league2-projections*
GitHub Action — and commit the resulting CSV.  The Streamlit page only ever reads
that file; it never scrapes.

    python scripts/scrape_league2_projections.py
    python scripts/scrape_league2_projections.py --sample   # offline rebuild

Design (mirrors the dynasty tool's ``data_sources``): the network ``fetch_*``
calls here are kept thin and dumb; all column mapping lives in the pure
``league2.sources`` normalizers, which are unit-tested offline.  Each source is
fetched independently and wrapped in try/except, so one site failing (they 403
datacenter IPs aggressively) never sinks the others.  If *no* stat source comes
back, we fall back to the bundled sample so a usable CSV always lands — the run
prints, and writes to a sidecar ``.meta.json``, exactly what each source
returned (incl. ``[t.shape for t in tables]``) so an unverified layout is a
30-second fix on the first real run.

Verified-vs-assumed source layouts are documented in ``league2/sources.py``.
FFToday's query params and CBS's table index in particular are best-guesses
until one real run confirms them — that's what the printed diagnostics are for.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from io import StringIO
from typing import List, Optional

import pandas as pd

try:  # requests is only needed for live scraping
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from league2 import sources as src  # noqa: E402
from league2.aggregate import build_board  # noqa: E402
from league2.sample import build_sample_long  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data")
CSV_PATH = os.path.join(DATA_DIR, "league2_projections.csv")
META_PATH = os.path.join(DATA_DIR, "league2_projections.meta.json")

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "text/html,application/xhtml+xml"}

# Per-source position lists / URL templates.
FANTASYPROS_POS = ["qb", "rb", "wr", "te", "k", "dst"]
CBS_POS = ["QB", "RB", "WR", "TE", "K", "DST"]
# FFToday param names are UNVERIFIED (see module docstring) — best-guess mapping.
FFTODAY_POS_PARAM = {
    "QB": "Pos=10", "RB": "Pos=20", "WR": "Pos=30",
    "TE": "Pos=40", "K": "Pos=80", "DEF": "Pos=99",
}


# ---------------------------------------------------------------------------
# Network (thin; not unit-tested — the pure normalizers carry the logic)
# ---------------------------------------------------------------------------
def _read_tables(url: str, label: str) -> List[pd.DataFrame]:  # pragma: no cover
    if requests is None:
        raise RuntimeError("the 'requests' package is required for live scraping")
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    tables = pd.read_html(StringIO(resp.text))
    print(f"    {label}: {len(tables)} tables, shapes={[t.shape for t in tables]}")
    return tables


def fetch_fantasypros(pos: str) -> pd.DataFrame:  # pragma: no cover
    url = f"https://www.fantasypros.com/nfl/projections/{pos}.php?week=draft"
    return src.pick_data_table(_read_tables(url, f"fantasypros/{pos}"))


def fetch_cbs(pos: str, year: int) -> pd.DataFrame:  # pragma: no cover
    url = (f"https://www.cbssports.com/fantasy/football/stats/{pos}/{year}"
           "/season/projections/ppr/")
    return src.pick_data_table(_read_tables(url, f"cbs/{pos}"))


def fetch_fftoday(pos: str, year: int) -> pd.DataFrame:  # pragma: no cover
    param = FFTODAY_POS_PARAM.get(pos, "")
    url = f"https://www.fftoday.com/rankings/playerproj.php?{param}&LeagueID=1&Season={year}"
    return src.pick_data_table(_read_tables(url, f"fftoday/{pos}"))


def fetch_razzball() -> pd.DataFrame:  # pragma: no cover
    url = "https://football.razzball.com/projections/"
    return src.pick_data_table(_read_tables(url, "razzball"))


def fetch_ffc_adp() -> pd.DataFrame:  # pragma: no cover
    url = "https://fantasyfootballcalculator.com/rankings/ppr"
    return src.pick_data_table(_read_tables(url, "ffc_adp"))


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def scrape_live(year: int):  # pragma: no cover - network
    """Fetch + normalize every source. Returns ``(long_df, adp_df, meta)``."""
    frames: List[pd.DataFrame] = []
    meta = {"counts": {}, "errors": [], "columns": {}}

    def run(label: str, fn):
        try:
            df = fn()
        except Exception as exc:  # noqa: BLE001
            meta["errors"].append(f"{label}: {type(exc).__name__}: {exc}")
            print(f"  {label} FAILED: {type(exc).__name__}: {exc}")
            return None
        return df

    print("FantasyPros …")
    for pos in FANTASYPROS_POS:
        df = run(f"fantasypros/{pos}", lambda p=pos: fetch_fantasypros(p))
        if df is not None:
            frames.append(src.normalize_fantasypros(df, pos))

    print("CBS …")
    for pos in CBS_POS:
        df = run(f"cbs/{pos}", lambda p=pos: fetch_cbs(p, year))
        if df is not None:
            frames.append(src.normalize_cbs(df, pos))

    print("FFToday …")
    for pos in ["QB", "RB", "WR", "TE", "K", "DEF"]:
        df = run(f"fftoday/{pos}", lambda p=pos: fetch_fftoday(p, year))
        if df is not None:
            frames.append(src.normalize_fftoday(df, pos))

    print("Razzball …")
    df = run("razzball", fetch_razzball)
    if df is not None:
        frames.append(src.normalize_razzball(df))

    print("FFC ADP …")
    adp_df = None
    df = run("ffc_adp", fetch_ffc_adp)
    if df is not None:
        adp_df = src.parse_ffc_adp(df)

    long_df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    for s, sub in long_df.groupby("source") if len(long_df) else []:
        meta["counts"][s] = int(len(sub))
    if adp_df is not None:
        meta["counts"]["ffc_adp"] = int(len(adp_df))
    return long_df, adp_df, meta


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int, default=date.today().year)
    ap.add_argument("--out", default=CSV_PATH)
    ap.add_argument("--sample", action="store_true",
                    help="skip the network and rebuild from bundled sample data")
    args = ap.parse_args(argv)

    meta = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "season": args.season, "counts": {}, "errors": []}

    long_df, adp_df = pd.DataFrame(), None
    if not args.sample:
        long_df, adp_df, live_meta = scrape_live(args.season)
        meta.update({"counts": live_meta["counts"], "errors": live_meta["errors"]})

    # No usable stat rows (every site 403'd, or --sample) -> bundled sample.
    offense_rows = 0 if long_df.empty else int((~long_df["pos"].isin(["K", "DEF"])).sum())
    if offense_rows == 0:
        if not args.sample:
            print("\nNo live stat data (all sources failed) — using bundled sample.")
        long_df, adp_df = build_sample_long()
        meta["fallback_to_sample"] = True
    else:
        meta["fallback_to_sample"] = False

    board = build_board(long_df, adp_df)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    board.to_csv(args.out, index=False)
    meta["players"] = int(len(board))
    meta["source_columns"] = sorted(c for c in board.columns if c.startswith("pts_"))
    with open(META_PATH, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)

    tag = " (sample)" if meta["fallback_to_sample"] else ""
    print(f"\nWrote {os.path.relpath(args.out)} — {len(board)} players{tag}.")
    print(f"  source point columns: {meta['source_columns']}")
    if meta["errors"]:
        print(f"  {len(meta['errors'])} source error(s) — see {os.path.relpath(META_PATH)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
