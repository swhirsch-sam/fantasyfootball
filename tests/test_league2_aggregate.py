"""League #2 aggregation — scoring, median blend, n_sources, and the board."""

import pandas as pd

from league2.aggregate import (
    aggregate_sources,
    build_board,
    score_long_frame,
)


def _long():
    """Two stat sources for one WR (different name formats) + a kicker by FPTS."""
    return pd.DataFrame([
        {"name": "Ja'Marr Chase", "pos": "WR", "team": "CIN", "source": "cbs",
         "rec": 100, "rec_yds": 1400, "rec_td": 10},
        {"name": "JaMarr Chase", "pos": "WR", "team": "CIN", "source": "razzball",
         "rec": 110, "rec_yds": 1500, "rec_td": 12},
        {"name": "Brandon Aubrey", "pos": "K", "team": "DAL", "source": "cbs",
         "fpts": 170},
        {"name": "Brandon Aubrey", "pos": "K", "team": "DAL", "source": "fftoday",
         "fpts": 180},
    ])


def test_offense_scored_from_raw_special_from_fpts():
    scored = score_long_frame(_long())
    chase = scored[(scored.name == "Ja'Marr Chase") & (scored.source == "cbs")].iloc[0]
    assert chase.points == 300.0                 # 100 + 140 + 60, the shared formula
    k = scored[scored.name == "Brandon Aubrey"].iloc[0]
    assert k.points == 170.0                      # K uses the site FPTS directly


def test_aggregate_medians_across_sources_and_collapses_names():
    board = aggregate_sources(score_long_frame(_long()))
    chase = board[board.pos == "WR"].iloc[0]
    # "Ja'Marr Chase" and "JaMarr Chase" must join to one row...
    assert (board.pos == "WR").sum() == 1
    assert chase.n_sources == 2
    # ...with agg_points = median(cbs 300, razzball 332) = 316
    assert chase.agg_points == 316.0


def test_n_sources_counts_only_present_sources():
    board = aggregate_sources(score_long_frame(_long()))
    k = board[board.pos == "K"].iloc[0]
    assert k.n_sources == 2


def _alpha(i: int) -> str:
    """0,1,2 -> A,B,C... (digit-free, since normalize_name strips digits)."""
    s, i = "", i + 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _board_inputs():
    long_df = pd.DataFrame([
        {"name": f"WideRec {_alpha(i)}", "pos": "WR", "team": "X", "source": s,
         "rec": 100 - i, "rec_yds": 1000 - 10 * i, "rec_td": 8}
        for i in range(40) for s in ("cbs", "razzball", "fftoday", "fantasypros")
    ] + [
        {"name": f"Passer {_alpha(i)}", "pos": "QB", "team": "Y", "source": s,
         "pass_yds": 4500 - 50 * i, "pass_td": 35 - i, "int": 8}
        for i in range(20) for s in ("cbs", "razzball")
    ])
    adp = pd.DataFrame([
        {"name": "WideRec A", "pos": "WR", "market_adp": 12},  # market lets him slide
        {"name": "Passer A", "pos": "QB", "market_adp": 1},    # market reaches for him
    ])
    return long_df, adp


def test_build_board_ranks_by_vorp_and_has_expected_columns():
    long_df, adp = _board_inputs()
    board = build_board(long_df, adp)
    for col in ("overall_rank", "name", "pos", "vorp", "n_sources",
                "market_adp", "value_vs_adp", "tier", "pos_rank"):
        assert col in board.columns
    assert board.overall_rank.tolist() == list(range(1, len(board) + 1))
    assert list(board.vorp) == sorted(board.vorp, reverse=True)


def test_value_vs_adp_is_market_minus_your_rank():
    long_df, adp = _board_inputs()
    board = build_board(long_df, adp).set_index("name")
    wr0 = board.loc["WideRec A"]
    assert wr0.value_vs_adp == wr0.market_adp - wr0.overall_rank
    # the top WR is elite by VORP (rank ~1) but market_adp 12 -> a positive "value"
    assert wr0.value_vs_adp > 0


def test_players_without_adp_have_nan_value_vs_adp():
    long_df, adp = _board_inputs()
    board = build_board(long_df, adp)
    missing = board[board.market_adp.isna()]
    assert len(missing) > 0
    assert missing.value_vs_adp.isna().all()
