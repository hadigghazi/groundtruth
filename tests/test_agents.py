"""The agent team with the scripted client: hand-offs, both retry paths, the code checks and the rulebook."""

from __future__ import annotations

import asyncio

import pytest

from groundtruth.agents.evidence import evidence_pack, is_allowed, numbers_with_places
from groundtruth.agents.fake import ScriptedChatClient
from groundtruth.agents.hook import InsightBoard
from groundtruth.agents.pipeline import AgentTeam
from groundtruth.agents.schemas import Claim, Insight, Rewrite
from groundtruth.agents.verify import check_insight, check_rewrite, rulebook
from groundtruth.schema import Moment
from groundtruth.stats.engine import BASELINE, RECENT


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


def _team(stats, causes, bad_first_draft=False, judge_rejects_first=False):
    published = []
    board = InsightBoard(published.append)
    team = AgentTeam(ScriptedChatClient(bad_first_draft=bad_first_draft),
                     ScriptedChatClient(judge_rejects_first=judge_rejects_first), stats, causes, board)
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
    for stage in ("detected", "drafting", "drafted", "checked", "scheduled", "judged", "written_en", "written_ar",
                  "verified"):
        assert stage in stages
    assert stages.index("verified") > max(stages.index("judged"), stages.index("written_es"))
    assert all(msg["type"] == "board" for msg in published)


def test_one_writer_call_per_language_covers_both_viewers(shift):
    stats, m, causes = shift
    small = ScriptedChatClient()
    team = AgentTeam(ScriptedChatClient(), small, stats, causes, InsightBoard(lambda _: None))
    asyncio.run(team.explain(m))
    assert small.calls.count("persona") == 3  # en, es, ar; each returns analyst and casual


def test_the_judge_runs_alongside_the_writers_and_its_rejection_discards_them(shift):
    stats, m, causes = shift
    team, board, _ = _team(stats, causes, judge_rejects_first=True)
    out = asyncio.run(team.explain(m))
    assert out.insight is not None and out.attempts == 2
    entries = board.items[m.moment_id]
    rejected = [e for e in entries if e["stage"] == "rejected"]
    assert [e["by"] for e in rejected] == ["judge"]
    # the writers had already written for the first draft, and wrote again for the second
    assert sum(e["stage"] == "written_en" for e in entries) == 2
    stages = board.stages(m.moment_id)
    assert stages.index("rejected") < stages.index("verified")


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


def test_rulebook_rejects_what_the_evidence_cannot_know():
    assert rulebook("Their most intense press of the season.", "en")
    assert rulebook("They will win this if it continues.", "en")
    assert rulebook("Su mejor racha de la temporada.", "es")
    assert rulebook("سيفوز الفريق بالتأكيد", "ar")
    assert rulebook("press_rate is up", "en")
    assert rulebook("They push up and press higher.", "en") == []
    assert rulebook("Un cambio claro en su forma de jugar.", "es") == []


def test_numbers_must_be_right_at_the_precision_written():
    assert numbers_with_places("from 31.6 to 32, then 31,4") == [(31.6, 1), (32.0, 0), (31.4, 1)]
    assert is_allowed(31.6, [31.62], 1)
    assert is_allowed(32, [31.62, round(31.62)], 0)
    assert not is_allowed(31, [31.62, round(31.62)], 0)  # 31.62 rounds to 32
    assert not is_allowed(31.8, [31.62, 31.6], 1)


def test_rewrites_must_name_the_team(shift):
    stats, m, causes = shift
    pack = evidence_pack(stats, m)
    names = (stats.team_name(m.team_id), "Full Name FC")
    good = Rewrite(headline=f"{names[0]} slow it down", body="A clear change in how they play.")
    bad = Rewrite(headline="They slow it down", body="A clear change in how they play.")
    assert check_rewrite(good, "en", [], 2, names) == []
    assert any("does not name the team" in p for p in check_rewrite(bad, "en", [], 2, names))
    assert check_insight(_insight(m, explanation="Their best spell of the season."), pack, causes)
