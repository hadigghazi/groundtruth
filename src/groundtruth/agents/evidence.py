"""The evidence pack an agent sees for one moment, and the numbers it may use.

Agents never read the engine or its truth log: only this pack (built from the
stats engine) and the tools that query the same state.

The pack carries only what changes from moment to moment. What each metric and
cause means is fixed, so it lives in the agents' instructions instead: every
call then starts with the same long prefix, which the model service can cache.
"""

from __future__ import annotations

import re

from ..schema import Moment
from ..stats.engine import BASELINE, MINUTE, RECENT, StatsEngine

NUMBER = re.compile(r"(?<![\w.])[-+]?\d+(?:[.,]\d+)?")

METRIC_NOTES = {
    "press_rate": "pressures in the opponent's half per opponent pass in their own 60%; higher = more pressing",
    "ppda": "opponent passes per defensive action in their half; lower = more pressing",
    "def_action_height_m": "average distance from own goal of defensive actions",
    "line_height_m": "back line's distance from own goal, adjusted for ball position",
    "block_height_m": "team's average distance from own goal out of possession",
    "block_height_adj_m": "team's average distance from own goal out of possession, adjusted for ball position",
    "width_m": "spread across the pitch in possession",
    "width_adj_m": "spread across the pitch in possession, adjusted for ball position",
    "long_pass_share": "share of passes over 30 m",
    "forward_share": "share of passes that gain more than 10 m",
    "time_on_ball_s": "seconds between receiving and passing",
    "high_turnovers": "possessions won within 40 m of goal, per 10 minutes",
    "pass_accuracy": "share of passes completed",
    "field_tilt": "share of both teams' touches in the final third that are this team's",
    "possession": "share of possession",
    "threat": "expected threat created",
}
RECENT_KEY = f"last_{RECENT // MINUTE}_min"
BASE_KEY = f"previous_{BASELINE // MINUTE}_min"


def _trim(x):
    """Three decimals carry every number a broadcast would show, at a fraction of the tokens."""
    if isinstance(x, float):
        return round(x, 3)
    if isinstance(x, dict):
        return {k: _trim(v) for k, v in x.items() if v is not None}
    if isinstance(x, (list, tuple)):
        return [_trim(v) for v in x]
    return x


def evidence_pack(stats: StatsEngine, m: Moment) -> dict:
    now = m.t
    recent = stats.window_metrics(now - RECENT, now)
    baseline = stats.window_metrics(now - RECENT - BASELINE, now - RECENT)
    teams = {}
    for tid in stats.team_ids:
        teams[tid] = {
            "name": stats.team_name(tid),
            "goals": stats.teams[tid].goals,
            RECENT_KEY: _trim(recent[tid]),
            BASE_KEY: _trim(baseline[tid]),
        }
    mt = dict(m.metrics)
    mt.pop("signals", None)
    return {
        "moment": _trim({"kind": m.kind, "team_id": m.team_id, "team": stats.team_name(m.team_id),
                         "minute": m.clock_ms // MINUTE, "period": m.period, **mt}),
        "teams": teams,
    }


def _forms(v: float) -> list[float]:
    out = [v, round(v), round(v, 1), round(v, 2)]
    if 0 < abs(v) <= 1:  # shares and rates also appear as percentages
        out += [round(100 * v), round(100 * v, 1)]
    return out


def allowed_numbers(pack: dict) -> list[float]:
    """Every number an agent may quote: values in the pack, in the forms people write them."""
    out: list[float] = []

    def walk(x):
        if isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            out.extend(_forms(float(x)))
        elif isinstance(x, dict):
            for y in x.values():
                walk(y)
        elif isinstance(x, (list, tuple)):
            for y in x:
                walk(y)

    walk(pack)
    walk([RECENT // MINUTE, BASELINE // MINUTE, 30, 40, 60])  # the windows and the fixed distances in the notes
    for team in pack.get("teams", {}).values():  # differences between the two windows are fair game too
        a, b = team.get(BASE_KEY, {}), team.get(RECENT_KEY, {})
        for k in a.keys() & b.keys():
            if isinstance(a[k], (int, float)) and isinstance(b[k], (int, float)):
                d = abs(b[k] - a[k])
                out.extend(_forms(d))
                # people often subtract the numbers as shown, not the exact ones
                out.extend(abs(round(b[k], p) - round(a[k], p)) for p in (0, 1, 2))
                if 0 < abs(a[k]) <= 1 and 0 < abs(b[k]) <= 1:
                    out.extend(abs(round(100 * b[k], p) - round(100 * a[k], p)) for p in (0, 1))
    return out


def numbers_in(text: str) -> list[float]:
    return [v for v, _ in numbers_with_places(text)]


def numbers_with_places(text: str) -> list[tuple[float, int]]:
    """Each number written in the text, with how many decimals it was written to."""
    out = []
    for n in NUMBER.findall(text or ""):
        n = n.replace(",", ".")
        out.append((float(n), len(n.split(".")[1]) if "." in n else 0))
    return out


def places_of(x: float) -> int:
    s = repr(round(float(x), 6))
    return len(s.split(".")[1].rstrip("0")) if "." in s else 0


def is_allowed(n: float, allowed: list[float], places: int | None = None) -> bool:
    """True if `n` is an allowed number written correctly to `places` decimals.

    A number written to one decimal must round to an allowed value at one decimal:
    31.6 for 31.62 is right, 31 is not (31.62 rounds to 32).
    """
    if places is None:
        places = places_of(n)
    tol = 0.5 * 10 ** -places + 1e-9
    return any(abs(n - a) <= tol for a in allowed)
