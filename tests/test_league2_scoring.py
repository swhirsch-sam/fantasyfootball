"""League #2 scoring + identity normalization (hand-computed expectations)."""

from league2.scoring import (
    LEAGUE_CONFIG,
    PPR_SCORING,
    normalize_name,
    normalize_position,
    score_player,
)


def test_qb_line_scored_from_raw_stats():
    stats = {"pass_yds": 4000, "pass_td": 30, "int": 10,
             "rush_yds": 200, "rush_td": 2, "fumbles_lost": 3}
    # 160 + 120 - 20 + 20 + 12 - 6
    assert score_player(stats) == 286.0


def test_full_ppr_reception_point():
    # 100 rec * 1 + 1400 yds * 0.1 + 10 td * 6
    assert score_player({"rec": 100, "rec_yds": 1400, "rec_td": 10}) == 300.0


def test_missing_keys_default_to_zero():
    # a WR line has no passing stats; they simply don't contribute
    assert score_player({"rec": 50, "rec_yds": 500}) == 100.0


def test_turnovers_are_negative():
    assert score_player({"int": 3, "fumbles_lost": 2}) == -10.0


def test_custom_scoring_overrides_default():
    half = dict(PPR_SCORING, rec=0.5)
    assert score_player({"rec": 10, "rec_yds": 100}, half) == 15.0


def test_normalize_name_strips_suffix_accent_punct():
    assert normalize_name("Michael Penix Jr.") == "michael penix"
    assert normalize_name("Amon-Ra St. Brown") == "amonra st brown"
    assert normalize_name("Patrick Mahomes II") == "patrick mahomes"
    assert normalize_name("Kenneth Walker III") == "kenneth walker"


def test_normalize_name_collapses_cross_source_formats():
    # the whole point of the join key: differing site formats must collapse
    assert normalize_name("D'Andre Swift") == normalize_name("DAndre Swift")
    assert normalize_name("A.J. Brown") == normalize_name("AJ Brown")


def test_normalize_position_aliases_and_rank_suffix():
    assert normalize_position("DST") == "DEF"
    assert normalize_position("D/ST") == "DEF"
    assert normalize_position("PK") == "K"
    assert normalize_position("RB12") == "RB"   # ADP pages append a rank
    assert normalize_position("qb") == "QB"


def test_league_config_is_2qb_and_flex_excludes_qb():
    assert LEAGUE_CONFIG["teams"] == 8
    assert LEAGUE_CONFIG["roster"]["QB"] == 2          # the headline structural fact
    assert "TE" in LEAGUE_CONFIG["flex_eligible"]
    assert "QB" not in LEAGUE_CONFIG["flex_eligible"]  # not a superflex
    assert LEAGUE_CONFIG["roster"]["TE"] == 1            # one dedicated TE slot
