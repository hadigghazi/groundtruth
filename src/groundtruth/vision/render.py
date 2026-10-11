"""Synthetic broadcast video: the engine's positions drawn through the broadcast camera.

Each frame gets exact labels for free, because the engine knows where everything is: the
pixel where every visible player's feet touch the grass, their side, and the ball. That
is what makes a trained detector possible here, and what lets every later step (finding
players, putting them on the pitch, tracking, rebuilding events and speeds) be graded.

The look is deliberately plain broadcast: stands, a striped pitch with its markings,
players in kit with shadows, compression noise. Nothing in a frame is a label.
"""

from __future__ import annotations

import math
import zlib

import numpy as np

from .. import pitch
from .camera import Camera

L, W = pitch.LENGTH, pitch.WIDTH
SKIN = [(196, 214, 238), (150, 180, 214), (110, 140, 185), (80, 105, 140), (60, 80, 110)]  # BGR
MARGIN = 6.0
HOME, AWAY, BALL = 0, 1, 2


def markings() -> tuple[np.ndarray, list[tuple[float, float, float, float, float]]]:
    """Every pitch line as segments (x0, y0, x1, y1) and arcs (cx, cy, r, a0, a1), as in the web views."""
    segs = [[0, 0, L, 0], [L, 0, L, W], [L, W, 0, W], [0, W, 0, 0], [L / 2, 0, L / 2, W]]
    for x0, s in ((0.0, 1.0), (L, -1.0)):
        for d, hw in ((16.5, 20.16), (5.5, 9.16)):
            segs += [[x0, W / 2 - hw, x0 + s * d, W / 2 - hw], [x0 + s * d, W / 2 - hw, x0 + s * d, W / 2 + hw],
                     [x0 + s * d, W / 2 + hw, x0, W / 2 + hw]]
    a = math.acos(5.5 / 9.15)  # the D: the part of the penalty arc outside the box
    arcs = [(L / 2, W / 2, 9.15, 0.0, 2 * math.pi), (11.0, W / 2, 9.15, -a, a), (L - 11.0, W / 2, 9.15, math.pi - a, math.pi + a)]
    return np.array(segs, dtype=float), arcs


def _hex_bgr(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16)


def kits(meta) -> dict[str, tuple[tuple, tuple, int]]:
    """player_id -> (shirt BGR, shorts BGR, side 0 home / 1 away). Keepers wear the second colour."""
    out = {}
    for side, team in enumerate((meta.home, meta.away)):
        main, alt = _hex_bgr(team.colors[0]), _hex_bgr(team.colors[1])
        for p in team.lineup + team.bench:
            gk = p.role == "GK"
            shirt = alt if gk else main
            shorts = tuple(int(c * 0.6) for c in (main if gk else alt))
            out[p.player_id] = (shirt, shorts, side)
    return out


