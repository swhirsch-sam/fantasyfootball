"""League #2 VORP — the 2QB-depth and FLEX-only-TE effects, made explicit."""

import pandas as pd

from league2.vorp import assign_tiers, compute_vorp, replacement_levels


def _pool():
    """A deep, predictable pool: points descend by 1 within each position."""
    rows = []

    def add(pos, n, top):
        for i in range(n):
            rows.append({"name": f"{pos}{i + 1}", "pos": pos,
                         "agg_points": top - i})

    add("QB", 30, 400)   # QB17 -> 384
    add("RB", 40, 350)
    add("WR", 50, 360)
    add("TE", 25, 300)
    add("K", 15, 150)
    add("DEF", 15, 140)
    # Two elite TEs that easily fill the dedicated TE slot.
    rows.append({"name": "EliteTE1", "pos": "TE", "agg_points": 372})
    rows.append({"name": "EliteTE2", "pos": "TE", "agg_points": 356})
    return pd.DataFrame(rows)


def test_qb_replacement_is_deep_in_2qb():
    board = compute_vorp(_pool())
    # 2 QB * 8 teams = 16 QB starters -> replacement is the 17th QB.
    reps, _ = replacement_levels(_pool().reset_index(drop=True))
    assert reps["QB"] == 384.0                      # points of the 17th QB
    qb16 = board[board.name == "QB16"].iloc[0]
    assert qb16.is_starter and qb16.vorp > 0        # a QB2 still beats replacement


def test_total_starters_match_the_lineup():
    board = compute_vorp(_pool())
    # dedicated 16+16+24+8+8+8 = 80, plus 3*8 = 24 FLEX -> 104 starters league-wide
    assert int(board.is_starter.sum()) == 104


def test_only_elite_tes_clear_a_flex_slot():
    board = compute_vorp(_pool())
    assert board[board.name == "EliteTE1"].iloc[0].is_starter
    assert board[board.name == "EliteTE2"].iloc[0].is_starter
    # a mid TE does not start: dedicated slot fills with the top 8 TEs only
    assert not board[board.name == "TE10"].iloc[0].is_starter
    reps, _ = replacement_levels(_pool().reset_index(drop=True))
    # TE replacement level remains below WR (value dries up fast after ~8)
    assert reps["TE"] < reps["WR"]


def test_vorp_is_points_minus_replacement():
    board = compute_vorp(_pool())
    row = board[board.name == "WR1"].iloc[0]
    assert row.vorp == round(row.agg_points - row.replacement_pts, 2)


def test_board_sorted_by_vorp_descending():
    board = compute_vorp(_pool())
    assert list(board.vorp) == sorted(board.vorp, reverse=True)


def test_assign_tiers_breaks_on_a_vorp_cliff():
    df = pd.DataFrame({
        "name": list("abcdef"),
        "pos": ["RB"] * 6,
        # tight cluster, then a big cliff before the last two
        "vorp": [50, 48, 46, 44, 10, 8],
    })
    tiers = assign_tiers(df).set_index("name")["tier"]
    assert tiers["a"] == 1
    assert tiers["e"] > tiers["d"]      # the cliff starts a new tier
    assert tiers["f"] == tiers["e"]     # back to a tight cluster
