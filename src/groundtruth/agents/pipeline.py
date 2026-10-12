"""The agent team as one Microsoft Agent Framework graph workflow.

    Tactician -> Checker --pass--> Producer --fan-out--> Judge        ┐
        ^          |                                     Challenger   |
        |          |                                     Persona (en) ├--fan-in--> Gate --> verified cards
        |          |                                     Persona (es) |             |
        |          |                                     Persona (ar) ┘             |
        └--retry---┴---------------------------------------------------------------┤
                   └--rejected twice--> Fallback <----------------------------------┘

The Checker is code: numbers, claims, the cause's direction and the rulebook.
Only a draft that passes it costs any more model time. The judge then runs
alongside the writers instead of before them: the code checks have already
passed, so the judge nearly always agrees, and waiting for it would add a
whole model round trip to every card. When it disagrees, the writers' text is
thrown away and the Tactician tries again with the judge's reasons.

The Challenger argues for the strongest different explanation, at the same time.
Code settles the argument: the detector's own cause score, computed from the
evidence, for each side. The challenger wins only by a clear margin, and then
the Tactician has to answer it on the retry.

Every step writes to the shared insight board (the web app's agent trace)
with its latency and token use.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field

from agent_framework import Agent, Executor, WorkflowBuilder, WorkflowContext, handler, tool
from typing_extensions import Never

from ..render.templates import word
from ..schema import Moment
from ..stats.engine import MINUTE, StatsEngine
from .evidence import allowed_numbers, evidence_pack
from .evidence import BASE_KEY, RECENT_KEY
from .prompts import CHALLENGER, JUDGE, LANG_NAMES, MAX_NUMBERS, PERSONA, TACTICIAN, task
from .schemas import Alternative, Insight, Judgement, PersonaPair, Rewrite
from .verify import check_insight, check_rewrite

MAX_ATTEMPTS = 2
MODES = ("analyst", "casual")
CHALLENGE_MARGIN = 1.0  # standardized units the challenger's cause score must lead by
# The play time of the moment being explained. Tools count back from it, not from the live
# edge, so a slow run (or an evaluation played faster than real time) never sees later play.
AS_OF: ContextVar[int | None] = ContextVar("groundtruth_as_of", default=None)


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
    """Passed the code checks; the judge's verdict is still to come."""
    job: Job
    insight: Insight


@dataclass
class Rejected:
    job: Job
    reasons: list[str]


@dataclass
class Part:
    """What the Gate collects from each branch of the fan-out."""
    approved: Approved


@dataclass
class Verdict(Part):
    problems: list[str]


@dataclass
class Rebuttal(Part):
    problems: list[str]


@dataclass
class Written(Part):
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
    pack: dict | None = None  # the evidence it was written from, kept for evaluation


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


def usage(response) -> dict[str, int]:
    """Token counts from an agent response, whatever shape the client reports them in."""
    u = getattr(response, "usage_details", None)
    if u is None:
        return {}
    get = u.get if isinstance(u, dict) else (lambda k, d=None: getattr(u, k, d))
    out = {"tokens_in": int(get("input_token_count", 0) or 0), "tokens_out": int(get("output_token_count", 0) or 0)}
    return out if any(out.values()) else {}


def _ms(t0: float) -> int:
    return round(1000 * (time.perf_counter() - t0))


# ----------------------------------------------------------------- the tools

def make_tools(stats: StatsEngine, record: list[float]):
    """Read-only tools over the live match state. Every number they return becomes quotable."""

    def keep(x):
        record.extend(allowed_numbers({"teams": {}, "x": x}))
        return x

    @tool(approval_mode="never_require")
    def team_window(team_id: str, minutes_back_start: int, minutes_back_end: int = 0) -> dict:
        """Metrics for one team over a stretch of play, counted back from now, e.g. 15 to 0 for the last 15 minutes."""
        now = AS_OF.get() or stats.t
        lo = now - max(minutes_back_start, minutes_back_end + 1) * MINUTE
        hi = now - minutes_back_end * MINUTE
        return keep({k: round(v, 3) for k, v in stats.window_metrics(lo, hi)[team_id].items() if v is not None})

    @tool(approval_mode="never_require")
    def running_report(team_id: str) -> list[dict]:
        """Distance covered (km), sprints and current speed (km/h) for each player of a team, to check fatigue."""
        rows = [{"player": p.name, "km": round(p.km, 2), "sprints": p.sprints, "speed_kmh": round(p.speed_kmh, 1)}
                for p in stats.players.values() if p.team_id == team_id and p.km > 0]
        return keep(sorted(rows, key=lambda r: -r["km"]))

    return [team_window, running_report]


