#!/usr/bin/env python3
"""Fetch ESPN + Sleeper projections once and save them to ``data/``.

This is the "pull it manually / monthly" path. Run it anywhere with open
internet — locally, or via the *Refresh projections snapshot* GitHub Action —
and commit the resulting ``data/*.json`` files. The app's ``snapshot`` source
then reads them with the same verified parsers, with no live API call at draft
time.

    python tools/fetch_snapshot.py --season 2026
    python tools/fetch_snapshot.py --season 2026 --weeks 1-17

Each source is fetched independently: if ESPN fails (it's the finicky one),
the Sleeper snapshot is still written.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import data_sources as ds  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data")


def _parse_weeks(text: str):
    if "-" in text:
        lo, hi = text.split("-", 1)
        return list(range(int(lo), int(hi) + 1))
    return [int(w) for w in text.split(",") if w.strip()]


def _write(path: str, payload) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    print(f"  wrote {os.path.relpath(path)} ({os.path.getsize(path) // 1024} KB)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--weeks", default="1-17")
    ap.add_argument("--out", default=DATA_DIR)
    args = ap.parse_args(argv)

    espn_path, sleeper_path = ds.snapshot_paths(args.season, args.out)
    weeks = _parse_weeks(args.weeks)
    failures = 0

    print(f"Fetching ESPN {args.season} …")
    try:
        payload = ds.fetch_espn(args.season)
        n = len(payload.get("players", []))
        _write(espn_path, payload)
        print(f"  ESPN: {n} player records")
    except Exception as exc:  # noqa: BLE001
        failures += 1
        print(f"  ESPN FAILED: {type(exc).__name__}: {exc}")
        print("  (ESPN sometimes blocks datacenter IPs — try running this "
              "locally from your own network.)")

    print(f"Fetching Sleeper {args.season} weeks {args.weeks} …")
    try:
        weeks_data = ds.fetch_sleeper_season(args.season, weeks)
        total = sum(len(w) for w in weeks_data)
        _write(sleeper_path, {"season": args.season, "weeks": weeks_data})
        print(f"  Sleeper: {len(weeks_data)} weeks, {total} rows")
    except Exception as exc:  # noqa: BLE001
        failures += 1
        print(f"  Sleeper FAILED: {type(exc).__name__}: {exc}")

    if failures == 2:
        print("\nBoth sources failed — nothing written.")
        return 1
    print("\nDone. Commit the data/ files to use them via the 'snapshot' source.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
