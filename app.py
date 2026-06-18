"""Dynasty auction draft tool — Streamlit front end.

Run with:  streamlit run app.py

Tabs
----
* Auction Board   live $ values, tiers, and a draft tracker (budget + inflation)
* Tiers           a per-position tier-break visual
* Data Diagnostics which source was used, coverage, and any unmapped stat keys
"""

from __future__ import annotations

import altair as alt  # ships with streamlit (its native charts depend on it)
import pandas as pd
import streamlit as st

import data_sources as ds
from scoring import DEFAULT_SCORING, Scoring
from valuation import DEFAULT_STARTERS, LeagueSettings, compute_values

st.set_page_config(page_title="Dynasty Auction Tool", page_icon="🏈", layout="wide")

SOURCE_LABELS = {
    "sample": "Sample (offline demo data)",
    "sleeper": "Sleeper (live)",
    "espn": "ESPN (live)",
    "blend": "Blend — ESPN offense + Sleeper K/DST (live)",
}


@st.cache_data(show_spinner="Loading projections…")
def load_projections(source: str, season: int):
    """Cached projection load. Returns (projections, diagnostics)."""
    return ds.get_projections(source, season=season)


def sidebar_settings():
    st.sidebar.header("League settings")
    teams = st.sidebar.number_input("Teams", 4, 20, 12, 1)
    budget = st.sidebar.number_input("Auction budget ($)", 50, 1000, 200, 10)

    st.sidebar.caption("Starting lineup")
    starters = {}
    cols = st.sidebar.columns(2)
    for i, (pos, default) in enumerate(DEFAULT_STARTERS.items()):
        with cols[i % 2]:
            starters[pos] = st.number_input(pos, 0, 5, default, 1, key=f"st_{pos}")
    bench = st.sidebar.number_input("Bench spots", 0, 20, 6, 1)

    st.sidebar.divider()
    st.sidebar.header("Data source")
    source = st.sidebar.selectbox(
        "Projections from",
        list(SOURCE_LABELS),
        format_func=lambda s: SOURCE_LABELS[s],
    )
    season = st.sidebar.number_input("Season", 2020, 2035, 2026, 1)
    if st.sidebar.button("↻ Reload data", width="stretch"):
        load_projections.clear()

    settings = LeagueSettings(
        teams=int(teams), budget=int(budget), starters=starters, bench=int(bench)
    )
    return settings, source, int(season)


def build_dataframe(valued):
    return pd.DataFrame(
        [
            {
                "id": vp.projection.player_id,
                "Player": vp.name,
                "Pos": vp.position,
                "Team": vp.team,
                "Proj Pts": round(vp.points, 1),
                "VORP": round(vp.vorp, 1),
                "Value $": vp.value,
                "Tier": vp.tier,
            }
            for vp in valued
        ]
    ).set_index("id")


def _draft_state(index) -> pd.DataFrame:
    """Persisted per-player draft state, aligned to the current player set."""
    state = st.session_state.get("draft_state")
    base = pd.DataFrame(
        {"Drafted": False, "Mine": False, "Paid": 0.0}, index=index
    )
    if state is not None:
        base.update(state.reindex(index))
        base["Drafted"] = base["Drafted"].astype(bool)
        base["Mine"] = base["Mine"].astype(bool)
    st.session_state.draft_state = base
    return base


