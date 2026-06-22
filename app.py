"""Personal dynasty auction war room — Streamlit front end.

This is a *personal* draft tool, hard-wired to one league (16 teams, $200
auction budget). There are intentionally no league-setting controls — the
sidebar space is spent on analysis instead.

Run with:  streamlit run app.py

Tabs
----
* Auction Board    $ values + deep metrics, your roster/budget tracker, draft log
* Analysis         budget allocation, positional scarcity, and bargain finder
* Tiers            per-position tier-break visual
* Data Diagnostics source used, coverage, and any unmapped stat keys
"""

from __future__ import annotations

import altair as alt  # ships with streamlit (its native charts depend on it)
import pandas as pd
import streamlit as st

import data_sources as ds
from scoring import DEFAULT_DST_PA_TIERS, DEFAULT_SCORING, Scoring
from valuation import LeagueSettings, compute_values, positional_summary

# --- Locked league configuration -------------------------------------------
# This tool is personal: the league is fixed. Change it here in code, not in
# the UI.
LEAGUE = LeagueSettings(
    teams=16,
    budget=200,
    starters={"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1},
    bench=6,
)
FLEX_ELIGIBLE = ("RB", "WR", "TE")

# The season is locked too — this is a personal, single-season tool. Bump it
# here in code (and refresh the data/ snapshot) when the next season opens.
SEASON = 2026

# Bump on each deploy; shown in the sidebar so you can confirm a live deploy is
# running the latest code (and not a stale cache).
APP_BUILD = "2026-06-22.1"

st.set_page_config(page_title="My Auction War Room", page_icon="🏈", layout="wide")

SOURCE_LABELS = {
    "snapshot": "Snapshot — committed ESPN + Sleeper files (recommended)",
    "sleeper": "Sleeper (live)",
    "espn": "ESPN (live)",
    "blend": "Blend — ESPN offense + Sleeper K/DST (live)",
    "sample": "Sample (offline demo data)",
}
POS_ORDER = ["QB", "RB", "WR", "TE", "K", "DST"]


@st.cache_data(show_spinner="Loading projections…")
def load_projections(source: str, season: int):
    return ds.get_projections(source, season=season)


# ---------------------------------------------------------------------------
# Data prep
# ---------------------------------------------------------------------------
def build_dataframe(valued) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "id": vp.projection.player_id,
                "Player": vp.name,
                "Pos": vp.position,
                "PosRk": vp.pos_rank,
                "Team": vp.team,
                "Proj": round(vp.points, 1),
                "Pts/G": vp.ppg,
                "VORP": round(vp.vorp, 1),
                "VOLS": round(vp.vols, 1),
                "Value $": vp.value,
                "V/$": vp.vorp_per_dollar,
                "Tier": vp.tier,
                "Starter": vp.is_starter,
            }
            for vp in valued
        ]
    ).set_index("id")


def _draft_state(index) -> pd.DataFrame:
    base = pd.DataFrame({"Drafted": False, "Mine": False, "Paid": 0.0}, index=index)
    state = st.session_state.get("draft_state")
    if state is not None:
        base.update(state.reindex(index))
        base["Drafted"] = base["Drafted"].astype(bool)
        base["Mine"] = base["Mine"].astype(bool)
    st.session_state.draft_state = base
    return base


# ---------------------------------------------------------------------------
# Roster tracker
# ---------------------------------------------------------------------------
def roster_status(my_positions: list[str]) -> pd.DataFrame:
    """Greedily fit my drafted players into starter / FLEX / bench slots."""
    need = {p: LEAGUE.starters.get(p, 0) for p in POS_ORDER}
    have = {p: my_positions.count(p) for p in POS_ORDER}
    filled = {p: min(have[p], need[p]) for p in POS_ORDER}

    leftovers = {p: have[p] - filled[p] for p in POS_ORDER}
    flex_need = LEAGUE.starters.get("FLEX", 0)
    flex_used = 0
    for p in FLEX_ELIGIBLE:
        take = min(leftovers[p], flex_need - flex_used)
        flex_used += take
        leftovers[p] -= take

    bench_used = sum(leftovers.values())
    rows = []
    for p in POS_ORDER:
        rows.append({"Slot": p, "Have": have[p], "Need": need[p],
                     "Fill": f"{filled[p]}/{need[p]}",
                     "Status": "✅" if filled[p] >= need[p] else f"need {need[p]-filled[p]}"})
    rows.append({"Slot": "FLEX", "Have": flex_used, "Need": flex_need,
                 "Fill": f"{flex_used}/{flex_need}",
                 "Status": "✅" if flex_used >= flex_need else f"need {flex_need-flex_used}"})
    rows.append({"Slot": "BENCH", "Have": bench_used, "Need": LEAGUE.bench,
                 "Fill": f"{bench_used}/{LEAGUE.bench}", "Status": ""})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
