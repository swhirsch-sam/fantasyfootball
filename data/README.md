# Projection snapshots

The app's **`snapshot`** source reads committed projection files from here so it
never needs a live API at draft time (which avoids ESPN 403s on cloud IPs and
browser CORS limits).

Files (created by the fetcher; not committed until you run it):

- `espn_<season>.json` — raw ESPN `kona_player_info` payload
- `sleeper_<season>.json` — `{"season": <year>, "weeks": [<weekly payloads>]}`

## Refresh them

**In GitHub (no local setup):** Actions tab → **Refresh projections snapshot**
→ *Run workflow*. It also runs automatically on the 1st of each month. The
workflow fetches both sources and commits the files here.

**Locally:** `python tools/fetch_snapshot.py --season 2026`, then commit `data/`.

> If ESPN fails from GitHub's servers (it sometimes blocks datacenter IPs), run
> the fetcher once from your own machine/network — the Sleeper snapshot will
> still be written either way, and the app blends whatever is present.
