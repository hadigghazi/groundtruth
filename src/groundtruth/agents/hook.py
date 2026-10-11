"""Attach the agent team to a live session: the insight board and the per-moment hook.

Nothing waits on a model. The template card for a moment goes on air the
instant the detector flags it; the agents' verified card then takes over the
same slot (`Cue.replaces`). Viewers watch on a broadcast delay, so when the
agents are quick the viewer only ever sees the verified card; when they are
slow, the card upgrades in place; when they fail, the template stays.
"""

from __future__ import annotations

import asyncio
import time

from agent_framework.observability import get_tracer

from ..schema import Cue, Moment
from .clients import make_clients
from .guard import GUARD, UsageGuard, run_record
from .pipeline import AgentTeam


class InsightBoard:
    """Shared state every agent writes to; the web app shows it live as the agent trace."""

    def __init__(self, publish):
        self.publish = publish
        self.items: dict[str, list[dict]] = {}
        self.t0 = time.perf_counter()

    def __call__(self, moment_id: str, stage: str, **details) -> None:
        entry = {"moment_id": moment_id, "stage": stage,
                 "at_ms": round(1000 * (time.perf_counter() - self.t0)),
                 **{k: v for k, v in details.items() if v is not None}}
        self.items.setdefault(moment_id, []).append(entry)
        self.publish({"type": "board", "entry": entry})

    def stages(self, moment_id: str) -> list[str]:
        return [e["stage"] for e in self.items.get(moment_id, [])]


def attach_agents(session, kind: str | None = None, budget_s: float = 20.0, use_judge: bool = True,
                  guard: UsageGuard = GUARD) -> AgentTeam:
    """The template stays on air whenever the team fails, is refused by the guard, or takes over `budget_s`."""
    large, small = make_clients(kind)
    board = InsightBoard(session.publish)
    team = AgentTeam(large, small, session.stats, session.stats.causes, board,
                     langs=session.builder.langs, use_judge=use_judge)
    session.board = board
    session.agent_team = team

    async def hook(sess, m: Moment, on_air: list[Cue]) -> None:
        mid = m.moment_id
        board(mid, "detected", kind=m.kind, team_id=m.team_id,
              rule_cause=m.metrics.get("rule_cause"), score=m.metrics.get("score"))
        board(mid, "template_on_air", cues=len(on_air))
        refused = "simulated model outage" if sess.outage else guard.admit()
        if refused:
            board(mid, "fallback", reasons=[refused])
            sess.record_run(run_record(m, [], "guarded" if not sess.outage else "outage", 0, 0))
            return
        t0 = time.perf_counter()
        outcome, kind_ = None, "fallback"
        with get_tracer().start_as_current_span("groundtruth.explain_moment") as span:
            span.set_attribute("groundtruth.match_id", m.match_id)
            span.set_attribute("groundtruth.moment_id", mid)
            span.set_attribute("groundtruth.rule_cause", str(m.metrics.get("rule_cause")))
            try:
                outcome = await asyncio.wait_for(team.explain(m, announce=False), timeout=budget_s)
            except asyncio.TimeoutError:
                kind_ = "timeout"
                board(mid, "fallback", reasons=[f"no verified insight within {budget_s:g} s"])
            except Exception as e:  # never let an agent failure touch the match
                kind_ = "error"
                board(mid, "fallback", reasons=[f"{type(e).__name__}: {e}"])
            total_ms = round(1000 * (time.perf_counter() - t0))
            verified = bool(outcome and outcome.insight)
            span.set_attribute("groundtruth.verified", verified)
            span.set_attribute("groundtruth.latency_ms", total_ms)
            if verified:
                span.set_attribute("groundtruth.cause", outcome.insight.cause)
                span.set_attribute("groundtruth.attempts", outcome.attempts)
        entries = board.items.get(mid, [])
        guard.spend(sum(e.get("tokens_in", 0) + e.get("tokens_out", 0) for e in entries))
        if verified:
            old = {c.lang: c for c in on_air}
            cues = sess.builder.from_insight(m, outcome.insight, outcome.written, outcome.attempts)
            for cue in cues:
                sess.upgrade_cue(cue, old.get(cue.lang))
            board(mid, "upgraded", cues=len(cues), ms=total_ms)
            ins = outcome.insight
            sess.agent_results[mid] = {"t": m.t, "team_id": ins.team_id, "cause": ins.cause,
                                       "text": f"{ins.headline}. {ins.explanation} {ins.why_it_matters}"}
            kind_ = "verified"
        sess.record_run(run_record(m, entries, kind_, total_ms, outcome.attempts if outcome else 0,
                                   outcome.insight if verified else None))

    session.hooks.append(hook)
    return team
