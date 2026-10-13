"""A match end to end in one process, as on Azure: simulator -> bus -> worker -> store, graded at full time."""

from __future__ import annotations

import asyncio

import pytest

from groundtruth.agents.desk import Desk, brief
from groundtruth.agents.fake import ScriptedChatClient
from groundtruth import cloud
from groundtruth.live.runner import play
from groundtruth.live.simulator import SimulatorService
from groundtruth.live.worker import WorkerService
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


def test_the_final_flush_waits_for_a_write_in_flight(played):
    """On a real store a write takes time; full time must not be signalled before the last one lands."""
    from groundtruth.cloud import ROLES, Platform
    from groundtruth.cloud.broadcast import LocalBroadcaster
    from groundtruth.cloud.bus import MemoryBus
    from groundtruth.cloud.store import MemoryStore
    from groundtruth.live.session import MatchSession
    from groundtruth.live.worker import WorkerService

    class SlowStore(MemoryStore):
        async def upsert(self, docs):
            await asyncio.sleep(0.2)
            await super().upsert(docs)

    async def go():
        p = Platform(MemoryBus(), SlowStore(), LocalBroadcaster(), ROLES)
        w = WorkerService(p, agents=False)
        s = MatchSession(played.session.meta)
        s.doc("result", "result-x", {"moment_id": "x"})
        periodic = asyncio.create_task(w._flush(s))  # the periodic flusher takes the document
        await asyncio.sleep(0.01)
        await w._flush(s)  # the end-of-match flush
        stored = await p.store.get(s.match_id, "result-x")
        await periodic
        return stored

    assert asyncio.run(go()) is not None


def test_a_restarted_simulator_finishes_the_match_it_was_playing():
    """A deploy restarts the simulator mid-match. The new one replays the match from its seed up to
    where the worker had got and plays on: same score, same hidden changes as an uninterrupted match."""
    import time

    async def go():
        ref = await play("RVM", "HLB", 5, speed=0, agents=False)
        p, mid = cloud.local(), "mrestart"
        sim, worker = SimulatorService(p), WorkerService(p, agents=False)
        tasks = [asyncio.create_task(sim.run()), asyncio.create_task(worker.run())]
        start = {"home": "RVM", "away": "HLB", "seed": 5, "speed": 600, "scripted": True}
        await p.store.upsert([{"id": f"match-{mid}", "match_id": mid, "kind": "match", "status": "starting",
                               "outage": False, "created": time.time(), **start}])
        await p.bus.publish("control", mid, [{"type": "start", "match_id": mid, **start}])
        while ((await p.store.get(mid, f"live-{mid}")) or {}).get("t", 0) < 20 * 60_000:
            await asyncio.sleep(0.05)
        sim.running[mid].task.cancel()  # the simulator goes away mid-match
        tasks[0].cancel()
        await asyncio.sleep(0.2)
        assert (await p.store.get(mid, f"match-{mid}"))["status"] == "live"
        tasks.append(asyncio.create_task(SimulatorService(p).run()))
        deadline = time.monotonic() + 300
        try:
            while await p.store.get(mid, f"truth-{mid}") is None:
                assert time.monotonic() < deadline, "the resumed match never reached full time"
                await asyncio.sleep(0.2)
        finally:
            for x in tasks:
                x.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        doc, ref_doc = await p.store.get(mid, f"match-{mid}"), await ref.platform.store.get(ref.match_id, f"match-{ref.match_id}")
        truth, ref_truth = await p.store.get(mid, f"truth-{mid}"), await ref.truth()
        return doc, ref_doc, truth, ref_truth

    doc, ref_doc, truth, ref_truth = asyncio.run(go())
    assert doc["status"] == "finished"
    assert doc["score"] == ref_doc["score"]
    key = lambda tr: [(c["team_id"], c["label"], c["t"]) for c in tr["truth"]]
    assert key(truth) == key(ref_truth)

