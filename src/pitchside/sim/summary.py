"""Match totals for calibrating the engine against real football.

The reference ranges are rough per-team-per-match values for top-flight
football, quoted from memory. They are wide on purpose: the target is
"looks like football", not a fit to any league's numbers.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np

from ..schema import Event
from .engine import Frame

TYPICAL = {  # per team per match, approximate
    "passes": (300, 700),
    "pass_completion": (0.70, 0.90),
    "shots": (5, 22),
    "shots_on_target": (1, 9),
    "goals": (0, 4),
    "corners": (1, 11),
    "fouls": (5, 18),
    "tackles": (8, 30),
    "interceptions": (4, 20),
}
TYPICAL_PLAYER_KM = (8.5, 13.0)  # outfield starters who play the whole match

_PLURAL = {"foul": "fouls", "tackle": "tackles", "interception": "interceptions",
           "pressure": "pressures", "carry": "carries", "recovery": "recoveries"}


def team_totals(events: list[Event], team_ids: tuple[str, str]) -> dict[str, dict[str, float]]:
    out = {t: defaultdict(float) for t in team_ids}
    for e in events:
        if e.team_id not in out:
            continue
        s = out[e.team_id]
        if e.type == "pass":
            s["passes"] += 1
            s["passes_complete"] += e.outcome == "complete"
        elif e.type == "shot":
            s["shots"] += 1
            s["shots_on_target"] += e.outcome in ("goal", "saved")
        elif e.type == "goal":
            s["goals"] += 1
        elif e.type == "restart" and e.restart == "corner":
            s["corners"] += 1
        elif e.type in _PLURAL:
            s[_PLURAL[e.type]] += 1
        elif e.type == "card":
            s[f"{e.card}_cards"] += 1
    for s in out.values():
        s["pass_completion"] = s["passes_complete"] / s["passes"] if s["passes"] else 0.0
    total = sum(out[t]["passes"] for t in team_ids) or 1
    for t in team_ids:
        out[t]["pass_share"] = out[t]["passes"] / total
    return {t: dict(v) for t, v in out.items()}


def player_running(frames: list[Frame]) -> dict[str, dict[str, float]]:
    """Distance (km), top speed (km/h) and minutes on the pitch per player, from the 10 Hz positions."""
    last: dict[str, tuple[int, int, np.ndarray]] = {}
    dist: dict[str, float] = defaultdict(float)
    hist: dict[str, list[float]] = defaultdict(list)
    top: dict[str, float] = defaultdict(float)
    ticks: dict[str, int] = defaultdict(int)
    for f in frames:
        for pid, xy in zip(f.ids, f.xy):
            prev = last.get(pid)
            step = 0.0
            if prev is not None and prev[0] == f.period and f.clock_ms - prev[1] == 100:
                step = float(np.linalg.norm(xy - prev[2]))
                if step >= 1.5:  # a kickoff or half-time reset, not running
                    step = 0.0
                dist[pid] += step
            last[pid] = (f.period, f.clock_ms, xy)
            ticks[pid] += 1
            h = hist[pid]
            h.append(step)
            if len(h) > 10:
                h.pop(0)
            if len(h) == 10:
                top[pid] = max(top[pid], sum(h) * 3.6)  # metres covered in the last second -> km/h
    return {pid: {"km": dist[pid] / 1000.0, "top_kmh": top[pid], "minutes": ticks[pid] / 600.0} for pid in dist}


def check(totals: dict[str, dict[str, float]]) -> list[str]:
    """Notes on totals that fall outside the typical ranges."""
    notes = []
    for team, s in totals.items():
        for key, (lo, hi) in TYPICAL.items():
            v = s.get(key, 0.0)
            if not lo <= v <= hi:
                notes.append(f"{team} {key}={v:.3g} outside {lo}-{hi}")
    return notes
