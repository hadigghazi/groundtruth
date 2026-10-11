"""Pitch geometry.

One fixed frame for the whole match: x runs 0..105 along the pitch and y runs
0..68 across it. A team's own frame (u, v) is the fixed frame turned so that
the team always attacks towards u = 105. Turning by 180 degrees is its own
inverse, so the same function maps in both directions.
"""

from __future__ import annotations

import math

import numpy as np

LENGTH = 105.0
WIDTH = 68.0
GOAL_WIDTH = 7.32
POST_LOW = (WIDTH - GOAL_WIDTH) / 2
POST_HIGH = (WIDTH + GOAL_WIDTH) / 2
BOX_DEPTH = 16.5
BOX_HALF_WIDTH = 20.16
_SIZE = np.array([LENGTH, WIDTH])


def to_team(xy, direction: int) -> np.ndarray:
    """Map fixed-frame points, shape (2,) or (n, 2), into a team's frame."""
    xy = np.asarray(xy, dtype=float)
    return xy.copy() if direction == 1 else _SIZE - xy


from_team = to_team


def goal_angle(u: float, v: float) -> float:
    """Opening angle, in radians, of the goal mouth seen from (u, v)."""
    dx = LENGTH - u
    if dx <= 0:
        return 0.0
    return abs(math.atan2(POST_HIGH - v, dx) - math.atan2(POST_LOW - v, dx))


def in_box(u: float, v: float) -> bool:
    """True when (u, v), in the attacking team's frame, is inside the opponent's box."""
    return u >= LENGTH - BOX_DEPTH and abs(v - WIDTH / 2) <= BOX_HALF_WIDTH


def exit_point(start: np.ndarray, direction: np.ndarray) -> tuple[np.ndarray, str]:
    """Where a ray from `start` leaves the pitch, and which line it crosses."""
    hits = []
    if direction[0] > 1e-9:
        hits.append(((LENGTH - start[0]) / direction[0], "goal_line"))
    if direction[0] < -1e-9:
        hits.append((-start[0] / direction[0], "goal_line"))
    if direction[1] > 1e-9:
        hits.append(((WIDTH - start[1]) / direction[1], "touchline"))
    if direction[1] < -1e-9:
        hits.append((-start[1] / direction[1], "touchline"))
    t, line = min(hits)
    point = start + direction * max(t, 0.0)
    return np.clip(point, 0.0, _SIZE), line
