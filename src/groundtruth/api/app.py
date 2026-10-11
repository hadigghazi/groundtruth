"""The API role: stateless HTTP over the store, commands onto the bus, viewers to the broadcaster.

It holds no match in memory. Reads come from the store (cues, runs, snapshots, the
scorecard); commands (start, console, speed, outage) go onto the control topic for the
simulator and the workers. On Azure, viewers connect to Web PubSub with a token from
/connect; locally, to this process's WebSocket.

With no platform configured, this process also runs the simulator and worker roles in
memory, so one `uvicorn` command is the whole system.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from ..agents.guard import GUARD, summarize
from ..cloud import Platform, from_env
from ..league import build_league
from ..live.view import StoreView
from ..schema import SCHEMA_VERSION, Cue
from ..sim.tactics import LABELS
from .speech import Speech

log = logging.getLogger("groundtruth")
LEAGUE = build_league()
MAX_LIVE = int(os.environ.get("GROUNDTRUTH_MAX_LIVE", "6"))
DELAY_S = float(os.environ.get("GROUNDTRUTH_DELAY_S", "12"))  # the viewer's broadcast delay, in wall seconds
LIVE = ("starting", "live")
speech = Speech.from_env()
_mcp = None


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    platform = from_env()
    app.state.platform = platform
    app.state.desk = None
    tasks = []
    if "simulator" in platform.roles:
        from ..live.simulator import SimulatorService

        app.state.simulator = SimulatorService(platform)
        tasks.append(asyncio.create_task(app.state.simulator.run()))
    if "worker" in platform.roles:
        from ..live.worker import WorkerService

        app.state.worker = WorkerService(platform)
        tasks.append(asyncio.create_task(app.state.worker.run()))
    log.info("platform %s", platform.describe)
    async with (_mcp.running() if _mcp else contextlib.nullcontext()):
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await platform.close()


app = FastAPI(title="GroundTruth API", version=__version__, lifespan=lifespan)


def P() -> Platform:
    return app.state.platform


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


# ------------------------------------------------------------------ reading the store

async def _match(match_id: str) -> dict:
    doc = await P().store.get(match_id, f"match-{match_id}")
    if doc is None:
        raise HTTPException(404, f"no match {match_id}")
    return doc


def _cue(d: dict) -> dict:
    return {k: v for k, v in d.items() if k not in ("id", "kind")}


def _visible(cues: list[dict], profile: str, lang: str) -> list[dict]:
    """What one viewer gets: the shared feed and their profile's, with upgraded cards swapped in."""
    feed = [c for c in cues if c["lang"] == lang and (c["profile"] == "all" or c["profile"] == profile)]
    gone = {r for c in feed for r in c.get("replaces", [])}
    return sorted((c for c in feed if c["cue_id"] not in gone), key=lambda c: c["t_in"])


async def _runs(match_id: str) -> list[dict]:
    return sorted((d["run"] for d in await P().store.query(match_id, "run")), key=lambda r: r["t"])


async def _hello(match_id: str) -> dict | None:
    """Everything a viewer joining now needs before the live stream: nothing past the live edge."""
    store = P().store
    doc = await store.get(match_id, f"match-{match_id}")
    if not doc or not doc.get("meta"):
        return None
    live = await store.get(match_id, f"live-{match_id}") or {}
    t = live.get("t", 0)
    cues = [_cue(d) for d in await store.query(match_id, "cue") if d["t_out"] >= t - 120_000]
    board = sorted((e for d in await store.query(match_id, "board") for e in d["entries"]), key=lambda e: e["at_ms"])
    runs = await _runs(match_id)
    truth = await store.get(match_id, f"truth-{match_id}") if doc.get("status") == "finished" else None
    recap = await store.get(match_id, f"recap-{match_id}")
    return {
        "type": "hello", "meta": doc["meta"], "speed": doc.get("speed", 1.0), "delay_s": DELAY_S,
        "state": live.get("state"), "outage": bool(doc.get("outage")), "cues": cues, "board": board[-400:],
        "console": sorted((_cue(d) for d in await store.query(match_id, "console")), key=lambda c: c["t"]),
        "agents": summarize(runs), "runs": runs,
        # a late joiner still needs the score so far and the timeline's markers
        "events": [{**d["event"], "derived": d.get("derived")} for d in sorted(await store.query(match_id, "event"),
                                                                              key=lambda d: d["t"])],
        "moments": [d["moment"] for d in sorted(await store.query(match_id, "moment"), key=lambda d: d["t"])],
        "full_time": truth["scorecard"] if truth else None,
        "storylines": sorted((_cue(d) for d in await store.query(match_id, "storyline")), key=lambda s: s["t"]),
        "recap": recap["recap"] if recap else None,
    }


