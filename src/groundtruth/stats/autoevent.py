"""Events from positions alone: what the ingest stage can recover with tracking and no event feed.

From the 10 Hz frames only (22 players and the ball), each frame has a holder when the
ball is low and within reach of one player. Between two holders the ball was kicked:

* same team, different player: a completed pass, timed at the passer's last touch;
* the ball leaves the field across an end line near the goal, or reaches the opposing
  goalkeeper, fast, after a kick from shooting range: a shot;
* a fast ball towards goal from range, stopped by an outfield opponent within half a
  second: a blocked shot.

Scored against the engine's event log (which never feeds this module), this measures
how much of the event feed a tracking-only provider could reconstruct.
"""

from __future__ import annotations

import math

import numpy as np

from .. import pitch

L, W = pitch.LENGTH, pitch.WIDTH

REACH_M = 1.0       # a player holds the ball if it is within this distance and low
LOW_M = 1.2
SHOT_SPEED = 13.0   # m/s: faster than this towards goal from range reads as a shot
SHOT_RANGE_M = 35.0
GOAL_HALF_M = 11.0  # an end-line crossing this close to the goal centre counts as on the goal side
BLOCK_MS = 600      # a shot charged down arrives at a defender this fast
BLOCK_SPEED = 16.0
MATCH_MS = {"pass": 1500, "shot": 2500}


def detect(frames, team_of: dict[str, str], keepers: set[str]) -> dict[str, list[dict]]:
    passes, shots = [], []
    last = None  # (holder, period, clock_ms, x, y) at the holder's last touch
    flight: list[tuple[float, float, int]] = []
    shot_open = False
    for f in frames:
        b = f.ball
        holder = None
        if b[2] <= LOW_M:
            d = np.hypot(f.xy[:, 0] - b[0], f.xy[:, 1] - b[1])
            i = int(np.argmin(d))
            if d[i] < REACH_M:
                holder = f.ids[i]
        if holder is None:
            if last is not None and last[1] == f.period:
                flight.append((float(b[0]), float(b[1]), f.clock_ms))
                if not shot_open and _shot_like(last, flight):
                    shots.append({"player_id": last[0], "period": last[1], "clock_ms": last[2]})
                    shot_open = True
            continue
        if last is not None and holder != last[0] and f.period == last[1] and flight:
            same_team = team_of.get(holder) == team_of.get(last[0])
            if same_team:
                passes.append({"player_id": last[0], "receiver_id": holder, "period": f.period, "clock_ms": last[2]})
            elif holder in keepers and not shot_open and _fast_towards_goal(last, flight):
                shots.append({"player_id": last[0], "period": last[1], "clock_ms": last[2]})  # a save
            elif (not shot_open and f.clock_ms - last[2] <= BLOCK_MS and _speed(flight) >= BLOCK_SPEED
                  and _fast_towards_goal(last, flight)):
                shots.append({"player_id": last[0], "period": last[1], "clock_ms": last[2]})  # blocked
        last = (holder, f.period, f.clock_ms, float(b[0]), float(b[1]))
        flight, shot_open = [], False
    return {"pass": passes, "shot": shots}


def _speed(flight: list[tuple[float, float, int]]) -> float:
    best = 0.0
    for (x0, y0, t0), (x1, y1, t1) in zip(flight, flight[1:]):
        if t1 > t0:
            best = max(best, math.hypot(x1 - x0, y1 - y0) / ((t1 - t0) / 1000))
    return best


def _goal_x(x: float) -> float:
    return L if x > L / 2 else 0.0


def _fast_towards_goal(last, flight) -> bool:
    x0, y0 = last[3], last[4]
    gx = _goal_x(flight[-1][0])
    if math.hypot(gx - x0, W / 2 - y0) > SHOT_RANGE_M:
        return False
    return _speed(flight) >= SHOT_SPEED and abs(flight[-1][0] - gx) < abs(x0 - gx)


def _shot_like(last, flight) -> bool:
    """The ball has crossed an end line near the goal, fast, from shooting range."""
    x, y, _ = flight[-1]
    if 0.0 < x < L:
        return False
    return abs(y - W / 2) < GOAL_HALF_M and _fast_towards_goal(last, flight)


def score(detected: dict[str, list[dict]], events) -> dict:
    """Precision and recall per kind, matching on player (and receiver) within a time window."""
    truth = {
        "pass": [e for e in events if e.type == "pass" and e.outcome == "complete" and e.receiver_id],
        "shot": [e for e in events if e.type == "shot"],
    }
    out = {}
    for kind, found in detected.items():
        used, hits = set(), 0
        for d in found:
            for j, e in enumerate(truth[kind]):
                if j in used or e.period != d["period"] or e.player_id != d["player_id"]:
                    continue
                if kind == "pass" and e.receiver_id != d["receiver_id"]:
                    continue
                if abs(e.clock_ms - d["clock_ms"]) <= MATCH_MS[kind]:
                    used.add(j)
                    hits += 1
                    break
        n_true, n_found = len(truth[kind]), len(found)
        p = hits / n_found if n_found else 0.0
        r = hits / n_true if n_true else 0.0
        out[kind] = {"events": n_true, "detected": n_found, "matched": hits, "precision": round(p, 3),
                     "recall": round(r, 3), "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0}
    return out


def run(home, away, seed: int) -> dict:
    """Play one match, keep its frames, detect from positions and score against the log."""
    from ..sim.engine import MatchEngine

    engine = MatchEngine(home, away, seed=seed, match_id=f"ae{seed}")
    frames, events = [], []
    for evs, frame in engine.run():
        frames.append(frame)
        events.extend(evs)
    meta = engine.meta()
    team_of = {p.player_id: t.team_id for t in (meta.home, meta.away) for p in t.lineup + t.bench}
    keepers = {p.player_id for t in (meta.home, meta.away) for p in t.lineup + t.bench if p.role == "GK"}
    return score(detect(frames, team_of, keepers), events)
