"""HTTP and WebSocket API over live match sessions."""

from __future__ import annotations

import asyncio
import itertools
import logging
import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from ..league import build_league
from ..live.session import MatchSession
from ..sim.tactics import LABELS

LEAGUE = build_league()
MAX_LIVE = 6

app = FastAPI(title="Pitchside API", version=__version__)
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


def _session(match_id: str) -> MatchSession:
    s = sessions.get(match_id)
    if s is None:
        raise HTTPException(404, f"no match {match_id}")
    return s


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": __version__, "live": sum(not s.finished for s in sessions.values())}


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
        await ws.send_json({"type": "hello", "meta": s.meta.model_dump(mode="json"), "speed": s.speed,
                            "state": s.stats.snapshot(), "cues": [c.model_dump(mode="json")
                                                                  for c in s.producer.sheet("all", "en")[-20:]]})
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
    if os.environ.get("PITCHSIDE_AGENTS", "1") == "0":
        return
    try:
        from ..agents.hook import attach_agents
    except ImportError:  # the agents extra is not installed
        return
    try:
        attach_agents(s, budget_s=float(os.environ.get("PITCHSIDE_AGENT_BUDGET_S", "12")))
    except Exception:  # a misconfigured model must not stop the match
        logging.getLogger("pitchside").exception("agents not attached")


_web = os.environ.get("PITCHSIDE_WEB_DIR")
if _web and Path(_web).is_dir():  # the built web app, served after every /api route
    app.mount("/", StaticFiles(directory=_web, html=True), name="web")


if os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    # Traces from every agent run go to Application Insights, where Foundry's tracing view reads them.
    try:
        from microsoft.opentelemetry import use_microsoft_opentelemetry

        use_microsoft_opentelemetry(enable_azure_monitor=True)
    except Exception:
        logging.getLogger("pitchside").exception("telemetry not configured")
