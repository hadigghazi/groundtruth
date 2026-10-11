"""Observable features for the pass-difficulty and xG models.

Everything here comes from the event and the position frame at the moment of
the kick, the same inputs a real data feed provides. Nothing reads the
engine's hidden probabilities.
"""

from __future__ import annotations

import math

import numpy as np

from .. import pitch
from ..schema import Event

PASS_FEATURES = ["dist", "dist_sq", "forward", "lane_gap", "end_space", "under_pressure", "high"]
SHOT_FEATURES = ["goal_dist", "goal_angle", "header", "under_pressure", "blockers", "free_kick"]


def team_xy(x: float, y: float, direction: int) -> tuple[float, float]:
    u, v = pitch.to_team(np.array([x, y]), direction)
    return float(u), float(v)


def pass_features(ev: Event, direction: int, opp_xy: np.ndarray) -> dict[str, float]:
    """`opp_xy` holds the opponents' positions in the frame when the pass was struck."""
    p = np.array([ev.x, ev.y])
    q = np.array([ev.end_x, ev.end_y])
    seg = q - p
    dist = float(np.linalg.norm(seg))
    u0, _ = team_xy(ev.x, ev.y, direction)
    u1, _ = team_xy(ev.end_x, ev.end_y, direction)
    if len(opp_xy):
        len2 = max(float(seg @ seg), 1e-6)
        t = np.clip(((opp_xy - p) @ seg) / len2, 0.0, 1.0)
        gaps = np.linalg.norm(opp_xy - (p + t[:, None] * seg), axis=1)
        gaps = np.where(t < 0.12, np.inf, gaps)
        lane = float(min(gaps.min(), 8.0))
        space = float(min(np.linalg.norm(opp_xy - q, axis=1).min(), 10.0))
    else:
        lane, space = 8.0, 10.0
    return {
        "dist": dist,
        "dist_sq": dist * dist / 100.0,
        "forward": u1 - u0,
        "lane_gap": lane,
        "end_space": space,
        "under_pressure": float(ev.under_pressure),
        "high": float(ev.height == "high"),
    }


def shot_features(ev: Event, direction: int, opp_xy: np.ndarray) -> dict[str, float]:
    u, v = team_xy(ev.x, ev.y, direction)
    blockers = 0
    if len(opp_xy):
        ouv = pitch.to_team(opp_xy, direction)
        for uj, vj in ouv:
            du = uj - u
            if 0.5 < du < pitch.LENGTH - u:
                f = du / (pitch.LENGTH - u)
                lo = v + (pitch.POST_LOW - v) * f - 1.0
                hi = v + (pitch.POST_HIGH - v) * f + 1.0
                blockers += lo <= vj <= hi
    return {
        "goal_dist": math.hypot(pitch.LENGTH - u, pitch.WIDTH / 2 - v),
        "goal_angle": pitch.goal_angle(u, v),
        "header": float(ev.body_part == "head"),
        "under_pressure": float(ev.under_pressure),
        "blockers": float(min(blockers, 4)),
        "free_kick": float(ev.restart == "free_kick"),
    }
