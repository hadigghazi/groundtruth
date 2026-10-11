"""Attach the agent team to a live session: the insight board and the per-moment hook."""

from __future__ import annotations

import asyncio
import time

from agent_framework.observability import get_tracer

from ..schema import Moment
from .clients import make_clients
from .pipeline import AgentTeam


class InsightBoard:
    """Shared state every agent writes to; the web app shows it live as the agent trace."""

    def __init__(self, publish):
        self.publish = publish
        self.items: dict[str, list[dict]] = {}
        self.t0 = time.perf_counter()

    def __call__(self, moment_id: str, stage: str, **details) -> None:
        entry = {"moment_id": moment_id, "stage": stage,
                 "at_ms": round(1000 * (time.perf_counter() - self.t0)), **details}
        self.items.setdefault(moment_id, []).append(entry)
        self.publish({"type": "board", "entry": entry})

    def stages(self, moment_id: str) -> list[str]:
        return [e["stage"] for e in self.items.get(moment_id, [])]


def attach_agents(session, kind: str | None = None, budget_s: float = 12.0, use_judge: bool = True) -> AgentTeam:
    """The template cue stands in whenever the team fails or takes longer than `budget_s`."""
    large, small = make_clients(kind)
    board = InsightBoard(session.publish)
    team = AgentTeam(large, small, session.stats, session.stats.causes, board,
                     langs=session.builder.langs, use_judge=use_judge)
    session.board = board
    session.agent_team = team
    session.latencies_ms = []

    async def hook(sess, m: Moment) -> None:
        t0 = time.perf_counter()
        outcome = None
        with get_tracer().start_as_current_span("groundtruth.explain_moment") as span:
            span.set_attribute("groundtruth.match_id", m.match_id)
            span.set_attribute("groundtruth.moment_id", m.moment_id)
            span.set_attribute("groundtruth.rule_cause", str(m.metrics.get("rule_cause")))
            try:
                outcome = await asyncio.wait_for(team.explain(m), timeout=budget_s)
            except asyncio.TimeoutError:
                board(m.moment_id, "fallback", reasons=[f"no verified insight within {budget_s:g} s"])
            except Exception as e:  # never let an agent failure touch the match
                board(m.moment_id, "fallback", reasons=[f"{type(e).__name__}: {e}"])
            span.set_attribute("groundtruth.verified", bool(outcome and outcome.insight))
            if outcome and outcome.insight:
                span.set_attribute("groundtruth.cause", outcome.insight.cause)
                span.set_attribute("groundtruth.attempts", outcome.attempts)
        sess.latencies_ms.append(round(1000 * (time.perf_counter() - t0)))
        cues = []
        if outcome is not None and outcome.insight is not None:
            cues = sess.builder.from_insight(m, outcome.insight, outcome.written, outcome.attempts)
        if not cues:
            cues = sess.builder.from_moment(m)
        for cue in cues:
            sess.place_cue(cue)
        board(m.moment_id, "on_air", cues=len(cues), fallback=all(c.fallback for c in cues))

    session.hooks.append(hook)
    return team
