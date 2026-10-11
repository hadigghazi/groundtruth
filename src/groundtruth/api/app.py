"""HTTP and WebSocket API over live match sessions."""

from __future__ import annotations

import asyncio
import itertools
import logging
import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from ..league import build_league
from ..agents.guard import GUARD, summarize
from ..live.session import MatchSession
from ..schema import SCHEMA_VERSION, Cue
from ..sim.tactics import LABELS
from .speech import Speech

LEAGUE = build_league()
MAX_LIVE = 6
DELAY_S = float(os.environ.get("GROUNDTRUTH_DELAY_S", "12"))  # the viewer's broadcast delay, in wall seconds

app = FastAPI(title="GroundTruth API", version=__version__)
speech = Speech.from_env()
sessions: dict[str, MatchSession] = {}
_ids = itertools.count(1)


class NewMatch(BaseModel):
    home: str = "RVM"
    away: str = "HLB"
    seed: int = 7
    speed: float = Field(1.0, ge=0, le=600)
    scripted: bool = True


class Console(BaseModel):
    team_id: str
    label: Literal["high_press", "low_block", "go_direct", "slow_down", "push_wide"]


class Speed(BaseModel):
    speed: float = Field(ge=0, le=600)


class Outage(BaseModel):
    on: bool


class CatchUp(BaseModel):
    lang: Literal["en", "es", "ar"] = "en"
    mode: Literal["analyst", "casual"] = "casual"
    as_of_t: int | None = None  # the viewer's playhead; defaults to the live edge


class Ask(BaseModel):
    question: str = Field(min_length=2, max_length=300)
    lang: Literal["en", "es", "ar"] = "en"
    as_of_t: int | None = None


def _session(match_id: str) -> MatchSession:
    s = sessions.get(match_id)
    if s is None:
        raise HTTPException(404, f"no match {match_id}")
    return s


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": __version__, "live": sum(not s.finished for s in sessions.values()),
            "tts": speech is not None}


@app.get("/api/tts")
async def tts(text: str = Query(min_length=1, max_length=400), lang: Literal["en", "es", "ar"] = "en",
              mode: Literal["analyst", "casual"] = "analyst") -> Response:
    """Spoken commentary from Azure AI Speech neural voices; 404 when Speech is not configured."""
    if speech is None:
        raise HTTPException(404, "text to speech is not configured")
    if not speech.allow(len(text)):
        raise HTTPException(429, "the spoken-commentary budget for this hour is spent")
    try:
        audio = await speech.synthesize(text, lang, mode)
    except Exception as e:
        logging.getLogger("groundtruth").warning("tts failed: %s", e)
        raise HTTPException(502, "text to speech failed") from e
    return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/league")
def league() -> list[dict]:
    return [{"club_id": c.club_id, "name": c.name, "short_name": c.short_name, "ground": c.ground,
             "colors": list(c.colors), "formation": c.formation,
             "players": [{"player_id": p.player_id, "name": p.name, "number": p.number, "role": p.role}
                         for p in c.players]} for c in LEAGUE.values()]


@app.get("/api/console/labels")
def console_labels() -> dict:
    return {label: deltas for label, deltas in LABELS.items()}


@app.post("/api/matches", status_code=201)
async def create_match(req: NewMatch) -> dict:
    if req.home not in LEAGUE or req.away not in LEAGUE or req.home == req.away:
        raise HTTPException(422, "home and away must be two different clubs from /api/league")
    if sum(not s.finished for s in sessions.values()) >= MAX_LIVE:
        raise HTTPException(429, "too many live matches; try again when one ends")
    match_id = f"live{next(_ids):03d}"
    s = MatchSession(LEAGUE[req.home], LEAGUE[req.away], seed=req.seed, match_id=match_id,
                     speed=req.speed, scripted=req.scripted)
    sessions[match_id] = s
    _attach_agents(s)
    s.start()
    return {"match_id": match_id, "meta": s.meta.model_dump(mode="json")}


@app.get("/api/matches")
def list_matches() -> list[dict]:
    return [{"match_id": k, "home": s.meta.home.team_id, "away": s.meta.away.team_id,
             "finished": s.finished, "score": s.engine.score} for k, s in sessions.items()]


