"""League #2 — 8-team · 2QB · full-PPR snake draft board (Streamlit page).

This page is **read-only against a pre-built file**: the GitHub Action scrapes
the five sources, writes ``data/league2_projections.csv``, and commits it; this
page just reads that CSV.  Nothing here touches the network, so it works on
Streamlit Cloud / Pages where the sources would 403.

It's a sibling page to the dynasty auction app (``app.py``); Streamlit's page
nav in the sidebar is the league switcher.
"""

from __future__ import annotations

import json
import os

import altair as alt
import pandas as pd
import streamlit as st

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(ROOT, "data", "league2_projections.csv")
META_PATH = os.path.join(ROOT, "data", "league2_projections.meta.json")

POS_ORDER = ["QB", "RB", "WR", "TE", "K", "DEF"]
# League structural facts (kept in sync with league2/scoring.py LEAGUE_CONFIG).
ROSTER = {"QB": 2, "RB": 2, "WR": 3, "FLEX": 3, "K": 1, "DEF": 1}
TEAMS = 8

# Categorical tier palette (tier is computed from VORP gaps per position).
_TIER_COLORS = ["#1b9e77", "#7570b3", "#d95f02", "#e7298a", "#66a61e",
                "#a6761d", "#666666"]

st.set_page_config(page_title="League 2 — Snake Draft", layout="wide")


@st.cache_data(show_spinner="Loading the draft board…")
def load_board():
    df = pd.read_csv(CSV_PATH)
    meta = {}
    if os.path.exists(META_PATH):
        with open(META_PATH, encoding="utf-8") as fh:
            meta = json.load(fh)
    return df, meta


def _confidence(n: int) -> str:
    return str(int(n))


def _tier_style(val):
    try:
        c = _TIER_COLORS[(int(val) - 1) % len(_TIER_COLORS)]
    except (ValueError, TypeError):
        return ""
    return f"background-color: {c}; color: white; font-weight: 600;"


def _conf_style(val):
    try:
        thin = int(val) <= 2
    except (ValueError, TypeError):
        return ""
    return "background-color: #ffe08a;" if thin else ""


def _vva_style(val):
    if pd.isna(val):
        return "color: #999;"
    if val > 0:
        return "color: #1a7f37; font-weight: 600;"   # market sleeps -> value
    if val < 0:
        return "color: #b00020;"                     # market reaches -> caution
    return ""


# ---------------------------------------------------------------------------
def provenance_banner(meta: dict):
    if not meta:
        return
    when = meta.get("generated_at", "unknown")
    if meta.get("fallback_to_sample"):
        st.warning(
            f"Showing **bundled sample data** (built {when}) — the live sources "
            "returned nothing on the last refresh (they 403 datacenter IPs). "
            "Run `python scripts/scrape_league2_projections.py` on open internet "
            "to populate real projections.")
    else:
        counts = meta.get("counts", {})
        cov = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        st.caption(f"Live projections · built {when} · rows per source: {cov or 'n/a'}")
    if meta.get("errors"):
        with st.expander(f"{len(meta['errors'])} source error(s) on last refresh"):
            for e in meta["errors"]:
                st.text(e)


def board_tab(df: pd.DataFrame):
    c = st.columns([2, 3, 2, 2])
    pos = c[0].selectbox("Position", ["ALL"] + POS_ORDER)
    query = c[1].text_input("Search player")
    min_src = c[2].select_slider("Min sources", options=[1, 2, 3, 4], value=1,
                                 help="Hide players covered by fewer than N "
                                      "sources — the low-confidence rookies/backups.")
    hide_special = c[3].checkbox("Hide K/DEF", value=False)

    view = df.copy()
    if pos != "ALL":
        view = view[view["pos"] == pos]
    if query:
        view = view[view["name"].str.contains(query, case=False, na=False)]
    view = view[view["n_sources"] >= min_src]
    if hide_special:
        view = view[~view["pos"].isin(["K", "DEF"])]

    view = view.sort_values("vorp", ascending=False)
    disp = pd.DataFrame({
        "Rk": view["overall_rank"],
        "Player": view["name"],
        "Pos": view["pos"],
        "PosRk": view["pos"] + view["pos_rank"].astype(str),
        "Team": view["team"],
        "Tier": view["tier"],
        "Proj": view["agg_points"].round(1),
        "VORP": view["vorp"].round(1),
        "Conf": view["n_sources"].map(_confidence),
        "ADP": view["market_adp"],
        "Val vs ADP": view["value_vs_adp"],
    })

    st.caption("Sorted by **VORP**. **Tier** breaks are VORP cliffs (act before a "
               "tier empties). **Val vs ADP** > 0 = the market lets him slide past "
               "your rank (a value); < 0 = you'd reach vs the market.")
    styler = (
        disp.style
        .map(_tier_style, subset=["Tier"])
        .map(_conf_style, subset=["Conf"])
        .map(_vva_style, subset=["Val vs ADP"])
        .format({"Proj": "{:.1f}", "VORP": "{:+.1f}", "ADP": "{:.0f}",
                 "Val vs ADP": "{:+.0f}"}, na_rep="—")
    )
    st.dataframe(styler, hide_index=True, height=560, width="stretch")
    st.caption(f"{len(disp)} players shown. Conf = number of sources "
               "(4 = all; 1–2 = thin coverage, highlighted — verify "
               "before trusting).")