# ---------------------------------------------------------------- executors

def _reasoning() -> dict:
    """The Tactician's reasoning effort when its model is a reasoning model (GROUNDTRUTH_REASONING_EFFORT).

    At the default effort gpt-5-mini thinks for thousands of tokens and misses the 20 s budget. Sent
    to the primary model only: the backup and the writers are not reasoning models and refuse it.
    """
    effort = os.environ.get("GROUNDTRUTH_REASONING_EFFORT", "").strip()
    return {"reasoning": {"effort": effort}} if effort else {}


class Tactician(Executor):
    """The larger model drafts; if it fails outright (outage, quota), the backup model drafts instead."""

    def __init__(self, agent: Agent, backup: Agent | None, board: Board):
        super().__init__(id="tactician")
        self.agent, self.backup, self.board = agent, backup, board

    @handler
    async def draft(self, job: Job, ctx: WorkflowContext[Draft]) -> None:
        mid = job.moment.moment_id
        self.board(mid, "drafting", attempt=job.attempt)
        payload = {"task": "tactician", "attempt": job.attempt, "evidence": job.pack}
        if job.feedback:
            payload["your_previous_draft_was_rejected_because"] = job.feedback
        insight, err, model, used, t0 = None, None, "primary", {}, time.perf_counter()
        for model, agent in (("primary", self.agent), ("backup", self.backup)):
            if agent is None:
                continue
            try:
                extra = _reasoning() if model == "primary" else {}
                resp = await agent.run(task(payload), options={"response_format": Insight, **extra})
                insight, used = _parse(Insight, resp), usage(resp)
                err = None if insight else "the reply was not a valid Insight"
                break
            except Exception as e:  # a model or network failure: try the backup, then reject the draft
                err = f"{type(e).__name__}: {e}"
        self.board(mid, "drafted", attempt=job.attempt, ms=_ms(t0), ok=insight is not None, model=model,
                   cause=insight.cause if insight else None, headline=insight.headline if insight else None,
                   team_id=insight.team_id if insight else None, **used)
        await ctx.send_message(Draft(job, insight, err))


class Checker(Executor):
    """The Verifier's code layer: every number, claim, cause direction and rule, in microseconds."""

    def __init__(self, causes: dict, board: Board):
        super().__init__(id="checker")
        self.causes, self.board = causes, board

    @handler
    async def check(self, draft: Draft, ctx: WorkflowContext[Job | Approved | Rejected]) -> None:
        job = draft.job
        problems = [draft.error] if draft.error else []
        if draft.insight:
            problems += check_insight(draft.insight, {**job.pack, "tool_results": job.tool_numbers}, self.causes)
        mid = job.moment.moment_id
        if not problems:
            self.board(mid, "checked", attempt=job.attempt)
            await ctx.send_message(Approved(job, draft.insight))
        elif job.attempt < MAX_ATTEMPTS:
            self.board(mid, "rejected", attempt=job.attempt, by="code", problems=problems)
            await ctx.send_message(Job(job.moment, job.pack, job.attempt + 1, problems, job.tool_numbers))
        else:
            self.board(mid, "rejected", attempt=job.attempt, by="code", problems=problems, final=True)
            await ctx.send_message(Rejected(job, problems))


class Producer(Executor):
    """Code, not a model: schedules the insight's slot, then starts the judge and the writers together."""

    def __init__(self, board: Board, min_confidence: float = 0.3):
        super().__init__(id="producer")
        self.board, self.min_confidence = board, min_confidence

    @handler
    async def plan(self, ok: Approved, ctx: WorkflowContext[Approved]) -> None:
        mid = ok.job.moment.moment_id
        if ok.insight.confidence < self.min_confidence:
            self.board(mid, "held", reason="low confidence")
        self.board(mid, "scheduled", layer="stat_panel", attempt=ok.job.attempt)
        await ctx.send_message(ok)


class Judge(Executor):
    def __init__(self, agent: Agent | None, board: Board):
        super().__init__(id="judge")
        self.agent, self.board = agent, board

    @handler
    async def judge(self, ok: Approved, ctx: WorkflowContext[Verdict]) -> None:
        if self.agent is None:
            await ctx.send_message(Verdict(ok, []))
            return
        t0, problems, used = time.perf_counter(), [], {}
        try:
            resp = await self.agent.run(task({"task": "judge", "evidence": ok.job.pack,
                                              "explanation": ok.insight.model_dump()}),
                                        options={"response_format": Judgement})
            verdict, used = _parse(Judgement, resp), usage(resp)
            if verdict is None:
                problems = ["the judge's reply was not a valid Judgement"]
            elif not verdict.supported:
                problems = verdict.problems or ["the judge found the reasoning unsupported"]
        except Exception as e:
            problems = [f"judge failed: {type(e).__name__}"]
        self.board(ok.job.moment.moment_id, "judged", attempt=ok.job.attempt, ms=_ms(t0), ok=not problems,
                   problems=problems or None, **used)
        await ctx.send_message(Verdict(ok, problems))


