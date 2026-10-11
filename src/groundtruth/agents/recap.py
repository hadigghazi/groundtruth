"""The full-time recap package: what a studio needs the moment the whistle goes.

Code assembles the facts: the score, the goals, the storylines, the verified insights, the
player of the match (with the numbers that earned it) and a highlights rundown, a list of
clips with match-clock in and out points and a label per language that an editor or a
playout system can cut from. An agent then writes the words for each language and viewer
(headline, recap, a social post), checked in code like every other line; any language it
cannot get right falls back to a code-written recap.
"""

from __future__ import annotations

import asyncio
import time

from pydantic import BaseModel, Field

from ..stats.engine import MINUTE
from .evidence import allowed_numbers, is_allowed, numbers_with_places
from .verify import rulebook

FALLBACK = {
    "en": ("{home} {h}-{a} {away}", "{home} and {away} finished {h}-{a}. {goals} {story}",
           "Goals: {list}.", "No goals.", "{home} {h}-{a} {away}. {story}"),
    "es": ("{home} {h}-{a} {away}", "{home} y {away} terminaron {h}-{a}. {goals} {story}",
           "Goles: {list}.", "Sin goles.", "{home} {h}-{a} {away}. {story}"),
    "ar": ("{home} {h}-{a} {away}", "انتهت مباراة {home} و{away} بنتيجة {h}-{a}. {goals} {story}",
           "الأهداف: {list}.", "بلا أهداف.", "{home} {h}-{a} {away}. {story}"),
}
CLIPS = {"goal": (20, 8), "big_chance": (12, 5), "card": (8, 4), "tactical_shift": (60, 0)}


class RecapText(BaseModel):
    headline: str = Field(description="At most 70 characters")
    body: str = Field(description="A recap of 80 to 140 words")
    social: str = Field(description="A post of at most 240 characters")


class FullRecap(BaseModel):
    analyst: RecapText
    casual: RecapText


FULL_RECAP = """\
You write the full-time recap for a football broadcast, in one language, for two viewers at
once: an analyst (tactics, numbers, at most 5 numbers in the body) and a casual fan (the story,
plain words, at most 3 numbers). Use the pack only: the score, the goals with their minutes, the
storylines, the verified insights, the player of the match and the team totals. Nothing about
seasons, records, history or what comes next. Keep club and player names exactly as given.
headline: at most 70 characters. body: 80 to 140 words. social: at most 240 characters.
"""


def player_of_match(stats) -> dict | None:
    def score(p):
        return 3 * p.goals + 2 * p.xg + 0.5 * p.on_target + 0.3 * (p.tackles_won + p.interceptions) \
            + 0.02 * p.passes_complete + p.km / 20
    players = [p for p in stats.players.values() if p.km > 1]
    if not players:
        return None
    p = max(players, key=score)
    return {"player_id": p.player_id, "player": p.name, "team_id": p.team_id, "team": stats.team_name(p.team_id),
            "goals": p.goals, "xg": round(p.xg, 2), "shots": p.shots, "passes": p.passes,
            "pass_accuracy": round(p.passes_complete / p.passes, 2) if p.passes else None,
            "tackles_and_interceptions": p.tackles_won + p.interceptions, "km": round(p.km, 1),
            "top_kmh": round(p.top_kmh, 1)}


def highlights(session) -> list[dict]:
    """Clips around goals, big chances, cards and the tactical shifts the agents verified."""
    cues = {}
    for feed in session.producer.schedule.values():
        for c in feed:
            if c.moment_id and c.profile == "all":
                cues.setdefault((c.moment_id, c.lang), c.headline)
    out = []
    for m in session.stats.moments:
        pre_post = CLIPS.get(m.kind)
        if pre_post is None or (m.kind == "tactical_shift" and m.moment_id not in session.agent_results):
            continue
        pre, post = pre_post
        labels = {lang: cues.get((m.moment_id, lang), m.headline) for lang in session.builder.langs}
        if m.kind == "tactical_shift":
            labels = {lang: session.agent_results[m.moment_id].get("headline") or labels[lang] for lang in labels}
        out.append({"moment_id": m.moment_id, "kind": m.kind, "team_id": m.team_id, "minute": m.clock_ms // MINUTE + 1,
                    "t_in": max(0, m.t - pre * 1000), "t_out": m.t + post * 1000,
                    "start": {"period": m.period, "clock_ms": max(0, m.clock_ms - pre * 1000)},
                    "end": {"period": m.period, "clock_ms": m.clock_ms + post * 1000}, "label": labels})
    return out


