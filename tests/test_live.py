"""A live session end to end with the scripted agents: template first, upgraded in place, scored at full time."""

from __future__ import annotations

import asyncio

import pytest

from groundtruth.agents.desk import brief
from groundtruth.agents.hook import attach_agents
from groundtruth.live.session import MatchSession


@pytest.fixture(scope="module")
def session(league):
    s = MatchSession(league["RVM"], league["HLB"], seed=11, match_id="lv01", speed=0)
    attach_agents(s, kind="scripted")
    asyncio.run(s.run())
    return s


def test_every_flagged_shift_airs_its_template_and_then_its_verified_card(session):
    shifts = [m for m in session.stats.moments if m.kind == "tactical_shift"]
    assert shifts, "the scripted match should flag at least one tactical shift"
    cues = [c for feed in session.producer.schedule.values() for c in feed]
    for m in shifts:
        templates = [c for c in cues if c.moment_id == m.moment_id and c.fallback]
        verified = [c for c in cues if c.moment_id == m.moment_id and not c.fallback]
        if not templates:  # the producer may drop a card if its layer stays busy too long
            continue
        assert {c.lang for c in templates} == {"en", "es", "ar"}
        if m.moment_id in session.agent_results:
            ids = {c.cue_id for c in templates}
            assert verified and all(set(c.replaces) <= ids and c.replaces for c in verified)
            for v in verified:  # the upgrade takes the template's slot exactly
                old = next(c for c in templates if c.cue_id == v.replaces[0])
                assert (v.t_in, v.t_out, v.layer) == (old.t_in, old.t_out, old.layer)


def test_viewers_see_the_upgrade_instead_of_the_template(session):
    for profile in ("analyst", "casual"):
        for lang in ("en", "ar"):
            seen = session.producer.visible(profile, lang)
            ids = {c.cue_id for c in seen}
            assert not any(r in ids for c in seen for r in c.replaces)
    upgraded = {r for feed in session.producer.schedule.values() for c in feed for r in c.replaces}
    if upgraded:
        assert any(c.cue_id in upgraded for c in session.producer.visible("all", "en"))  # no profile: template


def test_each_agent_run_is_recorded_with_its_timings(session):
    runs = session.agent_runs
    assert runs and all(r["outcome"] in ("verified", "fallback", "timeout", "error", "guarded", "outage") for r in runs)
    ok = [r for r in runs if r["outcome"] == "verified"]
    assert ok and all(r["total_ms"] >= 0 and r["tactician_ms"] is not None for r in ok)


def test_scorecard_scores_rule_and_agents_against_the_truth_log(session):
    card = session.full_time_card
    assert card is not None
    assert len(card["changes"]) == len(session.engine.truth) > 0
    for side in ("rule", "agents"):
        assert 0 <= card[side]["right_cause"] <= card[side]["caught"] <= card[side]["changes"]
    assert card["agents_verified"] == len(session.agent_results)


def _goals_before(session, t):
    h = a = 0
    for m in session.stats.moments:
        if m.kind == "goal" and m.t <= t:
            h += m.team_id == session.meta.home.team_id
            a += m.team_id == session.meta.away.team_id
    return h, a


def test_catch_up_stops_at_the_viewers_playhead(session):
    mid = session.minutes[len(session.minutes) // 2]["t"]
    b = brief(session, mid)
    assert all(m["minute"] <= b["minute"] + 1 for m in b["moments"])
    out = asyncio.run(session.desk.recap(mid, "en", "casual"))
    assert out["verified"] and not out["fallback"], out
    h, a = _goals_before(session, mid)
    assert f"{h}-{a}" in out["headline"]


def test_the_desk_answers_in_the_viewers_language(session):
    mid = session.minutes[len(session.minutes) // 3]["t"]
    out = asyncio.run(session.desk.ask("What is the score?", mid, "ar"))
    assert out["verified"] and out["answer"], out
    assert any("؀" <= ch <= "ۿ" for ch in out["answer"])
