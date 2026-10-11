"""Play matches end to end in this process: simulator, bus, worker and store, as on Azure.

Tests, the agent evaluation and the command line use this, so they exercise the same
path a deployed match takes, only with the in-memory platform.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass

from .. import cloud
from .simulator import SimulatorService
from .worker import WorkerService


@dataclass
class Played:
    platform: cloud.Platform
    match_id: str
    worker: WorkerService
    simulator: SimulatorService

    @property
    def session(self):
        return self.worker.sessions.get(self.match_id)

    async def docs(self, kind: str) -> list[dict]:
        return await self.platform.store.query(self.match_id, kind)

    async def truth(self) -> dict | None:
        return await self.platform.store.get(self.match_id, f"truth-{self.match_id}")


async def play(home: str, away: str, seed: int, *, speed: float = 0.0, scripted: bool = True, agents: bool = True,
               llm: str | None = None, budget_s: float = 20.0, guard=None, platform: cloud.Platform | None = None,
               timeout_s: float = 1800.0) -> Played:
    p = platform or cloud.local()
    sim, worker = SimulatorService(p), WorkerService(p, agents=agents, llm=llm, budget_s=budget_s, guard=guard)
    tasks = [asyncio.create_task(sim.run()), asyncio.create_task(worker.run())]
    mid = f"m{secrets.token_hex(4)}"
    await p.store.upsert([{"id": f"match-{mid}", "match_id": mid, "kind": "match", "status": "starting", "home": home,
                           "away": away, "seed": seed, "speed": speed, "scripted": scripted, "outage": False,
                           "created": time.time()}])
    await p.bus.publish("control", mid, [{"type": "start", "match_id": mid, "home": home, "away": away, "seed": seed,
                                          "speed": speed, "scripted": scripted}])
    deadline = time.monotonic() + timeout_s
    try:
        while await p.store.get(mid, f"truth-{mid}") is None:
            if time.monotonic() > deadline:
                raise TimeoutError(f"match {mid} did not reach full time in {timeout_s:g} s")
            await asyncio.sleep(0.2)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return Played(p, mid, worker, sim)
