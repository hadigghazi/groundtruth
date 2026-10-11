"""The learned detector's runtime: features from window drifts, probabilities per cause.

Trained by `groundtruth learn-detector` (evaluation/learned.py), which writes the weights
next to this file as learned.json. The stats engine uses it when it is there.
"""

from __future__ import annotations

import numpy as np


def drifts(base: dict, cur: dict, tid: str, opp: str, signals) -> dict[str, float]:
    """How far each signal moved between the earlier and the recent window, for the team and its opponent."""
    out = {}
    for who, t in (("own", tid), ("opp", opp)):
        for s in signals:
            a, b = base[t].get(s), cur[t].get(s)
            if a is not None and b is not None:
                out[f"{who}.{s}"] = b - a
    return out


def features(d: dict[str, float], sd: dict[str, float], signals) -> np.ndarray:
    """Standardized drifts, own then opponent's, with a bias term; a missing signal counts as no move."""
    x = [1.0]
    for who in ("own", "opp"):
        for s in signals:
            v, spread = d.get(f"{who}.{s}"), sd.get(s)
            x.append(float(np.clip(v / spread, -6, 6)) if v is not None and spread else 0.0)
    return np.array(x)


def probs(W: np.ndarray, x: np.ndarray) -> np.ndarray:
    z = W @ x
    z = z - z.max()
    p = np.exp(z)
    return p / p.sum()