@app.get("/api/matches/{match_id}")
def match(match_id: str) -> dict:
    s = _session(match_id)
    return {"meta": s.meta.model_dump(mode="json"), "state": s.stats.snapshot(), "finished": s.finished,
            "speed": s.speed, "console": s.console_log}


@app.get("/api/matches/{match_id}/players")
def players(match_id: str) -> list[dict]:
    return [p.view() for p in _session(match_id).stats.players.values()]


@app.get("/api/matches/{match_id}/series/{metric}")
def series(match_id: str, metric: str) -> dict:
    s = _session(match_id)
    if metric not in s.stats.series:
        raise HTTPException(404, f"no series {metric}")
    return {team: [[t, v] for t, v in pts] for team, pts in s.stats.series[metric].items()}


@app.get("/api/matches/{match_id}/cues")
def cues(match_id: str, profile: str = "all", lang: str = "en") -> list[dict]:
    return [c.model_dump(mode="json") for c in _session(match_id).producer.sheet(profile, lang)]


@app.get("/api/schema/cue")
def cue_schema() -> dict:
    """The overlay cue's JSON Schema, versioned, for a graphics system to validate against."""
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": f"urn:groundtruth:cue:{SCHEMA_VERSION}",
            "x-schema-version": SCHEMA_VERSION, **Cue.model_json_schema()}


@app.get("/api/matches/{match_id}/overlays")
def overlays(match_id: str, profile: Literal["all", "analyst", "casual"] = "analyst", lang: str = "en",
             since_t: int = 0) -> dict:
    """The overlay timeline one viewer gets: shared cues plus their profile's, upgrades swapped in."""
    s = _session(match_id)
    return {"schema_version": SCHEMA_VERSION, "match_id": match_id, "profile": profile, "lang": lang,
            "t": s.stats.t, "cues": [c.model_dump(mode="json") for c in s.producer.visible(profile, lang)
                                     if c.t_out >= since_t]}


@app.get("/api/matches/{match_id}/scorecard")
def match_scorecard(match_id: str) -> dict:
    """What the system said against the answer key. Like the truth log, it opens at full time."""
    s = _session(match_id)
    if not s.finished:
        raise HTTPException(409, "the scorecard opens at full time")
    return s.full_time_card or s.scorecard()


@app.get("/api/matches/{match_id}/agents")
def match_agents(match_id: str) -> dict:
    s = _session(match_id)
    return {"runs": s.agent_runs, "summary": summarize(s.agent_runs), "outage": s.outage, "guard": GUARD.view()}


@app.post("/api/matches/{match_id}/agents/outage")
def set_outage(match_id: str, req: Outage) -> dict:
    """Simulate the model service going down: every card falls back to its template, live."""
    s = _session(match_id)
    s.outage = req.on
    s.publish({"type": "outage", "on": req.on})
    return {"ok": True, "outage": s.outage}


@app.post("/api/matches/{match_id}/catchup")
async def catch_up(match_id: str, req: CatchUp) -> dict:
    """A summary of the match up to the viewer's playhead, never beyond it."""
    from ..agents.desk import brief, fallback_recap

    s = _session(match_id)
    as_of = min(req.as_of_t if req.as_of_t is not None else s.stats.t, s.stats.t)
    desk = getattr(s, "desk", None)
    refused = "simulated model outage" if s.outage else (GUARD.admit() if desk else "agents are off")
    if refused:
        return {**fallback_recap(brief(s, as_of), req.lang), "verified": True, "fallback": True,
                "problems": [refused], "minute": brief(s, as_of)["minute"]}
    out = await desk.recap(as_of, req.lang, req.mode)
    GUARD.spend(out.get("tokens_in", 0) + out.get("tokens_out", 0))
    return out


@app.post("/api/matches/{match_id}/ask")
async def ask(match_id: str, req: Ask) -> dict:
    """A question about the match so far, answered by the desk agent from the match up to the playhead."""
    s = _session(match_id)
    desk = getattr(s, "desk", None)
    if desk is None or s.outage:
        raise HTTPException(503, "the desk is offline")
    refused = GUARD.admit()
    if refused:
        raise HTTPException(429, refused)
    as_of = min(req.as_of_t if req.as_of_t is not None else s.stats.t, s.stats.t)
    out = await desk.ask(req.question, as_of, req.lang)
    GUARD.spend(out.get("tokens_in", 0) + out.get("tokens_out", 0))
    return out


