# 🏈 My Auction War Room

A **personal** fantasy-football auction draft tool, hard-wired to one league:
**16 teams · $200 budget · full PPR**. It pulls projections from ESPN and/or
Sleeper, scores every player under custom rules, and turns those points into
**dollar values** plus a stack of draft-day decision metrics.

This is deliberately *not* a configurable app for anyone — there are no
league-setting knobs. The league is locked in code (`app.py` / `scoring.py`)
and the UI spends its space on **analysis** instead.

It ships with realistic offline SAMPLE data, so everything works immediately;
live sources are opt-in.

---

## Quick start

```bash
pip install -r requirements.txt

streamlit run app.py            # the draft-day app
python tools/preview_live.py    # terminal preview / live-data sanity check
pytest -q                       # tests (incl. the live-mapping checks)
```

The app has four tabs:

| Tab | What it does |
| --- | --- |
| 💰 **Auction Board** | Every player priced in $, with **pos rank, Pts/G, VORP, VOLS, value-per-dollar, tier** and inflation-adjusted $. A live tracker shows your **budget, max bid, roster slots filled, and positional needs** as you tick off picks. |
| 📈 **Analysis** | **Budget allocation** (how the market should split your $200 across positions), **positional scarcity** (starter cliffs), a **bargain finder** (best VORP/$), and tier-1 must-pay targets. |
| 📊 **Tiers** | A per-position tier-break visual so you can see the cliffs. |
| 🔎 **Diagnostics** | Which source was used, coverage by position, and any **unmapped stat keys** — the early-warning signal that a source changed its schema. |

---

## Run it as a website (for yourself)

You can reach the tool from a browser without a local setup two ways:

- **Streamlit Community Cloud** — runs the real app server-side (live data
  works). It can be made **private** (invite-only by email) in the app
  settings, which suits a personal tool. Deploy from `main` / `app.py` at
  [share.streamlit.io](https://share.streamlit.io).
- **GitHub Pages (static)** — `docs/index.html` runs the app entirely in the
  browser via [stlite](https://github.com/whitphx/stlite) (Streamlit on
  WebAssembly), no server. Note Pages is **public** to anyone with the link,
  and it uses SAMPLE data only (browsers can't make the live ESPN/Sleeper calls
  — CORS). Enable via **Settings → Pages → Deploy from a branch → `main` →
  `/docs`**. The page regenerates with `python tools/build_static.py`.

---

## How values are computed

1. **Score** every player with the scoring rules (`scoring.py`).
2. **Replacement level** per position = the best player who *isn't* a starter,
   with the FLEX filled by the best leftover RB/WR/TE across the 16 teams.
3. **VORP** = points above replacement. **VOLS** = points above the *last
   starter* (a second, stricter baseline).
4. **Dollars**: the pool (`teams × budget` = $3,200) minus a $1 minimum bid per
   roster slot is distributed proportionally to VORP, so assigned dollars sum
   back to the pool.

### Metrics you get per player
| Metric | Meaning |
| --- | --- |
| **Pos rank** | Rank within position (e.g. `RB3`). |
| **Pts/G** | Projected points per game. |
| **VORP / VOLS** | Value over replacement / over the last starter. |
| **Value $ / Adj $** | Auction value, and value re-scaled for live draft **inflation**. |
| **V/$** | VORP bought per dollar — the **bargain** signal. |
| **Tier** | Auto-detected from value drop-offs within the position. |

### League-level analysis
- **Budget allocation** — recommended $ per position per team (sums to $200).
- **Positional scarcity** — startable counts and the *starter cliff* (points
  between the last starter and replacement) so you know where to pay up.
- **Bargain finder** and **tier-1 targets**.

---

## Scoring (fully editable)

All rules live in `scoring.py` as plain numbers — edit them and the whole tool
re-prices. Defaults are full-PPR dynasty:

- **Passing** 1pt/25yd, 4/TD, −2/INT, +2/2pt
- **Rushing** 1pt/10yd, 6/TD, +2/2pt
- **Receiving** full PPR, 1pt/10yd, 6/TD, +2/2pt
- **Fumbles lost** −2
- **Kicking** FG by distance (0-19/20-29/30-39 = 3, 40-49 = 4, 50+ = 5), XP = 1,
  misses −1
- **Defense/ST** 1/sack, 2/INT, 2/fumble rec, 6/TD, 2/safety, 2/blocked kick
- **Points allowed (per game)** tiered: `0 → 10`, `1-6 → 7`, `7-13 → 4`,
  `14-20 → 1`, `21-27 → 0`, `28-34 → −1`, `35+ → −4`

---

## Data sources & stat-mapping provenance

Network fetching and parsing are deliberately **separated** (`fetch_*` vs
`parse_*` in `data_sources.py`). The parsers are pure functions, so the stat
mapping is verified offline by pushing realistic payloads through them
(`tests/test_data_sources.py`) — no live call required.

| Piece | Status | Verified against |
| --- | --- | --- |
| **ESPN offense IDs** (3=passYds, 4=passTD, 20=INT, 24=rushYds, 25=rushTD, 53=rec, 42=recYds, 43=recTD, 72=lostFum, 19/26/44=2pt) | ✅ correct | canonical `espn-api` `STATS_MAP` (1:1) |
| **Sleeper offense keys** (`pass_yd`, `rush_yd`, `rec_yd`, `rec`, `fum_lost`, …) | ✅ correct | Sleeper projection schema |
| **Sleeper kicker buckets** (`fgm_0_19`…`fgm_50p`, `xpm`, `xpmiss`) | ✅ correct | Sleeper `PlayerStats` schema |
| **Sleeper DST** (`pts_allow`, `sack`, `int`, `fum_rec`, `def_td`, `safe`, `blk_kick`) | ✅ correct | same |
| **Sleeper missed-FG keys** | 🔧 fixed | Sleeper only buckets misses at 30-39 / 40-49 / 50+, so they collapse into one `fgmiss`; the non-existent `fgmiss_0_19`/`fgmiss_20_29` are gone |
| **ESPN K/DST** | ⚙️ intentionally unmapped | ESPN's points-allowed/FG buckets don't line up with this league's tiers, so the **blend** takes offense from ESPN and K/DST from Sleeper |

### The `blend` source
`blend` = **ESPN offense + Sleeper kickers/defenses** — the cleanest of each,
rather than averaging.

### What can't be verified without a live pull
All outbound HTTP is blocked in CI/sandboxes, so two things need exactly one
real run to confirm (run `tools/preview_live.py` on open internet):

- **ESPN's `X-Fantasy-Filter`** actually returning the projection stat blocks
  for the target season. If it returns nothing usable, the blend silently
  drops to Sleeper-only / SAMPLE.
- **Sleeper offseason availability** — early offseason weekly projections are
  often sparse or zero.

The failure mode of any mapping drift is *silent under-projection*, which is why
`preview_live.py` flags every unmapped key and prints top players per position —
so you can eyeball live data before draft day.

---

## Project layout

```
app.py                 Streamlit app (auction board, tiers, diagnostics)
scoring.py             Custom scoring rules (edit me)
data_sources.py        ESPN/Sleeper fetchers + pure parsers + SAMPLE data
valuation.py           VBD: replacement levels, VORP, dollar values, tiers
tools/preview_live.py  Terminal preview / live-data sanity check
tests/                 Scoring, parser, and valuation tests
.claude/               SessionStart hook so web sessions install deps
```

---

## Notes
- Built to run on [Claude Code on the web](https://code.claude.com/docs); the
  `.claude/` SessionStart hook installs dependencies automatically in fresh
  containers.
- SAMPLE projections are illustrative, not official forecasts.
