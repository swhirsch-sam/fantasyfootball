"""Data-source parsers — the live mapping made verifiable offline.

These push realistic ESPN / Sleeper payloads straight through the pure
parsers, so the stat mapping is checked without ever touching the network.
"""

import data_sources as ds


# --- ESPN -------------------------------------------------------------------
ESPN_PAYLOAD = {
    "players": [
        {
            "id": 3139477,
            "player": {
                "id": 3139477,
                "fullName": "Patrick Mahomes",
                "defaultPositionId": 1,
                "proTeamId": 12,
                "stats": [
                    {  # projected season split -> the one we want
                        "seasonId": 2026, "statSourceId": 1, "statSplitTypeId": 0,
                        "stats": {"3": 4800, "4": 38, "20": 10, "24": 250, "25": 2,
                                  "999": 7},
                    },
                    {  # actual stats -> must be ignored
                        "seasonId": 2025, "statSourceId": 0, "statSplitTypeId": 0,
                        "stats": {"3": 4183, "4": 26},
                    },
                ],
            },
        },
        {
            "id": 4262921,
            "player": {
                "id": 4262921,
                "fullName": "Bijan Robinson",
                "defaultPositionId": 2,
                "proTeamId": 1,
                "stats": [
                    {"seasonId": 2026, "statSourceId": 1, "statSplitTypeId": 0,
                     "stats": {"24": 1350, "25": 11, "53": 62, "42": 520, "43": 3,
                               "72": 2}},
                ],
            },
        },
    ]
}


def test_espn_stat_map_covers_canonical_offense():
    expected = {"3": "pass_yd", "4": "pass_td", "20": "pass_int", "24": "rush_yd",
                "25": "rush_td", "53": "rec", "42": "rec_yd", "43": "rec_td",
                "72": "fum_lost", "19": "pass_2pt", "26": "rush_2pt", "44": "rec_2pt"}
    for stat_id, key in expected.items():
        assert ds.ESPN_STAT_MAP[stat_id] == key


def test_parse_espn_basic_fields_and_mapping():
    projections, _ = ds.parse_espn_players(ESPN_PAYLOAD, season=2026)
    mahomes = next(p for p in projections if p.name == "Patrick Mahomes")
    assert mahomes.position == "QB"
    assert mahomes.team == "KC"
    assert mahomes.stats["pass_yd"] == 4800
    assert mahomes.stats["pass_td"] == 38
    assert mahomes.stats["pass_int"] == 10


def test_parse_espn_selects_projected_not_actual():
    projections, _ = ds.parse_espn_players(ESPN_PAYLOAD, season=2026)
    mahomes = next(p for p in projections if p.name == "Patrick Mahomes")
    # 4800 is the projection; 4183 is last year's actual and must not win.
    assert mahomes.stats["pass_yd"] == 4800


def test_parse_espn_flags_unmapped_ids():
    _, unmapped = ds.parse_espn_players(ESPN_PAYLOAD, season=2026)
    assert "999" in unmapped


# --- Sleeper ----------------------------------------------------------------
def _sleeper_entry(pid, pos, team, stats, first="First", last="Last"):
    return {
        "player_id": pid,
        "player": {"first_name": first, "last_name": last,
                   "position": pos, "team": team},
        "stats": stats,
    }


def test_parse_sleeper_week_basic():
    week = [_sleeper_entry("4046", "QB", "KC",
                           {"pass_yd": 280.5, "pass_td": 2.1, "pass_int": 0.6})]
    rows, _ = ds.parse_sleeper_week(week)
    assert rows[0]["player_id"] == "sleeper:4046"
    assert rows[0]["stats"]["pass_yd"] == 280.5
    assert rows[0]["position"] == "QB"


def test_sleeper_aggregates_across_weeks():
    w1 = [_sleeper_entry("1", "RB", "ATL", {"rush_yd": 80, "rush_td": 1, "rec": 4})]
    w2 = [_sleeper_entry("1", "RB", "ATL", {"rush_yd": 95, "rush_td": 0, "rec": 6})]
    proj, _ = ds.aggregate_sleeper_weeks([w1, w2])
    assert len(proj) == 1
    assert proj[0].stats["rush_yd"] == 175
    assert proj[0].stats["rush_td"] == 1
    assert proj[0].stats["rec"] == 10


def test_sleeper_missed_fg_buckets_collapse_to_fgmiss():
    week = [_sleeper_entry("k1", "K", "DAL",
                           {"fgmiss_30_39": 1, "fgmiss_40_49": 1, "fgmiss_50p": 2})]
    rows, _ = ds.parse_sleeper_week(week)
    assert rows[0]["stats"]["fgmiss"] == 4
    assert "fgmiss_30_39" not in rows[0]["stats"]


def test_sleeper_flags_genuinely_unmapped_keys():
    # fgmiss_0_19 does not exist in Sleeper; if it ever appeared it should be
    # surfaced rather than silently dropped.
    week = [_sleeper_entry("k1", "K", "DAL", {"fgmiss_0_19": 1})]
    _, unmapped = ds.parse_sleeper_week(week)
    assert "fgmiss_0_19" in unmapped


