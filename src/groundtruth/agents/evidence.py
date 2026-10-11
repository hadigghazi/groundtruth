"""The evidence pack an agent sees for one moment, and the numbers it may use.

Agents never read the engine or its truth log: only this pack (built from the
stats engine) and the tools that query the same state.
"""

from __future__ import annotations

import re

from ..schema import Moment
from ..stats.engine import BASELINE, MINUTE, RECENT, StatsEngine
from .schemas import CAUSE_MEANING

NUMBER = re.compile(r"(?<![\w.])[-+]?\d+(?:[.,]\d+)?")


def evidence_pack(stats: StatsEngine, m: Moment) -> dict:
    now = m.t
    recent = stats.window_metrics(now - RECENT, now)
    baseline = stats.window_metrics(now - RECENT - BASELINE, now - RECENT)
    teams = {}
    for tid in stats.team_ids:
        teams[tid] = {
            "name": stats.team_name(tid),
            "goals": stats.teams[tid].goals,
            f"last_{RECENT // MINUTE}_min": {k: v for k, v in recent[tid].items() if v is not None},
            f"previous_{BASELINE // MINUTE}_min": {k: v for k, v in baseline[tid].items() if v is not None},
        }
    mt = dict(m.metrics)
    mt.pop("signals", None)
    return {
        "moment": {"kind": m.kind, "team_id": m.team_id, "team": stats.team_name(m.team_id),
                   "minute": m.clock_ms // MINUTE, "period": m.period, **mt},
        "teams": teams,
        "causes": CAUSE_MEANING,
        "metric_notes": {
            "press_rate": "pressures in the opponent's half per opponent pass in their own 60%; higher = more pressing",
            "ppda": "opponent passes per defensive action in their half; lower = more pressing",
            "def_action_height_m": "average distance from own goal of defensive actions",
            "line_height_m": "back line's distance from own goal, adjusted for ball position",
            "block_height_adj_m": "team's average distance from own goal out of possession, adjusted for ball position",
            "width_adj_m": "spread across the pitch in possession, adjusted for ball position",
            "long_pass_share": "share of passes over 30 m",
            "forward_share": "share of passes that gain more than 10 m",
            "time_on_ball_s": "seconds between receiving and passing",
            "high_turnovers": "possessions won within 40 m of goal, per 10 minutes",
        },
    }


def allowed_numbers(pack: dict) -> list[float]:
    """Every number an agent may quote: values in the pack, in the forms people write them."""
    out: list[float] = []

    def walk(x):
        if isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            v = float(x)
            out.extend([v, round(v), round(v, 1), round(v, 2)])
            if 0 < abs(v) <= 1:  # shares and rates also appear as percentages
                out.extend([round(100 * v), round(100 * v, 1)])
        elif isinstance(x, dict):
            for k, y in x.items():
                if k != "causes":
                    walk(y)
        elif isinstance(x, (list, tuple)):
            for y in x:
                walk(y)

    walk(pack)
    walk([RECENT // MINUTE, BASELINE // MINUTE])
    for team in pack["teams"].values():  # differences between the two windows are fair game too
        a, b = team.get(f"previous_{BASELINE // MINUTE}_min", {}), team.get(f"last_{RECENT // MINUTE}_min", {})
        for k in a.keys() & b.keys():
            d = abs(b[k] - a[k])
            out.extend([d, round(d), round(d, 1), round(d, 2)])
    return out


def numbers_in(text: str) -> list[float]:
    return [float(n.replace(",", ".")) for n in NUMBER.findall(text or "")]


def is_allowed(n: float, allowed: list[float]) -> bool:
    return any(abs(n - a) <= max(0.051, 0.02 * abs(a)) for a in allowed)
