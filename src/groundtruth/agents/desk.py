"""The studio desk: "catch me up" and "ask the match", both as of the viewer's playhead.

A viewer watches on a delay, and may have rewound. Everything here is answered as of
their play time: the per-minute snapshots, the moments and the verified insights up to
then, and stats windows that count back from then. Nothing later can leak in.

Everything reads a *view* of the match: a worker's live session, or (in the API, which
holds no match in memory) the documents in the store. The same read-only functions serve
the desk agents (as Agent Framework tools) and outside clients (as MCP tools, see
api/mcp_server.py). Every number an answer uses must
come from the brief or a tool result, checked in code like everything else on screen;
when a model is unavailable or fails the checks, a code-written recap stands in.
"""

from __future__ import annotations

import functools
import time

from agent_framework import Agent, tool
from pydantic import BaseModel, Field

from ..stats.engine import MINUTE
from .evidence import allowed_numbers, is_allowed, numbers_with_places
from .pipeline import AS_OF, _parse, usage
from .prompts import LANG_NAMES, task
from .verify import rulebook

KEY_KINDS = ("goal", "card", "big_chance", "tactical_shift", "substitution", "momentum_swing")


class Answer(BaseModel):
    answer: str = Field(description="Two or three sentences at most, in the requested language")
    out_of_scope: bool = Field(False, description="True if the question cannot be answered from this match so far")


class Recap(BaseModel):
    headline: str = Field(description="At most 70 characters")
    body: str = Field(description="Two to four short sentences")


ASK = """\
You are the analyst on a live football studio desk. A viewer asks about the match they are
watching. They are watching on a delay, so answer only from the match up to their minute: the
brief and your tools already stop there. Use the tools for anything the brief does not cover.
Rules (a code verifier enforces them):
- Use only numbers from the brief or a tool result, copied or correctly rounded.
- Nothing about seasons, records, history, other matches or what will happen next. If the
  question needs any of that, or anything after the viewer's minute, set out_of_scope and say
  briefly what you can tell them instead.
- Answer in the requested language, in two or three sentences, plain broadcast English (or
  Spanish, or Modern Standard Arabic). Keep club and player names exactly as given.
"""

RECAP = """\
You write a "catch me up" summary for a viewer joining a live football match, as of their
minute. Lead with the score, then the goals and the one or two most telling changes, using the
verified insights where there are any. Use only facts and numbers in the pack. Nothing about
seasons, records or what will happen. headline: at most 70 characters. body: two to four short
sentences, in the requested language. Keep club and player names exactly as given.
"""

FALLBACK = {
    "en": ("{home} {h}-{a} {away} after {m} minutes", "Goals: {goals}.", "No goals yet.", "Latest: {latest}."),
    "es": ("{home} {h}-{a} {away} tras {m} minutos", "Goles: {goals}.", "Todavía no hay goles.", "Lo último: {latest}."),
    "ar": ("{home} {h}-{a} {away} بعد {m} دقيقة", "الأهداف: {goals}.", "لا أهداف حتى الآن.", "آخر المستجدات: {latest}."),
}


# ------------------------------------------------------------------ the briefs