def auction_board(df, settings):
    draft = _draft_state(df.index)

    # --- top-line metrics ---------------------------------------------------
    pool = settings.teams * settings.budget
    spent = float((draft["Paid"] * draft["Drafted"]).sum())
    my_mask = draft["Mine"] & draft["Drafted"]
    my_spent = float((draft["Paid"] * my_mask).sum())
    my_slots = int(my_mask.sum())
    slots_left = max(settings.roster_size - my_slots, 0)
    my_left = settings.budget - my_spent
    max_bid = my_left - max(slots_left - 1, 0)

    undrafted = df[~draft["Drafted"]]
    remaining_value = float(undrafted["Value $"].clip(lower=0).sum())
    remaining_pool = pool - spent
    inflation = remaining_pool / remaining_value if remaining_value > 0 else 1.0

    m = st.columns(4)
    m[0].metric("My budget left", f"${my_left:,.0f}", f"{slots_left} slots open")
    m[1].metric("Max bid", f"${max_bid:,.0f}")
    m[2].metric("League $ left", f"${remaining_pool:,.0f}")
    m[3].metric("Inflation", f"{inflation:.0%}",
                help="League dollars left ÷ remaining player value. "
                     ">100% means values are running hot.")

    # --- filters ------------------------------------------------------------
    f = st.columns([2, 3, 2])
    positions = ["ALL"] + sorted(df["Pos"].unique())
    pos = f[0].selectbox("Position", positions)
    query = f[1].text_input("Search player")
    hide_drafted = f[2].checkbox("Hide drafted", value=True)

    view = df.copy()
    view["Adj $"] = (view["Value $"] * inflation).round(1)
    view = view.join(draft)
    if pos != "ALL":
        view = view[view["Pos"] == pos]
    if query:
        view = view[view["Player"].str.contains(query, case=False, na=False)]
    if hide_drafted:
        view = view[~view["Drafted"]]
    view = view.sort_values("Value $", ascending=False)

    st.caption("Tick **Drafted**, set **Paid**, and tick **Mine** for your picks. "
               "Edits update budget and inflation live.")
    edited = st.data_editor(
        view,
        width="stretch",
        hide_index=True,
        height=520,
        column_order=["Player", "Pos", "Team", "Proj Pts", "VORP",
                      "Value $", "Adj $", "Tier", "Drafted", "Paid", "Mine"],
        column_config={
            "Player": st.column_config.TextColumn(disabled=True),
            "Pos": st.column_config.TextColumn(disabled=True),
            "Team": st.column_config.TextColumn(disabled=True),
            "Proj Pts": st.column_config.NumberColumn(disabled=True, format="%.1f"),
            "VORP": st.column_config.NumberColumn(disabled=True, format="%.1f"),
            "Value $": st.column_config.NumberColumn(disabled=True, format="$%.1f"),
            "Adj $": st.column_config.NumberColumn(
                disabled=True, format="$%.1f",
                help="Value adjusted for current draft inflation."),
            "Tier": st.column_config.NumberColumn(disabled=True),
            "Drafted": st.column_config.CheckboxColumn(),
            "Paid": st.column_config.NumberColumn(min_value=0, format="$%d"),
            "Mine": st.column_config.CheckboxColumn(),
        },
        key="board_editor",
    )

    # persist edits back to the canonical draft state
    changes = edited[["Drafted", "Mine", "Paid"]]
    st.session_state.draft_state.update(changes)


def tiers_tab(df):
    st.subheader("Tier breaks by position")
    pos = st.selectbox("Position", sorted(df["Pos"].unique()), key="tier_pos")
    sub = df[df["Pos"] == pos].sort_values("Value $", ascending=False).reset_index()
    if sub.empty:
        st.info("No players for this position.")
        return
    chart = (
        alt.Chart(sub)
        .mark_bar()
        .encode(
            x=alt.X("Value $:Q", title="Auction value ($)"),
            y=alt.Y("Player:N", sort="-x", title=None),
            color=alt.Color("Tier:N", title="Tier",
                            scale=alt.Scale(scheme="tableau10")),
            tooltip=["Player", "Team", "Proj Pts", "Value $", "Tier"],
        )
        .properties(height=max(300, 26 * len(sub)))
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


def main():
    st.title("🏈 Dynasty Auction Tool")
    settings, source, season = sidebar_settings()

    projections, diag = load_projections(source, season)
    valued = compute_values(projections, Scoring(), settings)
    df = build_dataframe(valued)

    if "fallback" in diag.source_used or diag.errors:
        st.warning(f"Showing **{diag.source_used}** data — see the Diagnostics tab.")

    board, tiers, diagnostics = st.tabs(
        ["💰 Auction Board", "📊 Tiers", "🔎 Data Diagnostics"]
    )
    with board:
        auction_board(df, settings)
    with tiers:
        tiers_tab(df)
    with diagnostics:
        diagnostics_tab(diag, projections)


if __name__ == "__main__":
    main()
