"""The Interpret stage over one full match: totals agree with the raw feed, metrics stay in range."""

from __future__ import annotations

import numpy as np

from groundtruth.sim.summary import player_running


def test_goal_moments_match_goals(played):
    goals = [e for e in played["events"] if e.type == "goal"]
    assert len([m for m in played["moments"] if m.kind == "goal"]) == len(goals)


def test_team_totals_agree_with_events(played):
    stats, events = played["stats"], played["events"]
    for tid, ts in stats.teams.items():
        assert ts.passes == sum(e.type == "pass" and e.team_id == tid for e in events)
        assert ts.shots == sum(e.type == "shot" and e.team_id == tid for e in events)
        assert ts.goals == sum(e.type == "goal" and e.team_id == tid for e in events)


def test_every_pass_and_shot_is_enriched(played):
    stats = played["stats"]
    for e in played["events"]:
        if e.type == "pass":
            d = stats.derived[e.event_id]
            assert 0.0 <= d["difficulty"] <= 100.0
            assert 0.0 < d["ball_speed_kmh"] < 120.0
        elif e.type == "shot":
            d = stats.derived[e.event_id]
            assert 0.0 < d["xg"] < 1.0
            if d["distance_m"] >= 5.0:
                assert 25.0 < d["shot_speed_kmh"] < 140.0


def test_xg_is_plausible(played):
    for ts in played["stats"].teams.values():
        assert 0.1 < ts.xg < 6.0


def test_running_matches_offline_summary(played):
    offline = player_running(played["frames"])
    for pid, ps in played["stats"].players.items():
        if pid in offline and offline[pid]["km"] > 1.0:
            assert abs(ps.km - offline[pid]["km"]) / offline[pid]["km"] < 0.02


def test_moments_are_ordered_and_unique(played):
    ms = played["moments"]
    assert [m.t for m in ms] == sorted(m.t for m in ms)
    assert len({m.moment_id for m in ms}) == len(ms)


def test_window_metrics_cover_both_teams(played):
    stats = played["stats"]
    full = stats.window_metrics(0, stats.t)
    for tid in stats.team_ids:
        assert full[tid]["ppda"] is not None and full[tid]["ppda"] > 0
        assert 0.0 <= full[tid]["field_tilt"] <= 1.0
        assert full[tid]["block_height_m"] is not None
    cc = stats.control_chaos(stats.t - 5 * 60_000, stats.t)
    assert cc is not None and 0.0 <= cc <= 100.0


def test_possession_adds_up(played):
    stats = played["stats"]
    total = sum(ts.possession_ms for ts in stats.teams.values())
    assert 0.85 * stats.t <= total <= stats.t


def test_series_are_recorded_every_minute(played):
    stats = played["stats"]
    for tid in stats.team_ids:
        pts = stats.series["threat"][tid]
        assert len(pts) >= 85
        assert np.all(np.diff([t for t, _ in pts]) == 60_000)


def test_passes_and_shots_can_be_rebuilt_from_positions_alone(played):
    from groundtruth.stats.autoevent import detect, score

    meta = played["engine"].meta()
    team_of = {p.player_id: t.team_id for t in (meta.home, meta.away) for p in t.lineup + t.bench}
    keepers = {p.player_id for t in (meta.home, meta.away) for p in t.lineup + t.bench if p.role == "GK"}
    result = score(detect(played["frames"], team_of, keepers), played["events"])
    print(result)
    assert result["pass"]["recall"] > 0.5 and result["pass"]["precision"] > 0.5, result
    assert result["shot"]["detected"] > 0, result
