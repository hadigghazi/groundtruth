"""A match as the store holds it: what the API answers from, holding no match in memory.

It offers the same reads as a worker's live session (meta, moments, verified insights,
per-minute snapshots, stats windows), so the desk and the MCP tools run unchanged on
either. Windows come from the minute snapshots: the last 10 minutes and the 20 before.
"""

from __future__ import annotations

from ..schema import MatchMeta, Moment


class StoreView:
    def __init__(self, meta: MatchMeta, moments: list[Moment], results: dict[str, dict], snapshots: list[dict], t: int,
                 doc: dict):
        self.meta, self.moments, self.agent_results, self.minutes, self.t, self.doc = meta, moments, results, snapshots, t, doc
        self._teams = {meta.home.team_id: meta.home.short_name, meta.away.team_id: meta.away.short_name}
        self._people = {p.player_id: p.short_name for team in (meta.home, meta.away) for p in team.lineup + team.bench}

    @classmethod
    async def load(cls, store, match_id: str) -> StoreView | None:
        doc = await store.get(match_id, f"match-{match_id}")
        if not doc or not doc.get("meta"):
            return None
        moments = sorted((Moment.model_validate(d["moment"]) for d in await store.query(match_id, "moment")),
                         key=lambda m: m.t)
        results = {d["moment_id"]: d for d in await store.query(match_id, "result")}
        snaps = sorted(await store.query(match_id, "snapshot"), key=lambda s: s["t"])
        live = await store.get(match_id, f"live-{match_id}")
        t = max([live["t"] if live else 0, snaps[-1]["t"] if snaps else 0, moments[-1].t if moments else 0])
        return cls(MatchMeta.model_validate(doc["meta"]), moments, results, snaps, t, doc)

    def team_name(self, tid: str | None) -> str:
        return self._teams.get(tid or "", tid or "")

    def name(self, pid: str | None) -> str:
        return self._people.get(pid or "", pid or "")

    def as_of(self, t: int) -> dict | None:
        best = None
        for snap in self.minutes:
            if snap["t"] > t:
                break
            best = snap
        return best

    def windows_at(self, tid: str, t: int) -> dict:
        snap = self.as_of(t)
        if not snap or "windows" not in snap:
            return {}
        return {"last_10_min": snap["windows"]["recent"].get(tid, {}),
                "previous_20_min": snap["windows"]["baseline"].get(tid, {})}