def recap_pack(session) -> dict:
    st, meta = session.stats, session.meta
    h, a = st.team_ids
    poss = {tid: st.teams[tid].possession_ms for tid in (h, a)}
    total = sum(poss.values()) or 1
    teams = {}
    for team in (meta.home, meta.away):
        ts = st.teams[team.team_id]
        teams[team.team_id] = {"name": team.name, "short_name": team.short_name, "goals": ts.goals, "shots": ts.shots,
                               "on_target": ts.on_target, "xg": round(ts.xg, 2), "possession": round(poss[team.team_id] / total, 2),
                               "pass_accuracy": round(ts.passes_complete / ts.passes, 2) if ts.passes else None}
    return {
        "home": h, "away": a, "teams": teams,
        "goals": [{"minute": m.clock_ms // MINUTE + 1, "team": st.team_name(m.team_id), "player": st.name(m.player_id)}
                  for m in st.moments if m.kind == "goal"],
        "storylines": [{"kind": s["kind"], "minute": s["minute"], "team": st.team_name(s.get("team_id")),
                        "player": st.name(s.get("player_id")) if s.get("player_id") else None,
                        "what": s["headline"]["en"], "facts": s["facts"]} for s in session.storylines.all],
        "insights": sorted(({"minute": r["minute"] + 1, "team": st.team_name(r["team_id"]), "cause": r["cause"],
                             "headline": r.get("headline", "")} for r in session.agent_results.values()),
                           key=lambda i: i["minute"]),
        "player_of_match": player_of_match(st),
    }


def fallback(pack: dict, lang: str) -> dict:
    t = FALLBACK.get(lang, FALLBACK["en"])
    h, a = pack["teams"][pack["home"]], pack["teams"][pack["away"]]
    goals = t[2].format(list=", ".join(f"{g['player']} {g['minute']}'" for g in pack["goals"])) if pack["goals"] else t[3]
    story = pack["storylines"][-1]["what"] if pack["storylines"] and lang == "en" else ""
    v = {"home": h["short_name"], "away": a["short_name"], "h": h["goals"], "a": a["goals"], "goals": goals, "story": story}
    text = {"headline": t[0].format(**v), "body": t[1].format(**v).strip(), "social": t[4].format(**v).strip()}
    return {"analyst": text, "casual": text}


def _check(rt: RecapText, lang: str, allowed: list[float]) -> list[str]:
    problems = []
    for field, limit in (("headline", 80), ("body", 1100), ("social", 280)):
        text = getattr(rt, field)
        problems += [f"{field} uses {n:g}, which is not in the pack" for n, p in numbers_with_places(text)
                     if not is_allowed(n, allowed, p)]
        if len(text) > limit:
            problems.append(f"{field} is longer than {limit} characters")
        problems += rulebook(text, lang)
    return problems


class RecapWriter:
    def __init__(self, client):
        from agent_framework import Agent

        self.agent = Agent(client=client, name="RecapWriter", instructions=FULL_RECAP)

    async def write(self, pack: dict, lang: str) -> dict | None:
        from .pipeline import _parse
        from .prompts import LANG_NAMES, task

        allowed = allowed_numbers({"teams": {}, "x": pack})
        payload = {"task": "full_recap", "lang": lang, "language": LANG_NAMES[lang], "pack": pack}
        for _attempt in (1, 2):
            try:
                resp = await self.agent.run(task(payload), options={"response_format": FullRecap})
            except Exception:
                return None
            out = _parse(FullRecap, resp)
            if out is None:
                continue
            problems = _check(out.analyst, lang, allowed) + _check(out.casual, lang, allowed)
            if not problems:
                return out.model_dump()
            payload = {**payload, "your_previous_recap_was_rejected_because": problems}
        return None


async def write_recap(session, writer: RecapWriter | None, timeout: float = 30) -> dict:
    t0 = time.perf_counter()
    pack = recap_pack(session)
    langs = session.builder.langs
    texts = {lang: fallback(pack, lang) for lang in langs}
    by = {lang: "code" for lang in langs}
    if writer is not None:
        try:
            done = await asyncio.wait_for(asyncio.gather(*(writer.write(pack, lang) for lang in langs)), timeout=timeout)
            for lang, out in zip(langs, done):
                if out:
                    texts[lang], by[lang] = out, "agents"
        except asyncio.TimeoutError:
            pass
    return {"texts": texts, "written_by": by, "player_of_match": pack["player_of_match"], "goals": pack["goals"],
            "storylines": pack["storylines"], "highlights": highlights(session), "ms": round(1000 * (time.perf_counter() - t0))}