# ------------------------------------------------------------------ endpoints

@app.get("/api/ping")
def ping() -> dict:
    """Liveness for the platform's probes: answers without touching the store."""
    return {"ok": True}


@app.get("/api/health")
async def health() -> dict:
    live = sum(m.get("status") in LIVE for m in await P().store.matches())
    return {"ok": True, "version": __version__, "live": live, "tts": speech is not None, "platform": P().describe}


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
        log.warning("tts failed: %s", e)
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
    if sum(m.get("status") in LIVE for m in await P().store.matches()) >= MAX_LIVE:
        raise HTTPException(429, "too many live matches; try again when one ends")
    match_id = f"m{secrets.token_hex(4)}"
    await P().store.upsert([{"id": f"match-{match_id}", "match_id": match_id, "kind": "match", "status": "starting",
                             "home": req.home, "away": req.away, "seed": req.seed, "speed": req.speed,
                             "scripted": req.scripted, "outage": False, "created": time.time()}])
    await P().bus.publish("control", match_id, [{"type": "start", "match_id": match_id, **req.model_dump()}])
    return {"match_id": match_id}


@app.get("/api/matches")
async def list_matches() -> list[dict]:
    docs = sorted(await P().store.matches(), key=lambda m: -m.get("created", 0))
    return [{"match_id": m["match_id"], "home": m["home"], "away": m["away"], "status": m.get("status"),
             "finished": m.get("status") == "finished", "score": m.get("score", [0, 0])} for m in docs[:30]]


@app.get("/api/matches/{match_id}")
async def match(match_id: str) -> dict:
    doc = await _match(match_id)
    live = await P().store.get(match_id, f"live-{match_id}") or {}
    console = sorted((_cue(d) for d in await P().store.query(match_id, "console")), key=lambda c: c["t"])
    return {"meta": doc.get("meta"), "status": doc.get("status"), "finished": doc.get("status") == "finished",
            "speed": doc.get("speed"), "state": live.get("state"), "console": console}


@app.get("/api/matches/{match_id}/connect")
async def connect(match_id: str) -> dict:
    """Where to stream from: a Web PubSub URL with a token for this match's group, or this server."""
    await _match(match_id)
    return {"hello": await _hello(match_id), "url": await P().broadcaster.client_url(match_id)}


async def _snapshots(match_id: str) -> list[dict]:
    return sorted(await P().store.query(match_id, "snapshot"), key=lambda s: s["t"])


@app.get("/api/matches/{match_id}/players")
async def players(match_id: str) -> list[dict]:
    snaps = await _snapshots(match_id)
    return list(snaps[-1]["players"].values()) if snaps else []


@app.get("/api/matches/{match_id}/series/{metric}")
async def series(match_id: str, metric: str) -> dict:
    out: dict[str, list] = {}
    for s in await _snapshots(match_id):
        for team, v in s.get("series", {}).get(metric, {}).items():
            out.setdefault(team, []).append([s["t"], v])
    if not out:
        raise HTTPException(404, f"no series {metric}")
    return out


@app.get("/api/matches/{match_id}/cues")
async def cues(match_id: str, profile: str = "all", lang: str = "en") -> list[dict]:
    docs = [_cue(d) for d in await P().store.query(match_id, "cue")]
    return sorted((c for c in docs if c["profile"] == profile and c["lang"] == lang), key=lambda c: c["t_in"])


