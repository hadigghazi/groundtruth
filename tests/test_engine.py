"""Engine invariants, determinism and broad realism on one full match."""

from __future__ import annotations

import pytest

from groundtruth import pitch
from groundtruth.league import build_league
from groundtruth.sim.engine import MatchEngine
from groundtruth.sim.summary import TYPICAL_PLAYER_KM, player_running, team_totals

LEAGUE = build_league()


def _first_minutes(seed: int, minutes: int = 10):
    eng = MatchEngine(LEAGUE["STM"], LEAGUE["KBY"], seed=seed, match_id="d")
    out = []
    for _ in range(minutes * 600):
        evs, _ = eng.step()
        out.extend(e.model_dump_json() for e in evs)
    return out


def test_same_seed_same_match():
    assert _first_minutes(3) == _first_minutes(3)


def test_different_seed_different_match():
    assert _first_minutes(3, 3) != _first_minutes(4, 3)


def test_two_periods(match):
    _, events, _ = match
    kinds = [e.type for e in events if e.type in ("period_start", "period_end")]
    assert kinds == ["period_start", "period_end", "period_start", "period_end"]


def test_sequence_and_ids(match):
    _, events, _ = match
    seqs = [e.seq for e in events]
    assert seqs == list(range(1, len(events) + 1))
    assert len({e.event_id for e in events}) == len(events)


def test_frames_are_10hz(match):
    _, _, frames = match
    for a, b in zip(frames, frames[1:]):
        if a.period == b.period:
            assert b.clock_ms - a.clock_ms == 100


def test_positions_stay_on_pitch(match):
    _, events, frames = match
    for e in events:
        for x, y in ((e.x, e.y), (e.end_x, e.end_y)):
            if x is not None:
                assert -0.5 <= x <= pitch.LENGTH + 0.5 and -0.5 <= y <= pitch.WIDTH + 0.5
    for f in frames[::50]:
        assert (f.xy[:, 0] > -2).all() and (f.xy[:, 0] < pitch.LENGTH + 2).all()
        assert (f.xy[:, 1] > -2).all() and (f.xy[:, 1] < pitch.WIDTH + 2).all()


def test_goals_match_score(match):
    engine, events, _ = match
    for t, tid in enumerate(engine.team_ids):
        assert sum(e.type == "goal" and e.team_id == tid for e in events) == engine.score[t]


def test_passes_are_resolved(match):
    engine, events, _ = match
    team_of = {p.player_id: c.club_id for c in engine.clubs for p in c.players}
    for e in events:
        if e.type == "pass":
            assert e.end_clock_ms is not None and e.end_clock_ms > e.clock_ms
            assert e.outcome in ("complete", "incomplete", "out")
            if e.outcome == "complete":
                assert team_of[e.receiver_id] == e.team_id


def test_raw_events_carry_no_derived_numbers(match):
    _, events, _ = match
    fields = set(events[0].model_dump())
    assert not fields & {"xg", "speed_kmh", "difficulty", "distance"}


def test_truth_log_records_hidden_changes(match):
    engine, _, _ = match
    assert 2 <= len(engine.truth) <= 3
    for rec in engine.truth:
        assert rec.kind == "tactics" and rec.source == "script"
        assert any(abs(rec.after[k] - rec.before[k]) >= 0.15 for k in rec.before)


def test_console_intervention_is_logged():
    eng = MatchEngine(LEAGUE["RVM"], LEAGUE["HLB"], seed=11, match_id="c", scripted=False)
    for _ in range(600):
        eng.step()
    eng.intervene("HLB", "high_press")
    eng.step()
    rec = eng.truth[-1]
    assert (rec.source, rec.team_id, rec.label) == ("console", "HLB", "high_press")
    assert eng.tactics[1].press_intensity > rec.before["press_intensity"]
    with pytest.raises(ValueError):
        eng.intervene("HLB", "park_the_bus")


def test_totals_look_like_football(match):
    engine, events, frames = match
    totals = team_totals(events, engine.team_ids)
    for s in totals.values():
        assert 250 <= s["passes"] <= 800
        assert 0.65 <= s["pass_completion"] <= 0.92
        assert 3 <= s["shots"] <= 28
    running = player_running(frames)
    gk = {p.player_id for c in engine.clubs for p in c.players if p.role == "GK"}
    full = [r["km"] for pid, r in running.items() if r["minutes"] > 88 and pid not in gk]
    lo, hi = TYPICAL_PLAYER_KM
    assert lo - 1.0 <= sum(full) / len(full) <= hi + 1.0