def auction_board(df: pd.DataFrame):
    draft = _draft_state(df.index)

    my_mask = draft["Mine"] & draft["Drafted"]
    my_spent = float((draft["Paid"] * my_mask).sum())
    my_count = int(my_mask.sum())
    slots_left = max(LEAGUE.roster_size - my_count, 0)
    my_left = LEAGUE.budget - my_spent
    max_bid = my_left - max(slots_left - 1, 0)

    m = st.columns(4)
    m[0].metric("My budget", f"${my_left:,.0f}", f"of ${LEAGUE.budget}")
    m[1].metric("Max bid", f"${max_bid:,.0f}")
    m[2].metric("My roster", f"{my_count}/{LEAGUE.roster_size}", f"{slots_left} open")
    m[3].metric("$/open slot", f"${(my_left/slots_left if slots_left else 0):,.0f}")

    left, right = st.columns([3, 1])
    with right:
        st.caption("**My roster**")
        my_positions = list(df.loc[my_mask[my_mask].index, "Pos"]) if my_count else []
        st.dataframe(roster_status(my_positions), hide_index=True, width="stretch")

    with left:
        f = st.columns([2, 3, 2])
        pos = f[0].selectbox("Position", ["ALL"] + POS_ORDER)
        query = f[1].text_input("Search player")
        hide_drafted = f[2].checkbox("Hide drafted", value=True)

        view = df.copy()
        view = view.join(draft)
        if pos != "ALL":
            view = view[view["Pos"] == pos]
        if query:
            view = view[view["Player"].str.contains(query, case=False, na=False)]
        if hide_drafted:
            view = view[~view["Drafted"]]
        view = view.sort_values("Value $", ascending=False)

        st.caption("Tick **Drafted**, set **Paid**, tick **Mine** for your picks.")
        edited = st.data_editor(
            view, width="stretch", hide_index=True, height=520,
            column_order=["Player", "Pos", "PosRk", "Team", "Proj", "Pts/G",
                          "VORP", "VOLS", "Value $", "V/$", "Tier",
                          "Drafted", "Paid", "Mine"],
            column_config={
                "Player": st.column_config.TextColumn(disabled=True),
                "Pos": st.column_config.TextColumn(disabled=True),
                "PosRk": st.column_config.NumberColumn("Rk", disabled=True,
                    help="Rank within position"),
                "Team": st.column_config.TextColumn(disabled=True),
                "Proj": st.column_config.NumberColumn(disabled=True, format="%.1f",
                    help="Projected season fantasy points"),
                "Pts/G": st.column_config.NumberColumn(disabled=True, format="%.1f"),
                "VORP": st.column_config.NumberColumn(disabled=True, format="%.1f",
                    help="Value over replacement (first non-starter)"),
                "VOLS": st.column_config.NumberColumn(disabled=True, format="%.1f",
                    help="Value over last starter"),
                "Value $": st.column_config.NumberColumn(disabled=True, format="$%.1f"),
                "V/$": st.column_config.NumberColumn(disabled=True, format="%.2f",
                    help="VORP per dollar — the bargain signal"),
                "Tier": st.column_config.NumberColumn(disabled=True),
                "Drafted": st.column_config.CheckboxColumn(),
                "Paid": st.column_config.NumberColumn(min_value=0, format="$%d"),
                "Mine": st.column_config.CheckboxColumn(),
            },
            key="board_editor",
        )
        st.session_state.draft_state.update(edited[["Drafted", "Mine", "Paid"]])


