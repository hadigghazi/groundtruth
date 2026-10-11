"""The agent team as one Microsoft Agent Framework graph workflow.

    Tactician -> Verifier --approved--> Producer --fan-out--> Persona(en) ┐
        ^           |                                         Persona(es) ├--fan-in--> Collector
        └--retry----┤                                         Persona(ar) ┘
                    └--rejected twice--> Fallback

Every hand-off is a typed message; every step writes to the shared insight
board, which the web app shows live as the agent trace.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from agent_framework import Agent, Executor, WorkflowBuilder, WorkflowContext, handler, tool
from typing_extensions import Never

from ..render.templates import word
from ..schema import Moment
from ..stats.engine import MINUTE, StatsEngine
from .evidence import allowed_numbers, evidence_pack
from .prompts import JUDGE, LANG_NAMES, MAX_NUMBERS, PERSONA, TACTICIAN
from .schemas import Insight, Judgement, Rewrite
from .verify import check_insight, check_rewrite

MAX_ATTEMPTS = 2
MODES = ("analyst", "casual")


# ------------------------------------------------------------------ messages

@dataclass
class Job:
    moment: Moment
    pack: dict
    attempt: int = 1
    feedback: list[str] = field(default_factory=list)
    tool_numbers: list[float] = field(default_factory=list)


@dataclass
class Draft:
    job: Job
    insight: Insight | None
    error: str | None = None


@dataclass
class Approved:
    job: Job
    insight: Insight


@dataclass
class Rejected:
    job: Job
    reasons: list[str]


@dataclass
class Written:
    approved: Approved
    lang: str
    texts: dict[str, Rewrite | None]
    problems: dict[str, list[str]]


@dataclass
class Outcome:
    moment: Moment
    insight: Insight | None
    written: list[Written]
    reasons: list[str]
    attempts: int


Board = Callable[..., None]  # board(moment_id, stage, **details)


def _parse(model, response) -> object | None:
    value = getattr(response, "value", None)
    if isinstance(value, model):
        return value
    text = (getattr(response, "text", "") or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return model.model_validate_json(text[start:end + 1])
    except ValueError:
        return None


# ----------------------------------------------------------------- the tools

def make_tools(stats: StatsEngine, record: list[float]):
    """Read-only tools over the live match state. Every number they return becomes quotable."""

    def keep(x):
        record.extend(allowed_numbers({"teams": {}, "x": x}))
        return x

    @tool(approval_mode="never_require")
    def team_window(team_id: str, minutes_back_start: int, minutes_back_end: int = 0) -> dict:
        """Metrics for one team over a stretch of play, counted back from now, e.g. 15 to 0 for the last 15 minutes."""
        lo = stats.t - max(minutes_back_start, minutes_back_end + 1) * MINUTE
        hi = stats.t - minutes_back_end * MINUTE
        return keep({k: v for k, v in stats.window_metrics(lo, hi)[team_id].items() if v is not None})

    @tool(approval_mode="never_require")
    def running_report(team_id: str) -> list[dict]:
        """Distance covered (km), sprints and current speed (km/h) for each player of a team, to check fatigue."""
        rows = [{"player": p.name, "km": round(p.km, 2), "sprints": p.sprints, "speed_kmh": round(p.speed_kmh, 1)}
                for p in stats.players.values() if p.team_id == team_id and p.km > 0]
        return keep(sorted(rows, key=lambda r: -r["km"]))

    return [team_window, running_report]


# ---------------------------------------------------------------- executors

class Tactician(Executor):
    def __init__(self, agent: Agent, board: Board):
        super().__init__(id="tactician")
        self.agent, self.board = agent, board

    @handler
    async def draft(self, job: Job, ctx: WorkflowContext[Draft]) -> None:
        self.board(job.moment.moment_id, "drafting", attempt=job.attempt)
        task = {"task": "tactician", "attempt": job.attempt, "evidence": job.pack}
        if job.feedback:
            task["your_previous_draft_was_rejected_because"] = job.feedback
        t0 = time.perf_counter()
        try:
            resp = await self.agent.run(json.dumps(task), options={"response_format": Insight})
            insight = _parse(Insight, resp)
            err = None if insight else "the reply was not a valid Insight"
        except Exception as e:  # a model or network failure is a rejected draft, not a crash
            insight, err = None, f"{type(e).__name__}: {e}"
        self.board(job.moment.moment_id, "drafted", attempt=job.attempt,
                   ms=round(1000 * (time.perf_counter() - t0)), ok=insight is not None,
                   cause=insight.cause if insight else None, headline=insight.headline if insight else None)
        await ctx.send_message(Draft(job, insight, err))


class Verifier(Executor):
    def __init__(self, judge: Agent | None, causes: dict, board: Board):
        super().__init__(id="verifier")
        self.judge, self.causes, self.board = judge, causes, board

    @handler
    async def check(self, draft: Draft, ctx: WorkflowContext[Job | Approved | Rejected]) -> None:
        job = draft.job
        problems = [draft.error] if draft.error else []
        if draft.insight:
            pack = dict(job.pack)
            pack["tool_results"] = job.tool_numbers
            problems += check_insight(draft.insight, pack, self.causes)
        if draft.insight and not problems and self.judge is not None:
            try:
                resp = await self.judge.run(json.dumps({"task": "judge", "evidence": job.pack,
                                                        "explanation": draft.insight.model_dump()}),
                                            options={"response_format": Judgement})
                verdict = _parse(Judgement, resp)
                if verdict and not verdict.supported:
                    problems += verdict.problems or ["the judge found the reasoning unsupported"]
            except Exception as e:
                problems.append(f"judge failed: {type(e).__name__}")
        mid = job.moment.moment_id
        if not problems:
            self.board(mid, "verified", attempt=job.attempt)
            await ctx.send_message(Approved(job, draft.insight))
        elif job.attempt < MAX_ATTEMPTS:
            self.board(mid, "rejected", attempt=job.attempt, problems=problems)
            await ctx.send_message(Job(job.moment, job.pack, job.attempt + 1, problems, job.tool_numbers))
        else:
            self.board(mid, "rejected", attempt=job.attempt, problems=problems, final=True)
            await ctx.send_message(Rejected(job, problems))


class Producer(Executor):
    """Code, not a model: decides whether the insight airs, then hands it to the writers."""

    def __init__(self, board: Board, min_confidence: float = 0.3):
        super().__init__(id="producer")
        self.board, self.min_confidence = board, min_confidence

    @handler
    async def plan(self, ok: Approved, ctx: WorkflowContext[Approved]) -> None:
        if ok.insight.confidence < self.min_confidence:
            self.board(ok.job.moment.moment_id, "held", reason="low confidence")
        self.board(ok.job.moment.moment_id, "scheduled", layer="stat_panel")
        await ctx.send_message(ok)


class Persona(Executor):
    def __init__(self, agent: Agent, lang: str, stats: StatsEngine, board: Board):
        super().__init__(id=f"persona_{lang}")
        self.agent, self.lang, self.stats, self.board = agent, lang, stats, board

    async def _one(self, ok: Approved, mode: str, allowed: list[float]) -> tuple[str, Rewrite | None, list[str]]:
        ins = ok.insight
        key = (ins.claims[-1].metric if ins.claims else None) or ok.job.moment.metrics.get("signal", "")
        task = {"task": "persona", "lang": self.lang, "language": LANG_NAMES[self.lang], "mode": mode,
                "team_name": self.stats.team_name(ins.team_id), "metric_word": word(key, self.lang),
                "insight": ins.model_dump()}
        try:
            resp = await self.agent.run(json.dumps(task, ensure_ascii=False), options={"response_format": Rewrite})
            rw = _parse(Rewrite, resp)
        except Exception as e:
            return mode, None, [f"{type(e).__name__}: {e}"]
        if rw is None:
            return mode, None, ["the reply was not a valid Rewrite"]
        problems = check_rewrite(rw, self.lang, allowed, MAX_NUMBERS[mode])
        return mode, (None if problems else rw), problems

    @handler
    async def write(self, ok: Approved, ctx: WorkflowContext[Written]) -> None:
        allowed = allowed_numbers(ok.job.pack) + ok.job.tool_numbers
        results = await asyncio.gather(*(self._one(ok, mode, allowed) for mode in MODES))
        texts = {mode: rw for mode, rw, _ in results}
        problems = {mode: p for mode, _, p in results if p}
        self.board(ok.job.moment.moment_id, f"written_{self.lang}",
                   ok=[m for m, rw in texts.items() if rw], failed=list(problems))
        await ctx.send_message(Written(ok, self.lang, texts, problems))


class Collector(Executor):
    def __init__(self):
        super().__init__(id="collector")

    @handler
    async def collect(self, written: list[Written], ctx: WorkflowContext[Never, Outcome]) -> None:
        ok = written[0].approved
        await ctx.yield_output(Outcome(ok.job.moment, ok.insight, list(written), [], ok.job.attempt))


class Fallback(Executor):
    def __init__(self, board: Board):
        super().__init__(id="fallback")
        self.board = board

    @handler
    async def give_up(self, rej: Rejected, ctx: WorkflowContext[Never, Outcome]) -> None:
        self.board(rej.job.moment.moment_id, "fallback", reasons=rej.reasons)
        await ctx.yield_output(Outcome(rej.job.moment, None, [], rej.reasons, rej.job.attempt))


# ---------------------------------------------------------------- the team

class AgentTeam:
    """Builds one workflow per moment from shared agents, so moments run concurrently."""

    def __init__(self, large_client, small_client, stats: StatsEngine, causes: dict, board: Board,
                 langs: tuple[str, ...] = ("en", "es", "ar"), use_judge: bool = True):
        self.stats, self.causes, self.board, self.langs = stats, causes, board, langs
        self.tool_numbers: list[float] = []
        self.tactician = Agent(client=large_client, name="Tactician", instructions=TACTICIAN,
                               tools=make_tools(stats, self.tool_numbers))
        self.judge = Agent(client=small_client, name="VerifierJudge", instructions=JUDGE) if use_judge else None
        self.writers = {lang: Agent(client=small_client, name=f"Persona_{lang}", instructions=PERSONA)
                        for lang in langs}

    def _workflow(self):
        tactician = Tactician(self.tactician, self.board)
        verifier = Verifier(self.judge, self.causes, self.board)
        producer = Producer(self.board)
        personas = [Persona(self.writers[lang], lang, self.stats, self.board) for lang in self.langs]
        collector = Collector()
        fallback = Fallback(self.board)
        return (WorkflowBuilder(start_executor=tactician, name="groundtruth-insight")
                .add_edge(tactician, verifier)
                .add_edge(verifier, tactician, condition=lambda m: isinstance(m, Job))
                .add_edge(verifier, producer, condition=lambda m: isinstance(m, Approved))
                .add_edge(verifier, fallback, condition=lambda m: isinstance(m, Rejected))
                .add_fan_out_edges(producer, personas)
                .add_fan_in_edges(personas, collector)
                .build())

    async def explain(self, m: Moment) -> Outcome:
        pack = evidence_pack(self.stats, m)
        self.board(m.moment_id, "detected", kind=m.kind, team_id=m.team_id,
                   rule_cause=m.metrics.get("rule_cause"), score=m.metrics.get("score"))
        result = await self._workflow().run(Job(m, pack, tool_numbers=self.tool_numbers))
        outputs = result.get_outputs()
        if not outputs:
            return Outcome(m, None, [], ["the workflow produced no output"], 0)
        return outputs[0]