def tiers_tab(df: pd.DataFrame):
    st.subheader("Positional tiers by VORP")
    st.caption("The bar length is VORP; color is tier. Gaps between colors are "
               "the draft-day cliffs — note how QB stays valuable deep (2QB) "
               "while TE value dries up fast (FLEX-only).")
    pos = st.selectbox("Position", POS_ORDER, key="l2_tier_pos")
    sub = df[df["pos"] == pos].sort_values("vorp", ascending=False).head(30)
    if sub.empty:
        st.info("No players for this position.")
        return
    chart = (
        alt.Chart(sub).mark_bar().encode(
            x=alt.X("vorp:Q", title="VORP (points over replacement)"),
            y=alt.Y("name:N", sort="-x", title=None),
            color=alt.Color("tier:N", title="Tier",
                            scale=alt.Scale(scheme="tableau10")),
            tooltip=["name", "team", "agg_points", "vorp", "tier", "n_sources"],
        ).properties(height=max(300, 22 * len(sub)))
    )
    st.altair_chart(chart, width="stretch")


def market_tab(df: pd.DataFrame):
    st.subheader("Model vs market (ADP cross-check)")
    st.caption("Where your VORP board and the live mock-draft market most "
               "disagree. Investigate before trusting either side.")
    have_adp = df[df["market_adp"].notna()].copy()
    have_adp["Val vs ADP"] = have_adp["value_vs_adp"]

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Values** — market lets them slide past your rank")
        val = have_adp.sort_values("value_vs_adp", ascending=False).head(15)
        st.dataframe(val[["name", "pos", "overall_rank", "market_adp", "Val vs ADP"]],
                     hide_index=True, width="stretch",
                     column_config={"overall_rank": "Your Rk", "market_adp": "ADP"})
    with c2:
        st.markdown("**Reaches** — market drafts them well above your rank")
        rea = have_adp.sort_values("value_vs_adp").head(15)
        st.dataframe(rea[["name", "pos", "overall_rank", "market_adp", "Val vs ADP"]],
                     hide_index=True, width="stretch",
                     column_config={"overall_rank": "Your Rk", "market_adp": "ADP"})


def data_tab(df: pd.DataFrame, meta: dict):
    st.subheader("Data & coverage")
    c = st.columns(3)
    c[0].metric("Players", len(df))
    c[1].metric("Built", meta.get("generated_at", "—")[:10] or "—")
    c[2].metric("Mode", "sample" if meta.get("fallback_to_sample") else "live")

    st.write("**Coverage by position**")
    cov = df.groupby("pos").size().reindex(POS_ORDER).fillna(0).astype(int)
    st.dataframe(cov.rename("players").reset_index().rename(columns={"pos": "Pos"}),
                 hide_index=True, width="stretch")

    st.write("**Source confidence (n_sources)** — thin coverage = trust less")
    dist = df["n_sources"].value_counts().sort_index()
    st.dataframe(dist.rename("players").reset_index().rename(
        columns={"n_sources": "# sources"}), hide_index=True, width="stretch")

    src_cols = [c for c in df.columns if c.startswith("pts_")]
    if src_cols:
        st.caption("Per-source point columns in the CSV: " + ", ".join(src_cols))


def main():
    st.title("League 2 — 8-Team 2QB PPR Snake Draft")
    st.caption("Ranked VORP board (not auction $). Starters/team: "
               "**2QB · 2RB · 3WR · 3FLEX · 1K · 1DEF** — full PPR. "
               "Use the sidebar page nav to switch to the auction league.")

    if not os.path.exists(CSV_PATH):
        st.error("No board file yet. Run "
                 "`python scripts/scrape_league2_projections.py` (or "
                 "`--sample`) to build `data/league2_projections.csv`.")
        st.stop()

    df, meta = load_board()
    provenance_banner(meta)

    with st.sidebar:
        st.header("League 2 (locked)")
        st.markdown(
            f"- **{TEAMS}** teams · **snake** · full PPR\n"
            "- Start: 2QB · 2RB · 3WR · 3FLEX · 1K · 1DEF\n"
            "- FLEX = RB/WR/TE (no dedicated TE slot)\n"
            "- 16 of 96 starters are **QB** → QB2s have real value\n"
            "- Edit rules in `league2/scoring.py`")
        if st.button("Reload board", width="stretch"):
            load_board.clear()
            st.rerun()

    with st.expander("Why this board looks different from a 1-QB league"):
        st.markdown(
            "- **2 QB starters × 8 teams = 16 QB starting spots**, so QB "
            "replacement level sits at ~QB17 — backup-caliber QBs still beat "
            "replacement, so they carry real draft value.\n"
            "- **No dedicated TE slot**: tight ends only start through FLEX, "
            "competing with RB/WR. Replacement TE is very low, so only the "
            "elite few clear it — everyone else is roughly replacement-level.\n"
            "- Points are **recomputed from raw stats** through one shared PPR "
            "formula for every source, then **median**-aggregated, so no single "
            "site's scoring quirk skews a player.")

    board, tiers, market, data = st.tabs(
        ["Draft Board", "Tiers", "Model vs Market", "Data"])
    with board:
        board_tab(df)
    with tiers:
        tiers_tab(df)
    with market:
        market_tab(df)
    with data:
        data_tab(df, meta)


if __name__ == "__main__":
    main()
