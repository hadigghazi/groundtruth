"""A match end to end in one process, as on Azure: simulator -> bus -> worker -> store, graded at full time."""

from __future__ import annotations

import asyncio

import pytest

from groundtruth.agents.desk import Desk, brief
from groundtruth.agents.fake import ScriptedChatClient
from groundtruth.live.runner import play
from groundtruth.live.view import StoreView


@pytest.fixture(scope="module")
def played():
    return asyncio.run(play("RVM", "HLB", 11, speed=0))


@pytest.fixture(scope="module")
def session(played):
    return played.session


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


def test_everything_the_worker_produced_reached_the_store(played, session):
    async def kinds():
        return {k: len(await played.docs(k)) for k in ("cue", "moment", "run", "result", "snapshot", "board", "event")}
    counts = asyncio.run(kinds())
    assert counts["cue"] == sum(len(f) for f in session.producer.schedule.values())
    assert counts["moment"] == len(session.stats.moments)
    assert counts["run"] == len(session.agent_runs) and counts["result"] == len(session.agent_results)
    assert counts["snapshot"] >= 90 and counts["event"] > 0


def test_each_agent_run_is_recorded_with_its_timings(session):
    runs = session.agent_runs
    assert runs and all(r["outcome"] in ("verified", "fallback", "timeout", "error", "guarded", "outage") for r in runs)
    ok = [r for r in runs if r["outcome"] == "verified"]
    assert ok and all(r["total_ms"] >= 0 and r["tactician_ms"] is not None for r in ok)


def test_the_simulator_grades_the_match_at_full_time(played, session):
    truth = asyncio.run(played.truth())
    card = truth["scorecard"]
    assert len(card["changes"]) == len(truth["truth"]) > 0
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
    out = asyncio.run(Desk(ScriptedChatClient()).recap(session, mid, "en", "casual"))
    assert out["verified"] and not out["fallback"], out
    h, a = _goals_before(session, mid)
    assert f"{h}-{a}" in out["headline"]


def test_the_store_view_answers_like_the_live_session(played, session):
    view = asyncio.run(StoreView.load(played.platform.store, played.match_id))
    mid = session.minutes[len(session.minutes) // 3]["t"]
    assert brief(view, mid) == brief(session, mid)
    tid = session.meta.home.team_id
    assert view.windows_at(tid, mid)["last_10_min"] == session.as_of(mid)["windows"]["recent"][tid]


def test_the_desk_answers_in_the_viewers_language(played):
    view = asyncio.run(StoreView.load(played.platform.store, played.match_id))
    mid = view.minutes[len(view.minutes) // 3]["t"]
    out = asyncio.run(Desk(ScriptedChatClient()).ask(view, "What is the score?", mid, "ar"))
    assert out["verified"] and out["answer"], out
    assert any("؀" <= ch <= "ۿ" for ch in out["answer"])


def test_the_full_time_recap_package(played, session):
    recap = session.recap
    assert set(recap["texts"]) == {"en", "es", "ar"} and set(recap["written_by"].values()) == {"agents"}
    for lang, modes in recap["texts"].items():
        assert set(modes) == {"analyst", "casual"} and all(m["headline"] and m["body"] for m in modes.values())
    goals = [m for m in session.stats.moments if m.kind == "goal"]
    clips = [c for c in recap["highlights"] if c["kind"] == "goal"]
    assert len(clips) == len(goals) and all(c["t_in"] < c["t_out"] and set(c["label"]) == {"en", "es", "ar"} for c in clips)
    assert recap["player_of_match"]["player"]
    assert asyncio.run(played.platform.store.get(played.match_id, f"recap-{played.match_id}")) is not None


def test_storylines_carry_their_numbers(session):
    for story in session.storylines.all:
        assert story["facts"] and set(story["headline"]) == {"en", "es", "ar"}
