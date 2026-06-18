"""Scoring rules — exercised against hand-computed expectations."""

import pytest

from scoring import Scoring


@pytest.fixture
def s():
    return Scoring()


def test_passing_yards_and_touchdowns(s):
    # 300 yds * 0.04 = 12, 3 TD * 4 = 12
    assert s.score({"pass_yd": 300, "pass_td": 3}, "QB") == 24.0


def test_full_ppr_reception_value(s):
    # 8 rec * 1 + 100 yds * 0.1 + 1 TD * 6 = 8 + 10 + 6
    assert s.score({"rec": 8, "rec_yd": 100, "rec_td": 1}, "WR") == 24.0


def test_interceptions_and_fumbles_are_negative(s):
    assert s.score({"pass_int": 2, "fum_lost": 1}, "QB") == -6.0


def test_two_point_conversions(s):
    assert s.score({"pass_2pt": 1, "rush_2pt": 1, "rec_2pt": 1}) == 6.0


def test_kicker_distance_buckets(s):
    # 0-19:3, 20-29:3, 30-39:3, 40-49:4, 50+:5, xpm:1
    stats = {
        "fgm_0_19": 1, "fgm_20_29": 2, "fgm_30_39": 3,
        "fgm_40_49": 2, "fgm_50p": 1, "xpm": 30,
    }
    # 3 + 6 + 9 + 8 + 5 + 30 = 61
    assert s.score(stats, "K") == 61.0


def test_kicker_misses_are_penalized(s):
    assert s.score({"fgmiss": 2, "xpmiss": 1}, "K") == -3.0


@pytest.mark.parametrize(
    "per_game,expected",
    [(0, 10.0), (3, 7.0), (10, 4.0), (17, 1.0), (24, 0.0), (30, -1.0), (40, -4.0)],
)
def test_dst_points_allowed_tiers(s, per_game, expected):
    assert s.points_allowed_points(per_game) == expected


def test_dst_points_allowed_rounds_to_tier(s):
    # 13.6 rounds to 14 -> the 14-20 band (1 pt), not the 7-13 band
    assert s.points_allowed_points(13.6) == 1.0
    assert s.points_allowed_points(13.4) == 4.0


def test_dst_full_line_scales_by_games(s):
    # 340 pts over 17 games = 20/g -> tier(20)=1 -> 17 pts from PA.
    # plus 40 sack*1 + 14 int*2 + 9 fumrec*2 + 3 def_td*6 = 40+28+18+18 = 104
    stats = {
        "pts_allow": 340, "games": 17, "sack": 40, "int": 14,
        "fum_rec": 9, "def_td": 3,
    }
    assert s.score(stats, "DST") == pytest.approx(17 + 104)


def test_unknown_keys_are_ignored(s):
    assert s.score({"pass_yd": 100, "made_up_stat": 999}, "QB") == 4.0


def test_custom_weights_change_pricing():
    custom = Scoring(weights={"rec": 0.5, "rec_yd": 0.1})
    # half-PPR: 10 rec * 0.5 + 100 * 0.1 = 5 + 10
    assert custom.score({"rec": 10, "rec_yd": 100}, "WR") == 15.0
