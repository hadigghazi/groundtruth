"""Fit the pass-difficulty and xG models on simulated training matches.

Training and test matches use disjoint seeds, so the reported log loss and
calibration are on matches the models never saw.
"""

from __future__ import annotations

import itertools
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from ..league import build_league
from ..sim.io import simulate
from .features import PASS_FEATURES, SHOT_FEATURES, pass_features, shot_features
from .models import COEF_FILE, calibration_bins, fit_logistic, log_loss


def match_samples(home: str, away: str, seed: int) -> dict[str, list]:
    league = build_league()
    engine, events, frames = simulate(league[home], league[away], seed=seed, match_id=f"fit{seed}")
    meta = engine.meta()
    team_of = {p.player_id: t.team_id for t in (meta.home, meta.away) for p in t.lineup + t.bench}
    by_time = {(f.period, f.clock_ms): f for f in frames}
    direction = {int(k): v for k, v in meta.direction.items()}
    out = {"pass_X": [], "pass_y": [], "shot_X": [], "shot_y": []}
    for ev in events:
        if ev.type not in ("pass", "shot") or (ev.type == "shot" and ev.restart == "penalty"):
            continue
        f = by_time.get((ev.period, ev.clock_ms))
        if f is None:
            continue
        opp = np.array([xy for pid, xy in zip(f.ids, f.xy) if team_of.get(pid) != ev.team_id], dtype=float)
        d = direction[ev.period][ev.team_id]
        if ev.type == "pass":
            x = pass_features(ev, d, opp)
            out["pass_X"].append([x[k] for k in PASS_FEATURES])
            out["pass_y"].append(1.0 if ev.outcome == "complete" else 0.0)
        else:
            x = shot_features(ev, d, opp)
            out["shot_X"].append([x[k] for k in SHOT_FEATURES])
            out["shot_y"].append(1.0 if ev.outcome == "goal" else 0.0)
    return out


def _collect(fixtures: list[tuple[str, str, int]], workers: int) -> dict[str, np.ndarray]:
    with ProcessPoolExecutor(max_workers=workers) as pool:
        parts = list(pool.map(match_samples, *zip(*fixtures)))
    return {k: np.array([row for p in parts for row in p[k]], dtype=float) for k in parts[0]}


def fixtures(n: int, seed0: int) -> list[tuple[str, str, int]]:
    pairs = itertools.cycle(itertools.permutations(build_league(), 2))
    return [(*next(pairs), seed0 + i) for i in range(n)]


def fit_models(train: int, test: int, seed0: int, workers: int = 4, write: Path | None = None) -> dict:
    tr = _collect(fixtures(train, seed0), workers)
    te = _collect(fixtures(test, seed0 + 10_000), workers)
    models, report = {}, {}
    for name, feats in (("pass", PASS_FEATURES), ("shot", SHOT_FEATURES)):
        m = fit_logistic(tr[f"{name}_X"], tr[f"{name}_y"], feats)
        p = m.prob_matrix(te[f"{name}_X"])
        y = te[f"{name}_y"]
        base = np.full_like(y, tr[f"{name}_y"].mean())
        models[name] = m.to_dict()
        report[name] = {
            "train_n": int(len(tr[f"{name}_y"])), "test_n": int(len(y)),
            "test_rate": round(float(y.mean()), 4),
            "test_log_loss": round(log_loss(p, y), 4),
            "test_log_loss_base_rate": round(log_loss(base, y), 4),
            "calibration": calibration_bins(p, y, bins=8),
            "coef": {k: round(c, 3) for k, c in zip(["intercept"] + feats, m.coef)},
        }
    if write:
        Path(write).write_text(json.dumps(models, indent=2), encoding="utf-8")
    return {"models": models, "report": report}


DEFAULT_COEF_PATH = Path(__file__).with_name(COEF_FILE)
