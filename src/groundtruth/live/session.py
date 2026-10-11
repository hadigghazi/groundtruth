"""One live match: engine -> stats -> cues, paced to the wall clock, fanned out to viewers.

The session runs the five stages in order every tick and never waits on an
LLM: agents attach through `on_moment` hooks and hand their cues back later.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable

from ..league import Club
from ..render.cues import AGENT_KINDS, CueBuilder
from ..render.producer import Producer
from ..render.templates import LANGS
from ..schema import Cue, Moment
from ..sim.engine import MatchEngine
from ..stats.engine import StatsEngine

MomentHook = Callable[["MatchSession", Moment], Awaitable[None]]
MAX_FRAMES_PER_SECOND = 25


class MatchSession:
    def __init__(self, home: Club, away: Club, *, seed: int, match_id: str, speed: float = 1.0,
                 scripted: bool = True, langs: tuple[str, ...] = LANGS):
        self.match_id = match_id
        self.engine = MatchEngine(home, away, seed=seed, match_id=match_id, scripted=scripted)
        self.meta = self.engine.meta()
        self.stats = StatsEngine(self.meta)
        self.builder = CueBuilder(self.stats, langs)
        self.producer = Producer()
        self.speed = speed  # 0 = as fast as possible
        self.subscribers: set[asyncio.Queue] = set()
        self.hooks: list[MomentHook] = []
        self.console_log: list[dict] = []
        self.started = time.time()
        self.task: asyncio.Task | None = None
        self.last_frame: dict | None = None

    # ------------------------------------------------------------ fan-out

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    def publish(self, msg: dict) -> None:
        for q in list(self.subscribers):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:  # a slow viewer loses messages, never the match
                pass

    def place_cue(self, cue: Cue) -> Cue | None:
        placed = self.producer.place(cue)
        if placed is not None:
            self.publish({"type": "cue", "cue": placed.model_dump(mode="json")})
        return placed

    # ------------------------------------------------------------ control

    def intervene(self, team_id: str, label: str) -> None:
        self.engine.intervene(team_id, label)
        entry = {"t": self.stats.t, "period": self.engine.period, "clock_ms": self.engine.clock_ms,
                 "team_id": team_id, "label": label}
        self.console_log.append(entry)
        self.publish({"type": "console", **entry})

    @property
    def finished(self) -> bool:
        return self.engine.finished

    # ------------------------------------------------------------ the loop

    async def run(self) -> None:
        wall0 = time.perf_counter()
        ticks = 0
        stride = 1
        for events, frame in self.engine.run():
            moments = self.stats.feed_frame(frame)
            for ev in events:
                moments += self.stats.feed_event(ev)
                self.publish({"type": "event", "event": ev.model_dump(mode="json", exclude_none=True)})
            for m in moments:
                self._on_moment(m)
            if self.speed > 0:
                stride = max(1, math.ceil(10 * self.speed / MAX_FRAMES_PER_SECOND))
            if ticks % stride == 0:
                self.last_frame = {"type": "frame", "t": self.stats.t, **frame.to_dict()}
                self.publish(self.last_frame)
            if ticks % 50 == 0:
                self.publish({"type": "state", "state": self.stats.snapshot()})
            ticks += 1
            if self.speed > 0:
                behind = wall0 + ticks * 0.1 / self.speed - time.perf_counter()
                if behind > 0:
                    await asyncio.sleep(behind)
                elif ticks % 20 == 0:
                    await asyncio.sleep(0)
            elif ticks % 50 == 0:
                await asyncio.sleep(0)
        self.publish({"type": "state", "state": self.stats.snapshot()})
        self.publish({"type": "full_time", "score": self.engine.score})

    def _on_moment(self, m: Moment) -> None:
        self.publish({"type": "moment", "moment": m.model_dump(mode="json")})
        if self.hooks and m.kind in AGENT_KINDS:
            # agents own the wording; their hook places the template if they fail or run late
            for hook in self.hooks:
                asyncio.get_running_loop().create_task(hook(self, m))
            return
        for cue in self.builder.from_moment(m):
            self.place_cue(cue)

    def start(self) -> asyncio.Task:
        self.task = asyncio.get_running_loop().create_task(self.run())
        return self.task
