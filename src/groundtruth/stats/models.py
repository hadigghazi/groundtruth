"""Logistic models for pass completion and shot conversion.

Fitted by iteratively reweighted least squares on simulated training matches
(`groundtruth fit-models`) and stored as plain coefficients, so serving needs
nothing but numpy.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import numpy as np


@dataclass
class Logistic:
    features: list[str]
    mean: list[float]
    scale: list[float]
    coef: list[float]  # intercept first

    def prob(self, x: dict[str, float]) -> float:
        z = self.coef[0]
        for name, m, s, b in zip(self.features, self.mean, self.scale, self.coef[1:]):
            z += b * (x[name] - m) / s
        return 1.0 / (1.0 + math.exp(-z))

    def prob_matrix(self, X: np.ndarray) -> np.ndarray:
        Z = (X - np.array(self.mean)) / np.array(self.scale)
        return 1.0 / (1.0 + np.exp(-(self.coef[0] + Z @ np.array(self.coef[1:]))))

    def to_dict(self) -> dict:
        return {"features": self.features, "mean": self.mean, "scale": self.scale, "coef": self.coef}


def fit_logistic(X: np.ndarray, y: np.ndarray, features: list[str], l2: float = 1e-3, iters: int = 50) -> Logistic:
    mean = X.mean(axis=0)
    scale = X.std(axis=0)
    scale[scale < 1e-9] = 1.0
    Z = np.column_stack([np.ones(len(X)), (X - mean) / scale])
    w = np.zeros(Z.shape[1])
    reg = l2 * np.eye(Z.shape[1])
    reg[0, 0] = 0.0
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-(Z @ w)))
        r = np.clip(p * (1 - p), 1e-6, None)
        grad = Z.T @ (p - y) + reg @ w
        hess = (Z * r[:, None]).T @ Z + reg
        step = np.linalg.solve(hess, grad)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return Logistic(features, mean.tolist(), scale.tolist(), w.tolist())


def log_loss(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())


def calibration_bins(p: np.ndarray, y: np.ndarray, bins: int = 10) -> list[dict[str, float]]:
    edges = np.quantile(p, np.linspace(0, 1, bins + 1))
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p <= hi)
        if m.any():
            out.append({"predicted": round(float(p[m].mean()), 3), "observed": round(float(y[m].mean()), 3),
                        "n": int(m.sum())})
    return out


COEF_FILE = "coefficients.json"


def load_models(path: Path | None = None) -> dict[str, Logistic]:
    if path is None:
        text = resources.files("groundtruth.stats").joinpath(COEF_FILE).read_text(encoding="utf-8")
    else:
        text = Path(path).read_text(encoding="utf-8")
    raw = json.loads(text)
    return {name: Logistic(**raw[name]) for name in ("pass", "shot")}
