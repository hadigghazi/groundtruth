"""One match inside a worker: stats, captions, cues and agents, from the simulator's ticks.

The session never sees the engine or its truth log: only what arrives on the bus, as a
real data provider's feed would. It never waits on a model either: every moment's
template card goes on air at once, and agents attach through hooks that upgrade those
cards later.

Everything it produces goes two ways, collected here and sent by the worker in batches:
live messages for viewers (the outbox) and documents for the store.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from ..agents.guard import summarize
from ..render.captions import CaptionWriter
from ..render.cues import AGENT_KINDS, CueBuilder
from ..render.producer import Producer
from ..render.templates import LANGS
from ..schema import Cue, Event, MatchMeta, Moment
from ..sim.engine import Frame
from ..stats.engine import BASELINE, MINUTE, RECENT, StatsEngine
from .storylines import Storylines

MomentHook = Callable[["MatchSession", Moment, list[Cue]], Awaitable[None]]
FRAME_GAP_S = 0.04  # viewers get at most 25 frames a second; they interpolate
KEY_EVENTS = ("goal", "card", "shot")


def _r(x):
    if isinstance(x, float):
        return round(x, 3)
    if isinstance(x, dict):
        return {k: _r(v) for k, v in x.items() if v is not None}
    return x


class MatchSession:
    def __init__(self, meta: MatchMeta, *, langs: tuple[str, ...] = LANGS):
        self.match_id = meta.match_id
        self.meta = meta
        self.stats = StatsEngine(meta)
        self.builder = CueBuilder(self.stats, langs)
        self.captions = CaptionWriter(self.stats, langs)
        self.producer = Producer()
        self.hooks: list[MomentHook] = []
        self.outage = False  # the director can simulate a model outage
        self.agent_runs: list[dict] = []
        self.agent_results: dict[str, dict] = {}  # moment_id -> the verified insight
        self.done_moments: set[str] = set()  # moments a previous owner already explained (replay)
        self.minutes: list[dict] = []  # one snapshot a minute, for answers as of a viewer's minute
        self.finished = False
        self.storylines = Storylines(self)
        self.recap: dict | None = None
        self.recap_writer = None  # set by attach_agents; without it the recap is written by code
        self.outbox: list[dict] = []
        self._docs: dict[str, dict] = {}
        self._boards: dict[str, list[dict]] = {}
        self._tasks: set[asyncio.Task] = set()
        self._next_minute = 0
        self._ticks = 0
        self._frame_wall = 0.0

    # ------------------------------------------------------------ outputs

    def doc(self, kind: str, doc_id: str, body: dict) -> None:
        self._docs[doc_id] = {"id": doc_id, "match_id": self.match_id, "kind": kind, **body}

    def publish(self, msg: dict) -> None:
        self.outbox.append(msg)
        kind = msg["type"]
        if kind == "cue":
            c = msg["cue"]
            self.doc("cue", c["cue_id"], c)
        elif kind == "moment":
            self.doc("moment", f"moment-{msg['moment']['moment_id']}", {"moment_id": msg["moment"]["moment_id"],
                                                                         "t": msg["moment"]["t"], "moment": msg["moment"]})
        elif kind == "board":
            e = msg["entry"]
            entries = self._boards.setdefault(e["moment_id"], [])
            entries.append(e)
            self.doc("board", f"board-{e['moment_id']}", {"moment_id": e["moment_id"], "entries": entries})
        elif kind == "agent_run":
            r = msg["run"]
            self.doc("run", f"run-{r['moment_id']}", {"moment_id": r["moment_id"], "t": r["t"], "run": r})
        elif kind == "state":
            self.doc("live", f"live-{self.match_id}", {"t": self.stats.t, "state": msg["state"]})

    def drain(self) -> tuple[list[dict], list[dict]]:
        msgs, docs = self.outbox, list(self._docs.values())
        self.outbox, self._docs = [], {}
        return msgs, docs

    def place_cue(self, cue: Cue) -> Cue | None:
        placed = self.producer.place(cue)
        if placed is not None:
            self.publish({"type": "cue", "cue": placed.model_dump(mode="json")})
        return placed

    def upgrade_cue(self, cue: Cue, old: Cue | None) -> Cue | None:
        """A verified card takes over the template's slot; with no template on air it queues like any cue."""
        if old is None:
            return self.place_cue(cue)
        placed = self.producer.upgrade(cue, old)
        self.publish({"type": "cue", "cue": placed.model_dump(mode="json")})
        return placed

    def record_run(self, run: dict) -> None:
        self.agent_runs.append(run)
        self.publish({"type": "agent_run", "run": run, "summary": summarize(self.agent_runs)})

    def save_result(self, moment_id: str, result: dict) -> None:
        self.agent_results[moment_id] = result
        self.doc("result", f"result-{moment_id}", {"moment_id": moment_id, **result})

    # ------------------------------------------------------------ the view the desk reads

    @property
    def t(self) -> int:
        return self.stats.t

    @property
    def moments(self) -> list[Moment]:
        return self.stats.moments

    def team_name(self, tid: str | None) -> str:
        return self.stats.team_name(tid)

    def name(self, pid: str | None) -> str:
        return self.stats.name(pid)

    def window(self, tid: str, lo: int, hi: int) -> dict:
        return {k: round(v, 3) for k, v in self.stats.window_metrics(lo, hi)[tid].items() if v is not None}

    def windows_at(self, tid: str, t: int) -> dict:
        return {"last_10_min": self.window(tid, t - RECENT, t),
                "previous_20_min": self.window(tid, t - RECENT - BASELINE, t - RECENT)}

    def as_of(self, t: int) -> dict | None:
        """The latest per-minute snapshot at or before play time t."""
        best = None
        for snap in self.minutes:
            if snap["t"] > t:
                break
            best = snap
        return best

    def _players(self) -> dict[str, list]:
        """Each player's running numbers, compact: km, top km/h, km/h now, passes, completed, shots, goals."""
        return {pid: [round(p.km, 2), round(p.top_kmh, 1), round(p.speed_kmh, 1), p.passes, p.passes_complete,
                      p.shots, p.goals] for pid, p in self.stats.players.items()}

    def _minute_snapshot(self) -> dict:
        st = self.stats
        now = st.t
        return {"t": now, "period": st.period, "clock_ms": st.clock_ms,
                "teams": {tid: st.teams[tid].view() for tid in st.team_ids},
                "players": {pid: p.view() for pid, p in st.players.items() if p.km > 0},
                "windows": {"recent": _r(st.window_metrics(now - RECENT, now)),
                            "baseline": _r(st.window_metrics(now - RECENT - BASELINE, now - RECENT))},
                "series": {k: {tid: pts[-1][1] for tid, pts in st.series[k].items() if pts}
                           for k in ("threat",)}}

    # ------------------------------------------------------------ the stages, once per tick

    def feed(self, events: list[Event], frame: Frame, frame_dict: dict | None = None) -> None:
        moments = self.stats.feed_frame(frame)
        for ev in events:
            moments += self.stats.feed_event(ev)
            derived = self.stats.derived.get(ev.event_id)
            body = ev.model_dump(mode="json", exclude_none=True)
            if ev.type in KEY_EVENTS:
                self.doc("event", f"event-{ev.event_id}", {"t": self.stats.t, "event": body, "derived": derived})
            # the event first, so a viewer has it before any cue that cites it
            self.publish({"type": "event", "event": body, "derived": derived})
            for cue in self.captions.from_event(ev):
                self.place_cue(cue)
        for m in moments:
            self._on_moment(m)
        now = time.perf_counter()
        if now - self._frame_wall >= FRAME_GAP_S:
            self._frame_wall = now
            self.publish({"type": "frame", "t": self.stats.t, **(frame_dict or frame.to_dict())})
        if self._ticks % 50 == 0:
            self.publish({"type": "state", "state": self.stats.snapshot(), "players": self._players()})
        if self.stats.t >= self._next_minute:
            self._next_minute += MINUTE
            snap = self._minute_snapshot()
            self.minutes.append(snap)
            self.doc("snapshot", f"snap-{self.match_id}-{len(self.minutes):03d}", snap)
            for story in self.storylines.update():
                self._on_story(story)
        self._ticks += 1

    def _on_story(self, story: dict) -> None:
        self.doc("storyline", story["id"], story)
        self.publish({"type": "storyline", "storyline": story})
        if story["status"] == "active":
            for cue in self.builder.from_storyline(story):
                self.place_cue(cue)

    def _recent_xg(self, minutes: int = 15) -> dict[str, float]:
        if len(self.minutes) <= minutes:
            return {tid: self.stats.teams[tid].xg for tid in self.stats.team_ids}
        then = self.minutes[-minutes - 1]["teams"]
        return {tid: self.stats.teams[tid].xg - then[tid]["xg"] for tid in self.stats.team_ids}

    def _on_moment(self, m: Moment) -> None:
        self.publish({"type": "moment", "moment": m.model_dump(mode="json")})
        if m.kind == "goal" and m.team_id:
            story = self.storylines.goal(m.team_id, self._recent_xg())
            if story:
                self._on_story(story)
        on_air = [c for c in (self.place_cue(cue) for cue in self.builder.from_moment(m)) if c is not None]
        if self.hooks and m.kind in AGENT_KINDS and m.moment_id not in self.done_moments:
            for hook in self.hooks:  # the agents upgrade these cards when they have something verified
                task = asyncio.get_running_loop().create_task(hook(self, m, on_air))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    async def finish(self, timeout: float = 30) -> None:
        """The final whistle: let the last moments' agents finish, then publish the final state."""
        if self._tasks:
            await asyncio.wait(set(self._tasks), timeout=timeout)
        self.publish({"type": "state", "state": self.stats.snapshot()})
        from ..agents.recap import write_recap

        self.recap = await write_recap(self, self.recap_writer, timeout=timeout)
        self.doc("recap", f"recap-{self.match_id}", {"recap": self.recap})
        self.publish({"type": "recap", "recap": self.recap})
        self.finished = True
