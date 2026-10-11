"""Run a whole match and write it to disk.

A match directory holds:
  meta.json      lineups, venue, attacking direction per half
  events.jsonl   raw events, one per line, in emission order
  frames.parquet 10 Hz positions, long format (one row per object per frame)
  truth.jsonl    hidden tactical changes; read only by the evaluation
  result.json    final score
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from ..league import Club
from ..schema import Event
from .engine import Frame, MatchEngine


def simulate(home: Club, away: Club, *, seed: int, match_id: str, scripted: bool = True):
    engine = MatchEngine(home, away, seed=seed, match_id=match_id, scripted=scripted)
    events: list[Event] = []
    frames: list[Frame] = []
    for evs, frame in engine.run():
        events.extend(evs)
        frames.append(frame)
    return engine, events, frames


def frames_table(frames: list[Frame]) -> pa.Table:
    n = sum(len(f.ids) + 1 for f in frames)
    period = np.empty(n, np.int8)
    clock = np.empty(n, np.int32)
    obj: list[str] = []
    xyz = np.zeros((n, 3), np.float32)
    k = 0
    for f in frames:
        m = len(f.ids) + 1
        period[k:k + m] = f.period
        clock[k:k + m] = f.clock_ms
        obj.append("ball")
        obj.extend(f.ids)
        xyz[k] = f.ball
        xyz[k + 1:k + m, :2] = f.xy
        k += m
    return pa.table({
        "period": period,
        "clock_ms": clock,
        "object_id": pa.array(obj).dictionary_encode(),
        "x": xyz[:, 0],
        "y": xyz[:, 1],
        "z": xyz[:, 2],
    })


def write_match(out_dir: Path, engine: MatchEngine, events: list[Event], frames: list[Frame]) -> Path:
    d = Path(out_dir) / engine.match_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(engine.meta().model_dump_json(indent=2), encoding="utf-8")
    with open(d / "events.jsonl", "w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(ev.model_dump_json(exclude_none=True) + "\n")
    with open(d / "truth.jsonl", "w", encoding="utf-8") as fh:
        for rec in engine.truth:
            fh.write(rec.model_dump_json() + "\n")
    pq.write_table(frames_table(frames), d / "frames.parquet", compression="zstd")
    (d / "result.json").write_text(json.dumps({
        "match_id": engine.match_id, "home": engine.team_ids[0], "away": engine.team_ids[1],
        "score": engine.score, "added_minutes": list(engine.added_min),
    }, indent=2), encoding="utf-8")
    return d