@app.get("/api/schema/cue")
def cue_schema() -> dict:
    """The overlay cue's JSON Schema, versioned, for a graphics system to validate against."""
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": f"urn:groundtruth:cue:{SCHEMA_VERSION}",
            "x-schema-version": SCHEMA_VERSION, **Cue.model_json_schema()}


@app.get("/api/matches/{match_id}/overlays")
async def overlays(match_id: str, profile: Literal["all", "analyst", "casual"] = "analyst", lang: str = "en",
                   since_t: int = 0) -> dict:
    """The overlay timeline one viewer gets: shared cues plus their profile's, upgrades swapped in."""
    await _match(match_id)
    live = await P().store.get(match_id, f"live-{match_id}") or {}
    docs = [_cue(d) for d in await P().store.query(match_id, "cue")]
    return {"schema_version": SCHEMA_VERSION, "match_id": match_id, "profile": profile, "lang": lang,
            "t": live.get("t", 0), "cues": [c for c in _visible(docs, profile, lang) if c["t_out"] >= since_t]}


async def _truth(match_id: str) -> dict:
    doc = await _match(match_id)
    truth = await P().store.get(match_id, f"truth-{match_id}")
    if doc.get("status") != "finished" or truth is None:
        raise HTTPException(409, "the truth log and the scorecard open at full time")
    return truth


@app.get("/api/matches/{match_id}/scorecard")
async def match_scorecard(match_id: str) -> dict:
    """What the system said against the answer key. Like the truth log, it opens at full time."""
    return (await _truth(match_id))["scorecard"]


@app.get("/api/matches/{match_id}/truth")
async def truth(match_id: str) -> list[dict]:
    """The answer key. Hidden until full time, so nothing downstream can peek."""
    return (await _truth(match_id))["truth"]


@app.get("/api/matches/{match_id}/storylines")
async def storylines(match_id: str) -> list[dict]:
    """The running storylines, as they opened and closed."""
    await _match(match_id)
    return sorted((_cue(d) for d in await P().store.query(match_id, "storyline")), key=lambda s: s["t"])


@app.get("/api/matches/{match_id}/recap")
async def recap(match_id: str) -> dict:
    """The full-time package: recap texts per language and viewer, player of the match, highlights rundown."""
    await _match(match_id)
    doc = await P().store.get(match_id, f"recap-{match_id}")
    if doc is None:
        raise HTTPException(409, "the recap is written at full time")
    return doc["recap"]


@app.get("/api/matches/{match_id}/agents")
async def match_agents(match_id: str) -> dict:
    doc = await _match(match_id)
    runs = await _runs(match_id)
    return {"runs": runs, "summary": summarize(runs), "outage": bool(doc.get("outage")), "guard": GUARD.view()}


@app.post("/api/matches/{match_id}/agents/outage")
async def set_outage(match_id: str, req: Outage) -> dict:
    """Simulate the model service going down: every card falls back to its template, live."""
    doc = await _match(match_id)
    await P().store.upsert([{**doc, "outage": req.on}])
    await P().bus.publish("control", match_id, [{"type": "outage", "match_id": match_id, "on": req.on}])
    await P().broadcaster.send(match_id, [{"type": "outage", "on": req.on}])
    return {"ok": True, "outage": req.on}


def _desk():
    if app.state.desk is None:
        from ..agents.clients import make_clients
        from ..agents.desk import Desk

        app.state.desk = Desk(make_clients()[1])
    return app.state.desk


def _agents_on() -> bool:
    if os.environ.get("GROUNDTRUTH_AGENTS", "1") == "0":
        return False
    try:
        import agent_framework  # noqa: F401
    except ImportError:
        return False
    return True


@app.post("/api/matches/{match_id}/catchup")
async def catch_up(match_id: str, req: CatchUp) -> dict:
    """A summary of the match up to the viewer's playhead, never beyond it."""
    view = await StoreView.load(P().store, match_id)
    if view is None:
        raise HTTPException(409, "the match has not kicked off")
    as_of = min(req.as_of_t if req.as_of_t is not None else view.t, view.t)
    from ..agents.desk import brief, fallback_recap

    refused = ("simulated model outage" if view.doc.get("outage") else
               (GUARD.admit() if _agents_on() else "agents are off"))
    if refused:
        b = brief(view, as_of)
        return {**fallback_recap(b, req.lang), "verified": True, "fallback": True, "problems": [refused],
                "minute": b["minute"]}
    out = await _desk().recap(view, as_of, req.lang, req.mode)
    GUARD.spend(out.get("tokens_in", 0) + out.get("tokens_out", 0))
    return out


