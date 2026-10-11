"""The worker role: interprets and explains every match on the partitions it owns.

It reads the simulator's feed from the bus, runs one MatchSession per match (stats,
captions, cues, agents), and every quarter of a second sends what the sessions produced:
live messages to the broadcaster, documents to the store.

Replicas share the feed's partitions. A partition is checkpointed only when no match on it
is still in play, so if a replica dies mid-match, the next owner replays that match from
its kick-off: the stats rebuild exactly (they are deterministic), documents are rewritten
under the same ids, and moments that already have an agent run are not explained twice.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict, deque

from ..schema import Event, MatchMeta
from ..sim.engine import Frame
from .session import MatchSession

log = logging.getLogger("groundtruth.worker")
FLUSH_S = 0.25


class WorkerService:
    def __init__(self, platform, *, agents: bool | None = None, llm: str | None = None, budget_s: float | None = None,
                 guard=None, keep_finished: int = 8):
        self.p = platform
        self.agents = os.environ.get("GROUNDTRUTH_AGENTS", "1") != "0" if agents is None else agents
        self.llm, self.guard = llm, guard
        self.budget_s = budget_s if budget_s is not None else float(os.environ.get("GROUNDTRUTH_AGENT_BUDGET_S", "20"))
        self.sessions: dict[str, MatchSession] = {}
        self.finished: deque[str] = deque()
        self.keep_finished = keep_finished
        self._skip: set[str] = set()
        self._active: dict[str, set[str]] = defaultdict(set)  # partition -> matches in play
        self._ending: set[asyncio.Task] = set()

    async def run(self) -> None:
        await asyncio.gather(self.p.bus.consume("events", "workers", self._on_event),
                             self.p.bus.consume("control", "workers", self._on_control),
                             self._flusher())

    # ------------------------------------------------------------------ the feed

    async def _on_event(self, msg: dict, d) -> None:
        mid, kind = msg["match_id"], msg["type"]
        if mid in self._skip:
            if kind == "end":
                self._skip.discard(mid)
            return
        if kind == "meta":
            if mid in self.sessions:
                return
            doc = await self.p.store.get(mid, f"match-{mid}")
            if doc and doc.get("status") == "finished":  # an old match still in the hub's retention
                self._skip.add(mid)
                return
            s = MatchSession(MatchMeta.model_validate(msg["meta"]))
            s.outage = bool(doc and doc.get("outage"))
            s.done_moments = {r["moment_id"] for r in await self.p.store.query(mid, "run")}
            if s.done_moments:
                log.warning("replaying %s from kick-off: %d moments already explained, skipped", mid, len(s.done_moments))
            if self.agents:
                self._attach(s)
            self.sessions[mid] = s
            self._active[d.partition].add(mid)
            log.info("%s on partition %s", mid, d.partition)
        elif kind == "tick":
            s = self.sessions.get(mid)
            if s is not None and not s.finished:
                s.feed([Event.model_validate(e) for e in msg["events"]], Frame.from_dict(msg["frame"]), msg["frame"])
                if s.t % 600_000 == 0:
                    log.info("%s minute %d", mid, s.t // 60_000)
        elif kind == "end":
            s = self.sessions.get(mid)
            if s is not None and not s.finished:
                task = asyncio.get_running_loop().create_task(self._end(s, d))
                self._ending.add(task)
                task.add_done_callback(self._ending.discard)

    def _attach(self, s: MatchSession) -> None:
        try:
            from ..agents.hook import attach_agents
        except ImportError:  # the agents extra is not installed: templates only
            return
        try:
            kwargs = {"guard": self.guard} if self.guard is not None else {}
            attach_agents(s, kind=self.llm, budget_s=self.budget_s, **kwargs)
        except Exception:  # a misconfigured model must not stop the match
            log.exception("agents not attached")

    async def _end(self, s: MatchSession, d) -> None:
        await s.finish()
        await self._flush(s)
        await self.p.bus.publish("control", s.match_id, [{"type": "agents_done", "match_id": s.match_id}])
        log.info("%s done: %d agent runs", s.match_id, len(s.agent_runs))
        active = self._active[d.partition]
        active.discard(s.match_id)
        if not active:  # nothing on this partition is still in play: safe to move the checkpoint
            await d.checkpoint()
        self.finished.append(s.match_id)
        while len(self.finished) > self.keep_finished:
            self.sessions.pop(self.finished.popleft(), None)

    async def _on_control(self, msg: dict, d) -> None:
        s = self.sessions.get(msg.get("match_id", ""))
        if s is not None and msg["type"] == "outage":
            s.outage = bool(msg["on"])

    # ------------------------------------------------------------------ output

    async def _flush(self, s: MatchSession) -> None:
        msgs, docs = s.drain()
        if msgs:
            await self.p.broadcaster.send(s.match_id, msgs)
        if docs:
            await self.p.store.upsert(docs)

    async def _flusher(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_S)
            for s in list(self.sessions.values()):
                try:
                    await self._flush(s)
                except Exception:
                    log.exception("flush failed for %s", s.match_id)
