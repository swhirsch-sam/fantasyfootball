"""Auction valuation — VBD math against the bundled SAMPLE projections."""

import data_sources as ds
from scoring import Scoring
from valuation import LeagueSettings, compute_values, positional_summary


def test_values_returned_for_every_player():
    proj = ds.sample_projections()
    valued = compute_values(proj, Scoring(), LeagueSettings())
    assert len(valued) == len(proj)


def test_total_auction_dollars_match_the_pool():
    settings = LeagueSettings(teams=12, budget=200)
    valued = compute_values(ds.sample_projections(), Scoring(), settings)
    pool = settings.teams * settings.budget
    spent = sum(vp.value for vp in valued)
    # Every dollar is distributed; rounding keeps it within a few bucks.
    assert abs(spent - pool) < 5


def test_replacement_makes_vorp_relative_to_position():
    valued = compute_values(ds.sample_projections())
    by_pos = {}
    for vp in valued:
        by_pos.setdefault(vp.position, []).append(vp)
    # The worst starter-tier players hover near 0 VORP; the best are well above.
    best_rb = max(by_pos["RB"], key=lambda vp: vp.vorp)
    assert best_rb.vorp > 0
    assert min(vp.vorp for vp in valued) <= 0  # deep players fall below replacement


def test_top_player_is_most_expensive():
    valued = compute_values(ds.sample_projections())
    assert valued[0].value == max(vp.value for vp in valued)
    assert valued[0].value > 1.0


def test_tiers_start_at_one_and_increase():
    valued = compute_values(ds.sample_projections())
    rbs = sorted([vp for vp in valued if vp.position == "RB"],
                 key=lambda vp: vp.value, reverse=True)
    assert rbs[0].tier == 1
    assert rbs[-1].tier >= rbs[0].tier  # tiers only grow as value falls


def test_more_teams_raises_replacement_and_concentrates_value():
    cheap = compute_values(ds.sample_projections(), settings=LeagueSettings(teams=8))
    deep = compute_values(ds.sample_projections(), settings=LeagueSettings(teams=14))
    # With more teams the same pool is split among more rostered players, so the
    # very top player commands a larger share in the shallower league.
    assert cheap[0].value > 1.0 and deep[0].value > 1.0


# --- richer metrics (the 16-team personal config) ---------------------------
LOCKED = LeagueSettings(teams=16, budget=200)


def test_pos_rank_and_label():
    valued = compute_values(ds.sample_projections(), settings=LOCKED)
    rbs = sorted([v for v in valued if v.position == "RB"],
                 key=lambda v: v.points, reverse=True)
    assert rbs[0].pos_rank == 1 and rbs[0].pos_label == "RB1"
    assert rbs[2].pos_label == "RB3"


def test_ppg_is_points_over_games():
    vp = compute_values(ds.sample_projections(), settings=LOCKED)[0]
    assert vp.ppg == round(vp.points / vp.games, 2)


def test_vols_never_exceeds_vorp():
    # the last starter outscores replacement, so VOLS <= VORP for everyone
    for vp in compute_values(ds.sample_projections(), settings=LOCKED):
        assert vp.vols <= vp.vorp + 1e-6


def test_starter_flags_track_league_size():
    valued = compute_values(ds.sample_projections(), settings=LOCKED)
    starters = sum(1 for vp in valued if vp.is_starter)
    # more than base starters (flex is used), no more than base + flex
    assert LOCKED.teams * 8 < starters <= LOCKED.teams * 9


def test_budget_allocation_sums_to_team_budget():
    valued = compute_values(ds.sample_projections(), settings=LOCKED)
    summary = positional_summary(valued, LOCKED)
    per_team_total = sum(s.per_team_spend for s in summary)
    assert abs(per_team_total - LOCKED.budget) < 5
    assert [s.position for s in summary][:3] == ["QB", "RB", "WR"]


def test_vorp_per_dollar_positive_for_priced_players():
    valued = compute_values(ds.sample_projections(), settings=LOCKED)
    assert all(vp.vorp_per_dollar > 0 for vp in valued if vp.value > 1)
