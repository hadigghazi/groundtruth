"""The broadcast camera: a pinhole model high in the main stand, panning with play.

The same framing as the web app's 3D view (vertical field of view 24 degrees, 16:9, 19 m
up, 32 m behind the near touchline), so rendered frames look like what a viewer sees.
Pitch coordinates are the engine's: x along the pitch (0..105 m), y across it (0..68 m,
0 at the camera's side), z up. `project` maps pitch points to pixels; `to_ground` maps
pixels back to the pitch along the camera ray, which is how detections become positions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .. import pitch

L, W = pitch.LENGTH, pitch.WIDTH
FOV_Y = 24.0
HEIGHT_M, BACK_M = 19.0, 32.0


@dataclass
class Camera:
    width: int
    height: int
    position: np.ndarray  # pitch coordinates of the lens
    target: np.ndarray    # the point it looks at

    @property
    def focal(self) -> float:
        return 0.5 * self.height / math.tan(math.radians(FOV_Y) / 2)

    def basis(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Right, up and forward unit vectors of the camera, in pitch coordinates."""
        f = self.target - self.position
        f = f / np.linalg.norm(f)
        r = np.cross(f, np.array([0.0, 0.0, 1.0]))
        r = r / np.linalg.norm(r)
        u = np.cross(r, f)
        return r, u, f

    def project(self, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(n, 3) pitch points -> (n, 2) pixels and (n,) depth along the view (negative = behind)."""
        r, u, f = self.basis()
        rel = np.atleast_2d(pts) - self.position
        x, y, z = rel @ r, rel @ u, rel @ f
        with np.errstate(divide="ignore", invalid="ignore"):
            px = self.width / 2 + self.focal * x / z
            py = self.height / 2 - self.focal * y / z
        return np.stack([px, py], axis=1), z

    def rays(self, px: np.ndarray, py: np.ndarray) -> np.ndarray:
        """Unit rays through pixel centres, in pitch coordinates; px, py any matching shape."""
        r, u, f = self.basis()
        x = (px - self.width / 2) / self.focal
        y = (self.height / 2 - py) / self.focal
        d = x[..., None] * r + y[..., None] * u + f
        return d / np.linalg.norm(d, axis=-1, keepdims=True)

    def to_ground(self, px: np.ndarray, py: np.ndarray, z: float = 0.0) -> np.ndarray:
        """Where each pixel's ray meets the plane at height z (NaN above the horizon)."""
        d = self.rays(np.asarray(px, float), np.asarray(py, float))
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (z - self.position[2]) / d[..., 2]
        out = self.position + t[..., None] * d
        out[t <= 0] = np.nan
        return out[..., :2]


class Director:
    """Pans the camera with the ball, smoothly, exactly as the web view does."""

    def __init__(self, width: int = 640, height: int = 360):
        self.width, self.height = width, height
        self.look = np.array([L / 2, W / 2, 0.0])

    def step(self, ball: np.ndarray) -> Camera:
        bx = float(np.clip(ball[0], 20, L - 20))
        # the web view aims a little short of the ball's line so the pitch fills the frame
        by = W / 2 + (ball[1] - W / 2) * 0.45 - 5
        self.look += 0.05 * (np.array([bx, by, 0.0]) - self.look)
        cx = L / 2 + (self.look[0] - L / 2) * 0.9
        return Camera(self.width, self.height, np.array([cx, -BACK_M, HEIGHT_M]), self.look.copy())