def analysis_tab(df: pd.DataFrame, valued):
    st.subheader("Budget allocation & scarcity")
    st.caption(f"For a {LEAGUE.teams}-team, ${LEAGUE.budget} league. "
               "Per-team spend is what the market *should* pay at each position.")

    summary = positional_summary(valued, LEAGUE)
    alloc = pd.DataFrame([
        {"Pos": s.position, "Startable": s.starters, "In tier 1": s.tier1,
         "$/team": s.per_team_spend, "% of budget": s.budget_share,
         "Repl. pts": s.replacement_pts, "Starter cliff": s.starter_cliff}
        for s in summary
    ])

    c1, c2 = st.columns([3, 2])
    with c1:
        st.dataframe(
            alloc, hide_index=True, width="stretch",
            column_config={
                "$/team": st.column_config.NumberColumn(format="$%.0f"),
                "% of budget": st.column_config.ProgressColumn(
                    format="%.0f%%", min_value=0, max_value=0.45),
                "Starter cliff": st.column_config.NumberColumn(format="%.0f",
                    help="Points between the last starter and replacement — "
                         "bigger = scarcer, pay up"),
            },
        )
    with c2:
        chart = (
            alt.Chart(alloc).mark_arc(innerRadius=55)
            .encode(theta="$/team:Q", color=alt.Color("Pos:N", sort=POS_ORDER),
                    tooltip=["Pos", "$/team", "% of budget"])
            .properties(height=240, title="Recommended spend by position")
        )
        st.altair_chart(chart, width="stretch")

    st.divider()
    st.subheader("💎 Bargains — most VORP per dollar")
    st.caption("Where to find points late: high value-over-replacement for the price.")
    bargains = (df[df["Value $"] >= 1]
                .sort_values("V/$", ascending=False)
                .head(20)[["Player", "Pos", "PosRk", "Proj", "VORP", "Value $", "V/$", "Tier"]])
    st.dataframe(bargains, hide_index=True, width="stretch",
                 column_config={
                     "Value $": st.column_config.NumberColumn(format="$%.0f"),
                     "V/$": st.column_config.NumberColumn(format="%.2f"),
                 })


def tiers_tab(df: pd.DataFrame):
    st.subheader("Tier breaks by position")
    pos = st.selectbox("Position", POS_ORDER, key="tier_pos")
    sub = df[df["Pos"] == pos].sort_values("Value $", ascending=False).reset_index()
    if sub.empty:
        st.info("No players for this position.")
        return
    chart = (
        alt.Chart(sub).mark_bar().encode(
            x=alt.X("Value $:Q", title="Auction value ($)"),
            y=alt.Y("Player:N", sort="-x", title=None),
            color=alt.Color("Tier:N", title="Tier",
                            scale=alt.Scale(scheme="tableau10")),
            tooltip=["Player", "Team", "Proj", "VORP", "Value $", "Tier"],
        ).properties(height=max(300, 24 * len(sub)))
    )
    st.altair_chart(chart, width="stretch")


def diagnostics_tab(diag, projections):
    st.subheader("Data diagnostics")
    c = st.columns(3)
    c[0].metric("Source requested", diag.source_requested)
    c[1].metric("Source used", diag.source_used)
    c[2].metric("Players loaded", len(projections))

    if "fallback" in diag.source_used:
        st.warning("Live data was unavailable, so SAMPLE data is shown. "
                   "Run `python tools/preview_live.py` on open internet to debug.")
    for note in diag.notes:
        st.info(note)
    for err in diag.errors:
        st.error(err)

    if diag.source_counts:
        st.write("**Players pulled per API** — confirms which source is live")
        st.dataframe(pd.DataFrame(sorted(diag.source_counts.items()),
                                  columns=["Source", "Players"]),
                     hide_index=True, width="stretch")

    st.write("**Coverage by position**")
    st.dataframe(pd.DataFrame(sorted(diag.counts.items()),
                              columns=["Position", "Players"]),
                 hide_index=True, width="stretch")

    if diag.unmapped:
        st.write("**Unmapped stat keys** — review whether any affect scoring:")
        for src, keys in diag.unmapped.items():
            st.warning(f"{src}: {', '.join(sorted(keys))}")
    else:
        st.success("Every stat key the source sent was mapped.")

    with st.expander("Scoring rules in effect"):
        st.dataframe(pd.DataFrame(sorted(DEFAULT_SCORING.items()),
                                  columns=["Stat", "Points"]),
                     hide_index=True, width="stretch")
        st.caption("DST points-allowed tiers (per game): "
                   + ", ".join(f"{lo}-{hi}: {pts:g}" for lo, hi, pts
                               in DEFAULT_DST_PA_TIERS))


