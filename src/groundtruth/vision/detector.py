"""A small keypoint detector for broadcast frames: players by side, and the ball.

CenterNet-style: one heatmap per class at a quarter of the input resolution, and a
sub-pixel offset. A player's keypoint is where their feet meet the grass, so a detection
maps straight onto the pitch through the camera model. About 150 thousand weights: it
trains on a laptop CPU in minutes and runs at broadcast frame rate on one core.
"""

from __future__ import annotations

import numpy as np
import torch
import torch.nn.functional as F
from torch import nn

STRIDE = 4
CLASSES = ("home", "away", "ball")
THRESHOLD = {0: 0.35, 1: 0.35, 2: 0.30}


def _block(cin: int, cout: int, stride: int = 1) -> nn.Sequential:
    return nn.Sequential(nn.Conv2d(cin, cout, 3, stride, 1, bias=False), nn.BatchNorm2d(cout), nn.ReLU(inplace=True),
                         nn.Conv2d(cout, cout, 3, 1, 1, bias=False), nn.BatchNorm2d(cout), nn.ReLU(inplace=True))


class Net(nn.Module):
    def __init__(self):
        super().__init__()
        self.d1, self.d2, self.d3, self.d4 = _block(3, 16, 2), _block(16, 32, 2), _block(32, 64, 2), _block(64, 96, 2)
        self.u3, self.u2 = _block(96 + 64, 64), _block(64 + 32, 48)
        self.heat, self.off = nn.Conv2d(48, len(CLASSES), 1), nn.Conv2d(48, 2, 1)
        nn.init.constant_(self.heat.bias, -2.19)  # start from "nothing here": a 10% prior

    def forward(self, x):
        x2 = self.d2(self.d1(x))
        x3 = self.d3(x2)
        x4 = self.d4(x3)
        y = self.u3(torch.cat([F.interpolate(x4, size=x3.shape[-2:], mode="nearest"), x3], 1))
        y = self.u2(torch.cat([F.interpolate(y, size=x2.shape[-2:], mode="nearest"), x2], 1))
        return self.heat(y), self.off(y)


def to_tensor(imgs: np.ndarray) -> torch.Tensor:
    """(n, h, w, 3) BGR uint8 -> (n, 3, h, w) float, roughly centred."""
    return torch.from_numpy(imgs).permute(0, 3, 1, 2).float().div_(255.0).sub_(0.45).div_(0.25)


def targets(labels: list[dict], h: int, w: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Gaussian heatmaps per class, offsets and a mask of keypoint cells, at the output resolution."""
    oh, ow = h // STRIDE, w // STRIDE
    heat = np.zeros((len(CLASSES), oh, ow), np.float32)
    off = np.zeros((2, oh, ow), np.float32)
    mask = np.zeros((oh, ow), np.float32)
    yy, xx = np.mgrid[0:oh, 0:ow]
    for lab in labels:
        cx, cy = lab["u"] / STRIDE, lab["v"] / STRIDE
        ix, iy = int(cx), int(cy)
        if not (0 <= ix < ow and 0 <= iy < oh):
            continue
        k = lab["kind"]
        sigma = 0.8 if k == 2 else max(0.8, lab["h"] / STRIDE / 6)
        g = np.exp(-((xx - ix) ** 2 + (yy - iy) ** 2) / (2 * sigma * sigma))
        heat[k] = np.maximum(heat[k], g)
        off[:, iy, ix] = (cx - ix, cy - iy)
        mask[iy, ix] = 1.0
    return heat, off, mask


def loss(heat_logits, off_pred, heat_gt, off_gt, mask):
    """Penalty-reduced focal loss on the heatmaps (CenterNet), L1 on the offsets at keypoints."""
    p = torch.sigmoid(heat_logits).clamp(1e-4, 1 - 1e-4)
    pos = heat_gt.eq(1).float()
    neg = 1 - pos
    pos_loss = -(torch.log(p) * (1 - p) ** 2 * pos).sum()
    neg_loss = -(torch.log(1 - p) * p ** 2 * (1 - heat_gt) ** 4 * neg).sum()
    n = pos.sum().clamp(min=1)
    l1 = (torch.abs(off_pred - off_gt) * mask.unsqueeze(1)).sum() / mask.sum().clamp(min=1)
    return (pos_loss + neg_loss) / n + l1


def detect(net: Net, imgs: np.ndarray, max_players: int = 30) -> list[list[tuple[int, float, float, float]]]:
    """Per frame: (class, u, v, score) for every peak above its class threshold, in input pixels."""
    with torch.no_grad():
        heat, off = net(to_tensor(imgs))
        heat = torch.sigmoid(heat)
        peaks = (heat == F.max_pool2d(heat, 3, 1, 1)).float() * heat
    out = []
    for b in range(peaks.shape[0]):
        found = []
        for k in range(len(CLASSES)):
            ys, xs = torch.nonzero(peaks[b, k] > THRESHOLD[k], as_tuple=True)
            for y, x in zip(ys.tolist(), xs.tolist()):
                found.append((k, (x + float(off[b, 0, y, x])) * STRIDE, (y + float(off[b, 1, y, x])) * STRIDE,
                              float(peaks[b, k, y, x])))
        players = sorted((f for f in found if f[0] < 2), key=lambda f: -f[3])[:max_players]
        balls = sorted((f for f in found if f[0] == 2), key=lambda f: -f[3])[:1]
        out.append(players + balls)
    return out