def arbitrate(stats: StatsEngine, pack: dict, ins: Insight, alt: Alternative) -> tuple[float | None, float | None, bool]:
    """Scores both explanations with the detector's cause score; True if the alternative clearly wins."""
    base = {tid: t.get(BASE_KEY, {}) for tid, t in pack["teams"].items()}
    cur = {tid: t.get(RECENT_KEY, {}) for tid, t in pack["teams"].items()}

    def score(cause: str, tid: str) -> float | None:
        if cause not in stats.causes or tid not in base:
            return None
        s = stats._score(cause, tid, base, cur)
        return round(s[0], 2) if s else None

    mine, theirs = score(ins.cause, ins.team_id), score(alt.cause, alt.team_id)
    if (alt.cause, alt.team_id) == (ins.cause, ins.team_id) or theirs is None:
        return mine, theirs, False
    bar = mine + CHALLENGE_MARGIN if mine is not None else stats.score_thresholds.get(alt.cause, 3.0)
    return mine, theirs, theirs >= bar


class Challenger(Executor):
    def __init__(self, agent: Agent | None, stats: StatsEngine, board: Board):
        super().__init__(id="challenger")
        self.agent, self.stats, self.board = agent, stats, board

    @handler
    async def challenge(self, ok: Approved, ctx: WorkflowContext[Rebuttal]) -> None:
        if self.agent is None:
            await ctx.send_message(Rebuttal(ok, []))
            return
        t0, used, problems, alt = time.perf_counter(), {}, [], None
        try:
            resp = await self.agent.run(task({"task": "challenge", "evidence": ok.job.pack,
                                              "explanation": ok.insight.model_dump()}),
                                        options={"response_format": Alternative})
            alt, used = _parse(Alternative, resp), usage(resp)
        except Exception:
            alt = None  # a failed challenge is no challenge
        mine = theirs = None
        wins = False
        if alt is not None:
            mine, theirs, wins = arbitrate(self.stats, ok.job.pack, ok.insight, alt)
            if wins:
                problems = [f"a challenger argued that {self.stats.team_name(alt.team_id)} changed ({alt.cause}): "
                            f"{alt.argument} The signals support that more strongly "
                            f"({theirs:g} against {mine if mine is not None else 'none'})."]
        self.board(ok.job.moment.moment_id, "challenged", attempt=ok.job.attempt, ms=_ms(t0), ok=not wins,
                   alt_cause=alt.cause if alt else None, alt_team=alt.team_id if alt else None,
                   score=mine, alt_score=theirs, **used)
        await ctx.send_message(Rebuttal(ok, problems))


class Persona(Executor):
    def __init__(self, agent: Agent, lang: str, stats: StatsEngine, board: Board):
        super().__init__(id=f"persona_{lang}")
        self.agent, self.lang, self.stats, self.board = agent, lang, stats, board

    @handler
    async def write(self, ok: Approved, ctx: WorkflowContext[Written]) -> None:
        ins = ok.insight
        key = (ins.claims[-1].metric if ins.claims else None) or ok.job.moment.metrics.get("signal", "")
        meta = self.stats.meta
        full = meta.home.name if ins.team_id == meta.home.team_id else meta.away.name
        names = (self.stats.team_name(ins.team_id), full)
        payload = {"task": "persona", "lang": self.lang, "language": LANG_NAMES[self.lang],
                   "team_name": names[0], "metric_word": word(key, self.lang), "insight": ins.model_dump()}
        t0, used = time.perf_counter(), {}
        texts: dict[str, Rewrite | None] = dict.fromkeys(MODES)
        problems: dict[str, list[str]] = {}
        try:
            resp = await self.agent.run(task(payload), options={"response_format": PersonaPair})
            pair, used = _parse(PersonaPair, resp), usage(resp)
            if pair is None:
                problems = {m: ["the reply was not a valid PersonaPair"] for m in MODES}
        except Exception as e:
            pair, problems = None, {m: [f"{type(e).__name__}: {e}"] for m in MODES}
        if pair is not None:
            allowed = allowed_numbers(ok.job.pack) + ok.job.tool_numbers
            for mode in MODES:
                rw = getattr(pair, mode)
                p = check_rewrite(rw, self.lang, allowed, MAX_NUMBERS[mode], names)
                texts[mode] = None if p else rw
                if p:
                    problems[mode] = p
        self.board(ok.job.moment.moment_id, f"written_{self.lang}", attempt=ok.job.attempt, ms=_ms(t0),
                   ok=[m for m, rw in texts.items() if rw], failed=list(problems),
                   problems=[f"{m}: {x}" for m, ps in problems.items() for x in ps] or None, **used)
        await ctx.send_message(Written(ok, self.lang, texts, problems))