def glossary():
    """A legend at the bottom of the page explaining every term/column."""
    st.divider()
    st.subheader("📖 Glossary — what each term means")
    st.markdown(
        "**Player columns**\n\n"
        "| Term | Meaning |\n"
        "| --- | --- |\n"
        "| **Proj** | Projected total fantasy points for the season, under your custom scoring. |\n"
        "| **Pts/G** | Projected points per game (Proj ÷ 17). |\n"
        "| **Rk** | Rank within the position by projected points (e.g. `RB3` = 3rd-best RB). |\n"
        "| **VORP** | *Value Over Replacement* — points above the best player who **won't** start in this 16-team league. This is what drives the price. |\n"
        "| **VOLS** | *Value Over Last Starter* — points above the **worst** starter at the position (a stricter scarcity check than VORP). |\n"
        "| **Value $** | Recommended auction price — VORP turned into dollars so every team's values sum to the $3,200 pool (16 × $200). |\n"
        "| **V/$** | VORP bought per dollar — the bargain signal. Higher = more points-over-replacement for the money. |\n"
        "| **Tier** | Players grouped by natural drop-offs in value. A tier break is a cliff — act before a tier empties out. |\n"
        "| **Starter** | Projected to fill a weekly starting slot (including FLEX) somewhere in the league. |\n\n"
        "**Draft tracker (top of the board)**\n\n"
        "| Term | Meaning |\n"
        "| --- | --- |\n"
        "| **My budget** | Your dollars left of the $200 cap. |\n"
        "| **Max bid** | The most you can bid right now and still keep $1 for every empty roster spot. |\n"
        "| **My roster** | Roster spots filled, out of 15. |\n"
        "| **$/open slot** | Money left ÷ open spots — your average room per remaining pick. |\n\n"
        "**Data**\n\n"
        "| Term | Meaning |\n"
        "| --- | --- |\n"
        "| **Snapshot** | Projections from committed files — ESPN offense + Sleeper kickers/defenses — refreshed by the monthly Action. No live API needed at draft time. |\n"
        "| **VORP / VOLS replacement** | Replacement = the first non-starter at a position; the last starter is the worst projected starter. Both depend on the 16-team roster. |\n"
    )


def main():
    st.title("🏈 My Auction War Room")
    st.caption(f"Personal tool · **{LEAGUE.teams} teams · ${LEAGUE.budget} "
               f"budget** · {LEAGUE.roster_size}-man rosters · full PPR")

    with st.sidebar:
        st.header("Data")
        source = st.selectbox("Projections from", list(SOURCE_LABELS),
                              format_func=lambda s: SOURCE_LABELS[s])
        if st.button("↻ Reload data", width="stretch"):
            load_projections.clear()
        st.divider()
        st.subheader("League (locked)")
        st.markdown(
            f"- **{LEAGUE.teams}** teams · **${LEAGUE.budget}** budget · **{SEASON}** season\n"
            "- Start: QB·RB·RB·WR·WR·TE·FLEX·K·DST\n"
            f"- Bench: {LEAGUE.bench} · Scoring: full PPR\n"
            "- Edit rules in `scoring.py` / `app.py`"
        )
        st.caption(f"build {APP_BUILD}")

    projections, diag = load_projections(source, SEASON)
    valued = compute_values(projections, Scoring(), LEAGUE)
    df = build_dataframe(valued)

    if "fallback" in diag.source_used or diag.errors:
        st.warning(f"Showing **{diag.source_used}** data — see the Diagnostics tab.")

    board, analysis, tiers, diagnostics = st.tabs(
        ["💰 Auction Board", "📈 Analysis", "📊 Tiers", "🔎 Diagnostics"]
    )
    with board:
        auction_board(df)
    with analysis:
        analysis_tab(df, valued)
    with tiers:
        tiers_tab(df)
    with diagnostics:
        diagnostics_tab(diag, projections)

    glossary()


if __name__ == "__main__":
    main()