def test_sleeper_ignores_known_non_scoring_keys():
    week = [_sleeper_entry("1", "QB", "KC",
                           {"pass_yd": 250, "pass_att": 35, "cmp_pct": 0.68})]
    rows, unmapped = ds.parse_sleeper_week(week)
    assert "pass_att" not in unmapped and "cmp_pct" not in unmapped
    assert "pass_att" not in rows[0]["stats"]


def test_sleeper_defense_records_games_for_pts_allowed():
    w1 = [_sleeper_entry("KC", "DEF", "KC", {"pts_allow": 17, "sack": 3})]
    w2 = [_sleeper_entry("KC", "DEF", "KC", {"pts_allow": 21, "sack": 2})]
    proj, _ = ds.aggregate_sleeper_weeks([w1, w2])
    d = proj[0]
    assert d.position == "DST"
    assert d.stats["pts_allow"] == 38
    assert d.stats["games"] == 2  # so scoring recovers a 19/game average


# --- Blend ------------------------------------------------------------------
def test_blend_takes_offense_from_espn_and_special_from_sleeper():
    espn_proj, _ = ds.parse_espn_players(ESPN_PAYLOAD, season=2026)
    sleeper_special = [
        ds.Projection("sleeper:k", "Kicker One", "K", "DAL", {"fgm_40_49": 5}),
        ds.Projection("sleeper:d", "Some D", "DST", "PHI", {"sack": 40}),
        ds.Projection("sleeper:qb", "Ignore Me", "QB", "KC", {"pass_yd": 1}),
    ]
    blended = ds.blend_projections(espn_proj, sleeper_special)
    sources = {p.name: p.source for p in blended}
    assert sources["Patrick Mahomes"] == "blend:espn"
    assert sources["Kicker One"] == "blend:sleeper"
    assert sources["Some D"] == "blend:sleeper"
    # The Sleeper QB must not leak in — offense comes from ESPN only.
    assert "Ignore Me" not in sources


def test_blend_uses_sleeper_offense_when_espn_is_empty():
    # ESPN failed/empty -> the blend must still use Sleeper, not drop to nothing.
    sleeper = [
        ds.Projection("sleeper:qb", "Sleeper QB", "QB", "KC", {"pass_yd": 4000}),
        ds.Projection("sleeper:k", "Some K", "K", "DAL", {"fgm_40_49": 5}),
    ]
    blended = ds.blend_projections([], sleeper)
    sources = {p.name: p.source for p in blended}
    assert sources["Sleeper QB"] == "blend:sleeper"
    assert sources["Some K"] == "blend:sleeper"


def test_blend_uses_espn_special_when_sleeper_is_empty():
    espn = [
        ds.Projection("espn:qb", "ESPN QB", "QB", "KC", {"pass_yd": 4000}),
        ds.Projection("espn:dst", "ESPN D", "DST", "SF", {"sack": 40}),
    ]
    blended = ds.blend_projections(espn, [])
    sources = {p.name: p.source for p in blended}
    assert sources["ESPN QB"] == "blend:espn"
    assert sources["ESPN D"] == "blend:espn"  # K/DST falls back to ESPN


# --- SAMPLE + orchestration -------------------------------------------------
def test_sample_data_has_every_position():
    proj = ds.sample_projections()
    positions = {p.position for p in proj}
    assert {"QB", "RB", "WR", "TE", "K", "DST"} <= positions


def test_get_projections_sample_is_offline_and_diagnosed():
    proj, diag = ds.get_projections("sample")
    assert diag.source_used == "sample"
    assert diag.counts["QB"] >= 12
    assert not diag.errors


def test_snapshot_source_reads_committed_files(tmp_path):
    import json
    espn = {"players": [{"id": 1, "player": {
        "id": 1, "fullName": "Snap QB", "defaultPositionId": 1, "proTeamId": 12,
        "stats": [{"seasonId": 2026, "statSourceId": 1, "statSplitTypeId": 0,
                   "stats": {"3": 4000, "4": 30}}]}}]}
    sleeper = {"season": 2026, "weeks": [[
        {"player_id": "SF", "player": {"position": "DEF", "team": "SF"},
         "stats": {"pts_allow": 18, "sack": 3}}]]}
    (tmp_path / "espn_2026.json").write_text(json.dumps(espn))
    (tmp_path / "sleeper_2026.json").write_text(json.dumps(sleeper))

    proj, diag = ds.get_projections("snapshot", season=2026, data_dir=str(tmp_path))
    names = {p.name for p in proj}
    assert "Snap QB" in names                       # ESPN offense from snapshot
    assert any(p.position == "DST" for p in proj)   # Sleeper DST from snapshot
    assert diag.source_counts.get("espn", 0) >= 1
    assert diag.source_counts.get("sleeper", 0) >= 1
    assert "snapshot" in diag.source_used


def test_snapshot_source_falls_back_when_files_missing(tmp_path):
    proj, diag = ds.get_projections("snapshot", season=1999, data_dir=str(tmp_path))
    assert diag.source_used == "sample (fallback)"
    assert any("no ESPN snapshot" in n for n in diag.notes)