class Gate(Executor):
    """Code: airs the writers' text only if the judge agreed with the insight it came from."""

    def __init__(self, board: Board):
        super().__init__(id="gate")
        self.board = board

    @handler
    async def decide(self, parts: list[Part], ctx: WorkflowContext[Job | Rejected, Outcome]) -> None:
        ok = parts[0].approved
        job, mid = ok.job, ok.job.moment.moment_id
        problems = [p for part in parts if isinstance(part, (Verdict, Rebuttal)) for p in part.problems]
        by = "challenger" if any(isinstance(p, Rebuttal) and p.problems for p in parts) else "judge"
        if not problems:
            written = [p for p in parts if isinstance(p, Written)]
            self.board(mid, "verified", attempt=job.attempt, cause=ok.insight.cause, team_id=ok.insight.team_id)
            await ctx.yield_output(Outcome(job.moment, ok.insight, written, [], job.attempt))
        elif job.attempt < MAX_ATTEMPTS:
            self.board(mid, "rejected", attempt=job.attempt, by=by, problems=problems)
            await ctx.send_message(Job(job.moment, job.pack, job.attempt + 1, problems, job.tool_numbers))
        else:
            self.board(mid, "rejected", attempt=job.attempt, by=by, problems=problems, final=True)
            await ctx.send_message(Rejected(job, problems))


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
                 langs: tuple[str, ...] = ("en", "es", "ar"), use_judge: bool = True, backup: bool = True,
                 challenger: bool = True):
        self.stats, self.causes, self.board, self.langs = stats, causes, board, langs
        self.tool_numbers: list[float] = []
        tools = make_tools(stats, self.tool_numbers)
        self.tactician = Agent(client=large_client, name="Tactician", instructions=TACTICIAN, tools=tools)
        self.backup = (Agent(client=small_client, name="TacticianBackup", instructions=TACTICIAN, tools=tools)
                       if backup and small_client is not large_client else None)
        self.judge = Agent(client=small_client, name="VerifierJudge", instructions=JUDGE) if use_judge else None
        self.challenger = Agent(client=small_client, name="Challenger", instructions=CHALLENGER) if challenger else None
        self.writers = {lang: Agent(client=small_client, name=f"Persona_{lang}", instructions=PERSONA)
                        for lang in langs}

    def _workflow(self):
        tactician = Tactician(self.tactician, self.backup, self.board)
        checker = Checker(self.causes, self.board)
        producer = Producer(self.board)
        judge = Judge(self.judge, self.board)
        rival = Challenger(self.challenger, self.stats, self.board)
        personas = [Persona(self.writers[lang], lang, self.stats, self.board) for lang in self.langs]
        gate = Gate(self.board)
        fallback = Fallback(self.board)
        return (WorkflowBuilder(start_executor=tactician, name="groundtruth-insight")
                .add_edge(tactician, checker)
                .add_edge(checker, tactician, condition=lambda m: isinstance(m, Job))
                .add_edge(checker, producer, condition=lambda m: isinstance(m, Approved))
                .add_edge(checker, fallback, condition=lambda m: isinstance(m, Rejected))
                .add_fan_out_edges(producer, [judge, rival, *personas])
                .add_fan_in_edges([judge, rival, *personas], gate)
                .add_edge(gate, tactician, condition=lambda m: isinstance(m, Job))
                .add_edge(gate, fallback, condition=lambda m: isinstance(m, Rejected))
                .build())

    async def explain(self, m: Moment, announce: bool = True) -> Outcome:
        pack = evidence_pack(self.stats, m)
        if announce:
            self.board(m.moment_id, "detected", kind=m.kind, team_id=m.team_id,
                       rule_cause=m.metrics.get("rule_cause"), score=m.metrics.get("score"))
        token = AS_OF.set(m.t)
        try:
            result = await self._workflow().run(Job(m, pack, tool_numbers=self.tool_numbers))
        finally:
            AS_OF.reset(token)
        outputs = result.get_outputs()
        if not outputs:
            return Outcome(m, None, [], ["the workflow produced no output"], 0, pack)
        outputs[0].pack = pack
        return outputs[0]