def brief(view, as_of: int) -> dict:
    """The match up to play time `as_of`: score, totals, key moments, verified insights."""
    meta, snap = view.meta, view.as_of(as_of)
    teams = (snap or {}).get("teams", {})
    minute = ((snap or {}).get("clock_ms", 0)) // MINUTE
    out_teams = {}
    for team in (meta.home, meta.away):
        v = teams.get(team.team_id, {})
        out_teams[team.team_id] = {"name": team.name, "short_name": team.short_name,
                                   **{k: v.get(k) for k in ("goals", "shots", "on_target", "xg", "pass_accuracy",
                                                            "corners", "fouls") if v.get(k) is not None}}
    poss = {tid: teams.get(tid, {}).get("possession_ms", 0) for tid in out_teams}
    total = sum(poss.values()) or 1
    for tid in out_teams:
        out_teams[tid]["possession"] = round(poss[tid] / total, 3)
    seen = [m for m in view.moments if m.t <= as_of]
    moments = [{"minute": m.clock_ms // MINUTE + 1, "kind": m.kind, "team": view.team_name(m.team_id),
                "what": m.headline} for m in seen if m.kind in KEY_KINDS]
    goals = [{"minute": m.clock_ms // MINUTE + 1, "team": view.team_name(m.team_id), "player": view.name(m.player_id)}
             for m in seen if m.kind == "goal"]
    insights = [{"minute": r["minute"] + 1, "team": view.team_name(r["team_id"]), "cause": r["cause"],
                 "headline": r.get("headline", "")} for r in view.agent_results.values() if r["t"] <= as_of]
    return {"minute": minute, "home": meta.home.team_id, "away": meta.away.team_id, "teams": out_teams,
            "goals": goals, "moments": moments[-14:], "insights": sorted(insights, key=lambda i: i["minute"])[-6:]}


def fallback_recap(b: dict, lang: str) -> dict:
    t = FALLBACK.get(lang, FALLBACK["en"])
    home, away = b["teams"][b["home"]], b["teams"][b["away"]]
    goals = [f"{g['player']} {g['minute']}'" for g in b["goals"]]
    body = [t[1].format(goals=", ".join(goals)) if goals else t[2]]
    if b["insights"]:
        body.append(t[3].format(latest=b["insights"][-1]["headline"]))
    return {"headline": t[0].format(home=home["short_name"], away=away["short_name"], h=home.get("goals", 0),
                                    a=away.get("goals", 0), m=b["minute"]), "body": " ".join(body)}


# ------------------------------------------------------------------ the tools

def desk_functions(view):
    """Plain read-only functions over a view of the match, as of AS_OF (or the live edge)."""

    def now() -> int:
        return min(AS_OF.get() or view.t, view.t)

    def team_window(team_id: str, minutes_before_now: int = 0) -> dict:
        """One team's pressing, shape, tempo and directness: the last 10 minutes and the 20 before them,
        ending a number of minutes before the viewer's minute (0 means up to now)."""
        return view.windows_at(team_id, now() - max(0, minutes_before_now) * MINUTE)

    def player_report(team_id: str) -> list[dict]:
        """Each player's running, passing and shooting so far: km, top speed, passes, accuracy, shots, goals."""
        snap = view.as_of(now()) or {"players": {}}
        rows = [{"player": p["name"], "km": round(p["km"], 2), "top_kmh": round(p["top_kmh"], 1), "passes": p["passes"],
                 "pass_accuracy": p["pass_accuracy"], "shots": p["shots"], "goals": p["goals"]}
                for p in snap["players"].values() if p["team_id"] == team_id]
        return sorted(rows, key=lambda r: -r["km"])

    def key_moments(kind: str = "") -> list[dict]:
        """Goals, cards, big chances, tactical shifts and substitutions so far; filter by kind if given."""
        t = now()
        return [{"minute": m.clock_ms // MINUTE + 1, "kind": m.kind, "team": view.team_name(m.team_id), "what": m.headline}
                for m in view.moments if m.t <= t and m.kind in KEY_KINDS and (not kind or m.kind == kind)]

    return {"team_window": team_window, "player_report": player_report, "key_moments": key_moments}


def _check(text: str, lang: str, allowed: list[float], max_len: int) -> list[str]:
    problems = [f"uses {n:g}, which is not in the brief or a tool result at that precision"
                for n, places in numbers_with_places(text) if not is_allowed(n, allowed, places)]
    if len(text) > max_len:
        problems.append(f"longer than {max_len} characters")
    return problems + rulebook(text, lang)


class Desk:
    """One desk per API process; each request brings the view it answers from."""

    def __init__(self, client):
        self.client = client
        self.recap_agent = Agent(client=client, name="CatchMeUp", instructions=RECAP)

    def _ask_agent(self, view, record: list[float]) -> Agent:
        def keep(f):  # every number a tool returns becomes quotable
            @functools.wraps(f)
            def wrapped(*a, **k):
                out = f(*a, **k)
                record.extend(allowed_numbers({"teams": {}, "x": out}))
                return out
            return tool(approval_mode="never_require")(wrapped)

        return Agent(client=self.client, name="AnalystDesk", instructions=ASK,
                     tools=[keep(f) for f in desk_functions(view).values()])

    async def _run(self, agent: Agent, model, payload: dict, allowed: list[float], lang: str, max_len: int, text_of,
                   record: list[float] | None = None):
        """One try, then one retry with the problems listed. Returns (parsed or None, problems, usage, ms)."""
        t0, problems, used = time.perf_counter(), [], {}
        for _attempt in (1, 2):
            if problems:
                payload = {**payload, "your_previous_answer_was_rejected_because": problems}
            try:
                resp = await agent.run(task(payload), options={"response_format": model})
            except Exception as e:
                return None, [f"{type(e).__name__}: {e}"], used, round(1000 * (time.perf_counter() - t0))
            out, used = _parse(model, resp), usage(resp)
            problems = (["the reply did not match the format"] if out is None
                        else _check(text_of(out), lang, allowed + (record or []), max_len))
            if not problems:
                return out, [], used, round(1000 * (time.perf_counter() - t0))
        return None, problems, used, round(1000 * (time.perf_counter() - t0))

    async def recap(self, view, as_of: int, lang: str, mode: str) -> dict:
        token = AS_OF.set(as_of)
        try:
            b = brief(view, as_of)
            payload = {"task": "recap", "lang": lang, "language": LANG_NAMES[lang], "viewer": mode, "pack": b}
            out, problems, used, ms = await self._run(self.recap_agent, Recap, payload, allowed_numbers({"teams": {}, "x": b}),
                                                      lang, 500, lambda r: f"{r.headline} {r.body}")
        finally:
            AS_OF.reset(token)
        if out is None:
            return {**fallback_recap(b, lang), "verified": True, "fallback": True, "problems": problems,
                    "minute": b["minute"], "ms": ms, **used}
        return {"headline": out.headline, "body": out.body, "verified": True, "fallback": False, "minute": b["minute"],
                "ms": ms, **used}

    async def ask(self, view, question: str, as_of: int, lang: str) -> dict:
        token = AS_OF.set(as_of)
        record: list[float] = []
        try:
            b = brief(view, as_of)
            payload = {"task": "ask", "lang": lang, "language": LANG_NAMES[lang], "question": question[:300], "brief": b}
            out, problems, used, ms = await self._run(self._ask_agent(view, record), Answer, payload,
                                                      allowed_numbers({"teams": {}, "x": b}), lang, 700,
                                                      lambda a: a.answer, record)
        finally:
            AS_OF.reset(token)
        if out is None:
            return {"answer": None, "verified": False, "problems": problems, "minute": b["minute"], "ms": ms, **used}
        return {"answer": out.answer, "out_of_scope": out.out_of_scope, "verified": True, "minute": b["minute"],
                "ms": ms, **used}