@app.post("/api/matches/{match_id}/ask")
async def ask(match_id: str, req: Ask) -> dict:
    """A question about the match so far, answered by the desk agent from the match up to the playhead."""
    view = await StoreView.load(P().store, match_id)
    if view is None:
        raise HTTPException(409, "the match has not kicked off")
    if view.doc.get("outage") or not _agents_on():
        raise HTTPException(503, "the desk is offline")
    refused = GUARD.admit()
    if refused:
        raise HTTPException(429, refused)
    as_of = min(req.as_of_t if req.as_of_t is not None else view.t, view.t)
    out = await _desk().ask(view, req.question, as_of, req.lang)
    GUARD.spend(out.get("tokens_in", 0) + out.get("tokens_out", 0))
    return out


@app.get("/api/agents")
async def agents_overview() -> dict:
    """Agent performance across every match in the store."""
    docs = await P().store.matches()
    runs = [r for m in docs for r in await _runs(m["match_id"])]
    return {"summary": summarize(runs), "guard": GUARD.view(), "matches": len(docs)}


@app.post("/api/matches/{match_id}/console")
async def console(match_id: str, req: Console) -> dict:
    doc = await _match(match_id)
    if req.team_id not in (doc["home"], doc["away"]):
        raise HTTPException(422, "team_id must be one of the two teams in this match")
    if doc.get("status") not in LIVE:
        raise HTTPException(409, "the match is over")
    await P().bus.publish("control", match_id, [{"type": "console", "match_id": match_id, **req.model_dump()}])
    return {"ok": True, "console": req.model_dump()}


@app.post("/api/matches/{match_id}/speed")
async def set_speed(match_id: str, req: Speed) -> dict:
    doc = await _match(match_id)
    await P().store.upsert([{**doc, "speed": req.speed}])
    await P().bus.publish("control", match_id, [{"type": "speed", "match_id": match_id, "speed": req.speed}])
    return {"ok": True, "speed": req.speed}


@app.websocket("/api/matches/{match_id}/stream")
async def stream(ws: WebSocket, match_id: str) -> None:
    """The live stream from this process (local mode). On Azure, viewers use Web PubSub via /connect."""
    b = P().broadcaster
    if not b.local:
        await ws.close(code=4400)
        return
    if await P().store.get(match_id, f"match-{match_id}") is None:
        await ws.close(code=4404)
        return
    await ws.accept()
    q = b.subscribe(match_id)  # before the hello, so nothing falls between the two
    try:
        hello = None
        for _ in range(150):
            hello = await _hello(match_id)
            if hello:
                break
            await asyncio.sleep(0.1)
        if hello is None:
            await ws.close(code=4408)
            return
        await ws.send_json(hello)
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=15.0)
            except asyncio.TimeoutError:
                await ws.send_json({"type": "ping"})
                continue
            await ws.send_json(msg)
            if any(m["type"] == "full_time" for m in msg.get("messages", [])):
                break
    except WebSocketDisconnect:
        pass
    finally:
        b.unsubscribe(match_id, q)


try:  # the match's tools over MCP, for any MCP client
    from .mcp_server import mount_mcp

    _mcp = mount_mcp(app, P)
except Exception:  # not installed, or an SDK change: the match server runs without it
    log.warning("mcp is off", exc_info=True)

_web = os.environ.get("GROUNDTRUTH_WEB_DIR")
if _web and Path(_web).is_dir():  # the built web app, served after every /api route
    app.mount("/", StaticFiles(directory=_web, html=True), name="web")


if os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    # Traces from every role go to Application Insights, where Foundry's tracing view reads the agents'.
    try:
        from microsoft.opentelemetry import use_microsoft_opentelemetry

        use_microsoft_opentelemetry(enable_azure_monitor=True)
    except Exception:
        log.exception("telemetry not configured")