class Renderer:
    def __init__(self, width: int = 384, height: int = 216, seed: int = 0):
        import cv2  # noqa: F401  (fail early if the vision extra is missing)

        self.w, self.h = width, height
        rng = np.random.default_rng(seed)
        crowd = rng.integers(20, 90, size=(height, width, 3)).astype(np.float32)
        speck = rng.random((height, width)) < 0.25
        crowd[speck] = rng.integers(90, 200, size=(int(speck.sum()), 3))
        self.crowd = crowd
        self.segments, self.arcs = markings()
        ys, xs = np.mgrid[0:height, 0:width]
        self.px, self.py = xs + 0.5, ys + 0.5

    def _ground(self, g: np.ndarray, valid: np.ndarray) -> np.ndarray:
        img = self.crowd.copy()
        gx, gy = np.nan_to_num(g[..., 0], nan=-999.0), np.nan_to_num(g[..., 1], nan=-999.0)
        on = valid & (gx > -MARGIN - 4) & (gx < L + MARGIN + 4) & (gy > -MARGIN - 4) & (gy < W + MARGIN + 4)
        stripe = (np.floor(gx / (L / 20)) % 2).astype(bool)
        img[on & stripe] = (52, 112, 34)
        img[on & ~stripe] = (60, 126, 40)
        # a pixel's footprint on the grass: lines stay visible, and thin, at any distance
        foot = np.abs(np.gradient(gx, axis=1)) + np.abs(np.gradient(gy, axis=0))
        half = np.maximum(0.07, 0.6 * np.minimum(foot, 3.0))
        d = np.full(gx.shape, np.inf)
        for x0, y0, x1, y1 in self.segments:
            vx, vy = x1 - x0, y1 - y0
            t = np.clip(((gx - x0) * vx + (gy - y0) * vy) / (vx * vx + vy * vy), 0, 1)
            d = np.minimum(d, np.hypot(gx - (x0 + t * vx), gy - (y0 + t * vy)))
        for cx, cy, r, a0, a1 in self.arcs:
            ang = (np.arctan2(gy - cy, gx - cx) - a0) % (2 * math.pi)
            span = (a1 - a0) % (2 * math.pi) or 2 * math.pi
            dist = np.abs(np.hypot(gx - cx, gy - cy) - r)
            d = np.minimum(d, np.where(ang <= span, dist, np.inf))
        line = on & (d < half)
        img[line] = 0.25 * img[line] + 0.75 * np.array([235, 240, 238], np.float32)
        return img

    def render(self, frame, cam: Camera, kit: dict, rng: np.random.Generator) -> tuple[np.ndarray, list[dict]]:
        import cv2

        def at(x, y, z):
            uv, depth = cam.project(np.array([[x, y, z]]))
            return float(uv[0, 0]), float(uv[0, 1]), float(depth[0])

        g = cam.to_ground(self.px, self.py)
        img = np.clip(self._ground(g, ~np.isnan(g[..., 0])), 0, 255).astype(np.uint8)  # drawing wants 8-bit
        labels, people = [], []
        for pid, (x, y) in zip(frame.ids, frame.xy):
            if pid not in kit:
                continue
            fx, fy, depth = at(x, y, 0.0)
            if depth <= 1:
                continue
            _, hy, _ = at(x, y, 1.85)
            people.append((depth, pid, fx, fy, fy - hy, float(x), float(y)))
        people.sort(reverse=True)  # far first, so nearer players cover them
        for _depth, pid, fx, fy, hpx, x, y in people:
            shirt, shorts, side = kit[pid]
            wpx = max(1.0, 0.36 * hpx)
            if not (-wpx < fx < self.w + wpx and 0 < fy < self.h + hpx):
                continue
            cv2.ellipse(img, (int(fx), int(fy)), (max(1, int(wpx * 0.8)), max(1, int(wpx * 0.25))), 0, 0, 360,
                        (20, 45, 15), -1, cv2.LINE_AA)
            legs_top, torso_top = fy - 0.47 * hpx, fy - 0.86 * hpx
            cv2.rectangle(img, (int(fx - wpx * 0.35), int(legs_top)), (int(fx + wpx * 0.35), int(fy)), shorts, -1)
            cv2.rectangle(img, (int(fx - wpx * 0.5), int(torso_top)), (int(fx + wpx * 0.5), int(legs_top)), shirt, -1)
            skin = SKIN[zlib.crc32(pid.encode()) % len(SKIN)]
            cv2.circle(img, (int(fx), int(fy - 0.93 * hpx)), max(1, int(0.075 * hpx)), skin, -1, cv2.LINE_AA)
            if 0 <= fx < self.w and 0 <= fy < self.h:
                labels.append({"kind": side, "player_id": pid, "u": fx, "v": fy, "h": float(hpx), "x": x, "y": y})
        bx, by, bz = (float(c) for c in frame.ball)
        cx, cy, depth = at(bx, by, bz + 0.11)
        if depth > 1 and 0 <= cx < self.w and 0 <= cy < self.h:
            r = max(1.2, cam.focal * 0.11 / depth)
            sx, sy, _ = at(bx, by, 0.0)
            cv2.ellipse(img, (int(sx), int(sy)), (max(1, int(r)), max(1, int(r * 0.4))), 0, 0, 360, (25, 50, 20), -1, cv2.LINE_AA)
            cv2.circle(img, (int(round(cx)), int(round(cy))), max(1, int(round(r))), (245, 245, 245), -1, cv2.LINE_AA)
            labels.append({"kind": BALL, "u": cx, "v": cy, "h": float(2 * r), "x": bx, "y": by, "z": bz})
        # the broadcast chain: a touch of blur, sensor noise, compression
        if rng.random() < 0.5:
            img = cv2.GaussianBlur(img, (3, 3), 0)
        img = np.clip(img.astype(np.float32) + rng.normal(0, 3.5, img.shape), 0, 255).astype(np.uint8)
        _, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(rng.integers(55, 88))])
        return cv2.imdecode(enc, cv2.IMREAD_COLOR), labels
