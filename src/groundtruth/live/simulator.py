"""The simulator role: plays matches, paced to the wall clock, onto the bus. It alone holds the truth.

It takes "start", "console" and "speed" commands from the control topic and publishes
each match as a feed a real data provider could send: "meta", then a "tick" every 0.1 s
of play (positions and the events that resolved), then "end".

The truth log (every hidden tactical change and when it took effect) never leaves this
process during play. At full time, once the workers say their agents have finished, the
simulator reads what was said from the store and grades it: the scorecard and the truth
log are written then, and only then.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from ..agents.guard import summarize
from ..evaluation.scorecard import scorecard
from ..league import build_league
from ..schema import Moment
from ..sim.engine import MatchEngine

log = logging.getLogger("groundtruth.simulator")
SEND_S = 0.1          # publish the ticks gathered over this much wall time together
FAST_BATCH = 25       # at full speed, ticks per publish
AGENTS_WAIT_S = 90.0  # how long full time waits for the workers' last agent runs
REGRADE_FOR_S = 900.0  # a worker that replays a match after a takeover reports late; grade again until then


@dataclass
class Running:
    match_id: str
    engine: MatchEngine
    speed: float
    truth_t: list[int] = field(default_factory=list)
    agents_done: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None
    t: int = 0


class SimulatorService:
    def __init__(self, platform):
        self.p = platform
        self.league = build_league()
        self.running: dict[str, Running] = {}
        self.graded: dict[str, tuple[Running, dict, dict, float]] = {}  # kept for late reports

    async def run(self) -> None:
        await self.p.bus.consume("control", "simulator", self._on_control)

    async def _on_control(self, msg: dict, d) -> None:
        kind, mid = msg["type"], msg.get("match_id", "")
        m = self.running.get(mid)
        if kind == "start" and m is None:
            doc = await self.p.store.get(mid, f"match-{mid}")
            if doc is None or doc.get("status") != "starting":  # an old command replayed: ignore
                await d.checkpoint()
                return
            engine = MatchEngine(self.league[msg["home"]], self.league[msg["away"]], seed=int(msg["seed"]),
                                 match_id=mid, scripted=bool(msg.get("scripted", True)))
            m = self.running[mid] = Running(mid, engine, float(msg.get("speed", 1.0)))
            m.task = asyncio.get_running_loop().create_task(self._play(m, doc))
        elif m is not None and kind == "console":
            m.engine.intervene(msg["team_id"], msg["label"])
            entry = {"t": m.t, "period": m.engine.period, "clock_ms": m.engine.clock_ms,
                     "team_id": msg["team_id"], "label": msg["label"]}
            await self.p.store.upsert([{"id": f"console-{mid}-{m.t}", "match_id": mid, "kind": "console", **entry}])
            await self.p.broadcaster.send(mid, [{"type": "console", **entry}])
        elif m is not None and kind == "speed":
            m.speed = float(msg["speed"])
            await self.p.broadcaster.send(mid, [{"type": "speed", "speed": m.speed}])
        elif m is not None and kind == "agents_done":
            m.agents_done.set()
        elif kind == "agents_done" and mid in self.graded:  # a replaying worker finished after we graded
            g, doc, meta, at = self.graded[mid]
            if time.time() - at <= REGRADE_FOR_S:
                log.info("%s: the workers reported late; grading again", mid)
                await self._full_time(g, doc, meta)
        await d.checkpoint()

    async def _play(self, m: Running, doc: dict) -> None:
        bus, mid = self.p.bus, m.match_id
        meta = m.engine.meta().model_dump(mode="json")
        await self.p.store.upsert([{**doc, "status": "live", "meta": meta, "started": time.time()}])
        await bus.publish("events", mid, [{"type": "meta", "match_id": mid, "meta": meta}])
        log.info("%s kicked off at speed %g", mid, m.speed)
        wall0, last_send, ticks, pending = time.perf_counter(), time.perf_counter(), 0, []
        pace_from, pace_ticks, pace_speed = wall0, 0, m.speed
        try:
            for events, frame in m.engine.run():
                while len(m.truth_t) < len(m.engine.truth):  # when each hidden change took effect, in play time
                    m.truth_t.append(m.t)
                pending.append({"type": "tick", "match_id": mid, "t": m.t, "frame": frame.to_dict(),
                                "events": [e.model_dump(mode="json", exclude_none=True) for e in events]})
                ticks += 1
                m.t += 100
                if ticks % 6000 == 0:
                    log.info("%s minute %d", mid, m.t // 60_000)
                if m.speed != pace_speed:  # re-anchor the pacing when the director changes speed
                    pace_from, pace_ticks, pace_speed = time.perf_counter(), ticks, m.speed
                now = time.perf_counter()
                if (m.speed > 0 and now - last_send >= SEND_S) or (m.speed <= 0 and len(pending) >= FAST_BATCH):
                    await bus.publish("events", mid, pending)
                    pending, last_send = [], now
                if m.speed > 0:
                    behind = pace_from + (ticks - pace_ticks) * 0.1 / m.speed - time.perf_counter()
                    if behind > 0:
                        await asyncio.sleep(behind)
                elif ticks % FAST_BATCH == 0:
                    await asyncio.sleep(0)
            pending.append({"type": "end", "match_id": mid, "score": m.engine.score})
            await bus.publish("events", mid, pending)
            log.info("%s final whistle %s; waiting for the workers", mid, m.engine.score)
            try:
                await asyncio.wait_for(m.agents_done.wait(), timeout=AGENTS_WAIT_S)
            except asyncio.TimeoutError:
                log.warning("%s: no word from the workers; grading what is in the store", mid)
            await self._full_time(m, doc, meta)
            self.graded[mid] = (m, doc, meta, time.time())
            for old in [k for k, v in self.graded.items() if time.time() - v[3] > REGRADE_FOR_S]:
                self.graded.pop(old, None)
        except Exception:
            log.exception("match %s failed", mid)
            await self.p.store.upsert([{**doc, "status": "failed", "meta": meta}])
        finally:
            self.running.pop(mid, None)

    async def _full_time(self, m: Running, doc: dict, meta: dict) -> None:
        store, mid = self.p.store, m.match_id
        moments = [Moment.model_validate(x["moment"]) for x in await store.query(mid, "moment")]
        results = {x["moment_id"]: x for x in await store.query(mid, "result")}
        runs = sorted((x["run"] for x in await store.query(mid, "run")), key=lambda r: r["t"])
        changes = [{"t": t, "team_id": c.team_id, "label": c.label, "source": c.source, "period": c.period,
                    "clock_ms": c.clock_ms} for t, c in zip(m.truth_t, m.engine.truth)]
        card = scorecard(changes, moments, results)
        truth = [{**c.model_dump(mode="json"), "t": t} for t, c in zip(m.truth_t, m.engine.truth)]
        latest = await store.get(mid, f"match-{mid}") or doc
        await store.upsert([
            {"id": f"truth-{mid}", "match_id": mid, "kind": "truth", "truth": truth, "scorecard": card},
            {**latest, "status": "finished", "meta": meta, "score": m.engine.score, "finished_at": time.time()},
        ])
        await self.p.broadcaster.send(mid, [{"type": "full_time", "score": m.engine.score, "scorecard": card,
                                             "agents": summarize(runs)}])
        log.info("%s graded: %d hidden changes, %d agent runs", mid, len(truth), len(runs))