@app.get("/api/agents")
def agents_overview() -> dict:
    """Agent performance across every match on this server."""
    runs = [r for s in sessions.values() for r in s.agent_runs]
    return {"summary": summarize(runs), "guard": GUARD.view(), "matches": len(sessions)}


@app.get("/api/matches/{match_id}/truth")
def truth(match_id: str) -> list[dict]:
    """The answer key. Hidden until full time, so nothing downstream can peek."""
    s = _session(match_id)
    if not s.finished:
        raise HTTPException(409, "the truth log opens at full time")
    return [t.model_dump(mode="json") for t in s.engine.truth]


@app.post("/api/matches/{match_id}/console")
def console(match_id: str, req: Console) -> dict:
    s = _session(match_id)
    if req.team_id not in (s.meta.home.team_id, s.meta.away.team_id):
        raise HTTPException(422, "team_id must be one of the two teams in this match")
    if s.finished:
        raise HTTPException(409, "the match is over")
    s.intervene(req.team_id, req.label)
    return {"ok": True, "console": s.console_log[-1]}


@app.post("/api/matches/{match_id}/speed")
def set_speed(match_id: str, req: Speed) -> dict:
    s = _session(match_id)
    s.speed = req.speed
    s.publish({"type": "speed", "speed": s.speed})
    return {"ok": True, "speed": s.speed}


@app.websocket("/api/matches/{match_id}/stream")
async def stream(ws: WebSocket, match_id: str) -> None:
    s = sessions.get(match_id)
    if s is None:
        await ws.close(code=4404)
        return
    await ws.accept()
    q = s.subscribe()
    try:
        recent = [c for feed in s.producer.schedule.values() for c in feed if c.t_out >= s.stats.t - 120_000]
        b = getattr(s, "board", None)
        board = sorted((e for entries in b.items.values() for e in entries), key=lambda e: e["at_ms"]) if b else []
        await ws.send_json({"type": "hello", "meta": s.meta.model_dump(mode="json"), "speed": s.speed,
                            "delay_s": DELAY_S, "state": s.stats.snapshot(), "outage": s.outage,
                            "cues": [c.model_dump(mode="json") for c in recent], "board": board[-300:],
                            "console": s.console_log, "agents": summarize(s.agent_runs), "runs": s.agent_runs,
                            # a late joiner still needs the score so far and the timeline's markers
                            "events": [{**e.model_dump(mode="json", exclude_none=True),
                                        "derived": s.stats.derived.get(e.event_id)} for e in s.key_events],
                            "moments": [m.model_dump(mode="json") for m in s.stats.moments],
                            "full_time": s.full_time_card})
        if s.last_frame:
            await ws.send_json(s.last_frame)
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=15.0)
            except asyncio.TimeoutError:
                await ws.send_json({"type": "ping"})
                continue
            await ws.send_json(msg)
            if msg["type"] == "full_time":
                break
    except WebSocketDisconnect:
        pass
    finally:
        s.unsubscribe(q)


def _attach_agents(s: MatchSession) -> None:
    """Agents explain tactical shifts; without them the template cues still air."""
    if os.environ.get("GROUNDTRUTH_AGENTS", "1") == "0":
        return
    try:
        from ..agents.hook import attach_agents
    except ImportError:  # the agents extra is not installed
        return
    try:
        attach_agents(s, budget_s=float(os.environ.get("GROUNDTRUTH_AGENT_BUDGET_S", "20")))
    except Exception:  # a misconfigured model must not stop the match
        logging.getLogger("groundtruth").exception("agents not attached")


try:  # the match's tools over MCP, for any MCP client
    from .mcp_server import mount_mcp

    mount_mcp(app, sessions)
except Exception:  # not installed, or an SDK change: the match server runs without it
    logging.getLogger("groundtruth").warning("mcp is off", exc_info=True)

_web = os.environ.get("GROUNDTRUTH_WEB_DIR")
if _web and Path(_web).is_dir():  # the built web app, served after every /api route
    app.mount("/", StaticFiles(directory=_web, html=True), name="web")


if os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    # Traces from every agent run go to Application Insights, where Foundry's tracing view reads them.
    try:
        from microsoft.opentelemetry import use_microsoft_opentelemetry

        use_microsoft_opentelemetry(enable_azure_monitor=True)
    except Exception:
        logging.getLogger("groundtruth").exception("telemetry not configured")
