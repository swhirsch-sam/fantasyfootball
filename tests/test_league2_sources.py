"""League #2 source normalizers — the column maps made verifiable offline.

These push DataFrames shaped like each site's scraped table straight through the
pure normalizers, so the mapping is checked without ever hitting a site (they
403 datacenter IPs).  The headline risk is ambiguous repeated headers ("YDS"
under both Passing and Rushing), so that disambiguation is tested explicitly.
"""

import pandas as pd

from league2 import sources as src


def test_pick_data_table_takes_the_largest():
    small = pd.DataFrame({"a": [1, 2]})
    big = pd.DataFrame({"a": range(10), "b": range(10)})
    assert src.pick_data_table([small, big]) is big


# --- Razzball: one combined table, columns documented in the brief -----------
def test_razzball_maps_raw_columns_for_each_position():
    df = pd.DataFrame(
        [["Josh Allen", "QB", "BUF", 380, 4000, 30, 10, 500, 6, 0, 0, 0],
         ["Ja'Marr Chase", "WR", "CIN", 350, 0, 0, 0, 0, 0, 110, 1500, 12]],
        columns=["Name", "Pos", "Team", "PPR PTS", "Pass Yds", "Pass TD", "Int",
                 "Rush Yds", "Run TD", "Rec", "Rec Yds", "Rec TD"],
    )
    long = src.normalize_razzball(df).set_index("name")
    qb = long.loc["Josh Allen"]
    assert qb.pos == "QB" and qb.team == "BUF"
    assert qb.pass_yds == 4000 and qb.rush_yds == 500 and qb["int"] == 10
    wr = long.loc["Ja'Marr Chase"]
    assert wr.rec == 110 and wr.rec_yds == 1500 and wr.rec_td == 12


# --- FantasyPros: nested (group, stat) headers -------------------------------
def _fp_qb():
    cols = pd.MultiIndex.from_tuples([
        ("Unnamed: 0", "Player"),
        ("Passing", "YDS"), ("Passing", "TDS"), ("Passing", "INTS"),
        ("Rushing", "YDS"), ("Rushing", "TDS"),
        ("MISC", "FL"), ("MISC", "FPTS"),
    ])
    return pd.DataFrame([["Josh Allen", 4000, 30, 10, 500, 6, 3, 380]], columns=cols)


def test_fantasypros_qb_disambiguates_passing_vs_rushing_yards():
    long = src.normalize_fantasypros(_fp_qb(), "qb").iloc[0]
    assert long.pos == "QB"
    # the whole point: "YDS" appears twice; group prefix keeps them distinct
    assert long.pass_yds == 4000
    assert long.rush_yds == 500
    assert long.pass_yds != long.rush_yds
    assert long["int"] == 10
    assert long.rush_td == 6
    assert long.fumbles_lost == 3   # nested under "MISC FL" -> trailing-token fallback


def test_fantasypros_rb_disambiguates_rushing_vs_receiving_yards():
    cols = pd.MultiIndex.from_tuples([
        ("", "Player"),
        ("Rushing", "ATT"), ("Rushing", "YDS"), ("Rushing", "TDS"),
        ("Receiving", "REC"), ("Receiving", "YDS"), ("Receiving", "TDS"),
        ("MISC", "FL"),
    ])
    df = pd.DataFrame([["Bijan Robinson", 300, 1400, 11, 60, 500, 3, 2]], columns=cols)
    long = src.normalize_fantasypros(df, "rb").iloc[0]
    assert long.rush_yds == 1400 and long.rec_yds == 500   # distinct YDS columns
    assert long.rec == 60 and long.rush_td == 11 and long.rec_td == 3
    assert long.fumbles_lost == 2


def test_fantasypros_kicker_uses_site_fpts_only():
    cols = pd.MultiIndex.from_tuples([("", "Player"), ("Scoring", "FPTS")])
    df = pd.DataFrame([["Brandon Aubrey", 175]], columns=cols)
    long = src.normalize_fantasypros(df, "k").iloc[0]
    assert long.pos == "K"
    assert long.fpts == 175
    assert long.get("pass_yds", 0) in (0, None) or pd.isna(long.get("pass_yds"))


# --- CBS / FFToday: tolerant synonym map -------------------------------------
def test_generic_synonyms_map_a_flat_rb_table():
    df = pd.DataFrame(
        [["Bijan Robinson", 1400, 11, 60, 500, 3, 2]],
        columns=["Player", "Rush Yds", "Rush TD", "Rec", "Rec Yds", "Rec TD", "FL"],
    )
    long = src.normalize_cbs(df, "RB").iloc[0]
    assert long.pos == "RB"
    assert long.rush_yds == 1400 and long.rush_td == 11
    assert long.rec == 60 and long.rec_yds == 500 and long.rec_td == 3
    assert long.fumbles_lost == 2


def test_generic_extracts_name_and_team_from_combined_cell():
    df = pd.DataFrame([["Bijan Robinson ATL RB", 60, 500]],
                      columns=["Player", "Rec", "Rec Yds"])
    long = src.normalize_fftoday(df, "RB").iloc[0]
    assert long["name"] == "Bijan Robinson"
    assert long.team == "ATL"
    assert long.pos == "RB"


# --- FFC ADP -----------------------------------------------------------------
def test_ffc_adp_extracts_name_pos_and_market_adp():
    df = pd.DataFrame(
        [[1, "Ja'Marr Chase", "WR", "CIN", 2.5],
         [2, "Josh Allen", "QB", "BUF", 1.2]],
        columns=["Pick", "Name", "Pos", "Team", "ADP"],
    )
    adp = src.parse_ffc_adp(df).set_index("name")
    assert adp.loc["Ja'Marr Chase", "pos"] == "WR"
    assert adp.loc["Ja'Marr Chase", "market_adp"] == 2.5
    assert adp.loc["Josh Allen", "pos"] == "QB"
