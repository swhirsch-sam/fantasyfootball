# 🏈 Dynasty Auction Tool

A fantasy-football **auction draft** tool with fully custom scoring. It pulls
projections from ESPN and/or Sleeper, scores every player under *your* league
rules, and turns those points into **dollar values** using value-based drafting
(VBD) — then helps you run draft day with a live budget + inflation tracker.

It ships with realistic offline SAMPLE data, so everything works the moment you
clone it; live sources are opt-in.

---

## Quick start

```bash
pip install -r requirements.txt

streamlit run app.py            # the draft-day app
python tools/preview_live.py    # terminal preview / live-data sanity check
pytest -q                       # tests (incl. the live-mapping checks)
```

The app opens with three tabs:

| Tab | What it does |
| --- | --- |
| 💰 **Auction Board** | Sortable $ values, tiers, search/position filters, and a draft tracker that updates **budget**, **max bid**, and **inflation** live as you tick off picks. |
| 📊 **Tiers** | A per-position tier-break visual so you can see the cliffs. |
| 🔎 **Data Diagnostics** | Which source was used, coverage by position, and any **unmapped stat keys** — the early-warning signal that a source changed its schema. |

---

## How values are computed

1. **Score** every player with your scoring rules (`scoring.py`).
2. **Replacement level** per position = the best player who *isn't* a starter,
   with the FLEX filled by the best leftover RB/WR/TE across the league.
3. **VORP** = points above that replacement level.
4. **Dollars**: the auction pool (`teams × budget`) minus a $1 minimum bid per
   roster slot is distributed proportionally to VORP. Total assigned dollars
   therefore sum back to the pool.

Tiers are detected from natural drop-offs in value within each position.

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
