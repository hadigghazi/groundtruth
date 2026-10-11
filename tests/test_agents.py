"""The agent team with the scripted client: hand-offs, the Verifier's retry, and the code checks."""

from __future__ import annotations

import asyncio

import pytest

from pitchside.agents.evidence import evidence_pack
from pitchside.agents.fake import ScriptedChatClient
from pitchside.agents.hook import InsightBoard
from pitchside.agents.pipeline import AgentTeam
from pitchside.agents.schemas import Claim, Insight
from pitchside.agents.verify import check_insight
from pitchside.schema import Moment
from pitchside.stats.engine import BASELINE, RECENT


@pytest.fixture(scope="module")
def shift(played):
    """A tactical-shift moment built from the match's real numbers, so its claims can check out."""
    stats = played["stats"]
    tid, key = stats.team_ids[0], "time_on_ball_s"
    now = stats.t
    a = stats.window_metrics(now - RECENT - BASELINE, now - RECENT)[tid][key]
    b = stats.window_metrics(now - RECENT, now)[tid][key]
    direction = "up" if b > a else "down"
    cause = "slow_down" if direction == "up" else "go_direct"
    m = Moment(moment_id="t001-k9999", match_id="t001", kind="tactical_shift", period=2, clock_ms=85 * 60_000,
               t=now, team_id=tid, salience=0.8, headline="test",
               metrics={"signal": key, "direction": direction, "before": a, "after": b, "rule_cause": cause})
    causes = {cause: [[key, direction]]}
    return stats, m, causes


def _team(stats, causes, bad_first_draft=False):
    published = []
    board = InsightBoard(published.append)
    team = AgentTeam(ScriptedChatClient(bad_first_draft=bad_first_draft), ScriptedChatClient(), stats, causes, board)
    return team, board, published


def test_verified_insight_reaches_all_writers(shift):
    stats, m, causes = shift
    team, board, published = _team(stats, causes)
    out = asyncio.run(team.explain(m))
    assert out.insight is not None and out.attempts == 1
    assert {w.lang for w in out.written} == {"en", "es", "ar"}
    for w in out.written:
        assert all(rw is not None for rw in w.texts.values()), w.problems
    stages = board.stages(m.moment_id)
    for stage in ("detected", "drafting", "drafted", "verified", "scheduled", "written_en", "written_ar"):
        assert stage in stages
    assert all(msg["type"] == "board" for msg in published)


def test_a_wrong_number_is_rejected_then_fixed_on_retry(shift):
    stats, m, causes = shift
    team, board, _ = _team(stats, causes, bad_first_draft=True)
    out = asyncio.run(team.explain(m))
    assert out.insight is not None and out.attempts == 2
    stages = board.stages(m.moment_id)
    assert stages.index("rejected") < stages.index("verified")
    rejected = next(e for e in board.items[m.moment_id] if e["stage"] == "rejected")
    assert any("not in the evidence" in p for p in rejected["problems"])


def _insight(m, **kw):
    base = dict(team_id=m.team_id, cause=m.metrics["rule_cause"], headline="A change", explanation="Something moved.",
                why_it_matters="It matters.", claims=[], confidence=0.5)
    return Insight(**{**base, **kw})


def test_verifier_rejects_numbers_not_in_the_evidence(shift):
    stats, m, causes = shift
    pack = evidence_pack(stats, m)
    assert check_insight(_insight(m), pack, causes) == []
    assert check_insight(_insight(m, explanation="It went up by 97.3 points."), pack, causes)


def test_verifier_rejects_a_claim_with_the_wrong_value(shift):
    stats, m, causes = shift
    pack = evidence_pack(stats, m)
    claim = Claim(text="x", metric=m.metrics["signal"], team_id=m.team_id, value=m.metrics["after"] + 5)
    assert any("evidence has" in p for p in check_insight(_insight(m, claims=[claim]), pack, causes))


def test_verifier_rejects_a_cause_the_signals_contradict(shift):
    stats, m, causes = shift
    pack = evidence_pack(stats, m)
    (cause, sigs), = causes.items()
    flipped = {cause: [[sigs[0][0], "down" if sigs[0][1] == "up" else "up"]]}
    assert any("none of them moved that way" in p for p in check_insight(_insight(m), pack, flipped))


def test_verifier_rejects_an_unknown_team(shift):
    stats, m, causes = shift
    pack = evidence_pack(stats, m)
    assert check_insight(_insight(m, team_id="XYZ"), pack, causes)
