#!/usr/bin/env python3
"""Preview live (or sample) projections from the terminal.

This is the tool for sanity-checking the live-data path before draft day.
On open internet it will:

* pull projections from the chosen source (sleeper / espn / blend),
* flag any stat keys or IDs the source sent that we don't map — the signal
  that the mapping may have drifted and points are being silently dropped, and
* print the top projected players per position under your custom scoring.

Run from the repo root, e.g.::

    python tools/preview_live.py --source blend --season 2026
    python tools/preview_live.py --source sleeper --weeks 1-17
    python tools/preview_live.py --source sample          # offline smoke test
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import data_sources as ds  # noqa: E402
from scoring import Scoring  # noqa: E402
from valuation import LeagueSettings, compute_values  # noqa: E402

POSITIONS = ["QB", "RB", "WR", "TE", "K", "DST"]
# Match the personal league baked into the app.
LEAGUE = LeagueSettings(teams=16, budget=200)


def _parse_weeks(text: str):
    if "-" in text:
        lo, hi = text.split("-", 1)
        return range(int(lo), int(hi) + 1)
    return [int(w) for w in text.split(",") if w.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="blend",
                    choices=["sample", "sleeper", "espn", "blend"])
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--weeks", default="1-17", help="e.g. 1-17 or 1,2,3")
    ap.add_argument("--top", type=int, default=10, help="players per position")
    args = ap.parse_args(argv)

    weeks = _parse_weeks(args.weeks)
    print(f"\nLoading '{args.source}' projections for {args.season} …\n")
    projections, diag = ds.get_projections(args.source, season=args.season,
                                           weeks=weeks)

    # --- diagnostics --------------------------------------------------------
    print("=" * 64)
    print(f"  source requested : {diag.source_requested}")
    print(f"  source used      : {diag.source_used}")
    print(f"  players loaded   : {len(projections)}")
    print(f"  by position      : "
          + ", ".join(f"{p}={diag.counts.get(p, 0)}" for p in POSITIONS))
    if diag.source_counts:
        print("  players per API  : "
              + ", ".join(f"{k}={v}" for k, v in sorted(diag.source_counts.items())))
    for note in diag.notes:
        print(f"  note  : {note}")
    for err in diag.errors:
        print(f"  ERROR : {err}")

    if diag.unmapped:
        print("\n  ⚠ UNMAPPED stat keys (review for scoring relevance):")
        for src, keys in diag.unmapped.items():
            print(f"      {src}: {', '.join(sorted(keys))}")
    else:
        print("\n  ✓ every stat key the source sent was mapped")
    print("=" * 64)

    # --- top players per position ------------------------------------------
    valued = compute_values(projections, Scoring(), LEAGUE)
    by_pos = {}
    for vp in valued:
        by_pos.setdefault(vp.position, []).append(vp)

    for pos in POSITIONS:
        players = sorted(by_pos.get(pos, []), key=lambda vp: vp.points, reverse=True)
        if not players:
            continue
        print(f"\n{pos}")
        print(f"  {'Player':24s} {'Team':4s} {'Pts':>7s} {'VORP':>7s} "
              f"{'$':>6s}  Tier")
        for vp in players[: args.top]:
            print(f"  {vp.name[:24]:24s} {vp.team:4s} {vp.points:7.1f} "
                  f"{vp.vorp:7.1f} {vp.value:6.1f}   T{vp.tier}")

    if "sample" in diag.source_used and args.source != "sample":
        print("\n(Live data unavailable here — showing SAMPLE. Re-run on open "
              "internet to fetch real projections.)")
    print()


if __name__ == "__main__":
    main()
