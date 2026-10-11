"""A learned tactical-shift detector, trained on synthetic matches whose causes we know.

The engine labels its own data: every hidden change has a team, a cause and a moment it
took effect. So instead of hand-picking signals and thresholds, a softmax classifier learns
from many matches which pattern of drifts (the team's own, and its opponent's) means which
cause, or no change at all. The opponent's drifts are what let it tell "they dropped deeper"
from "they were pushed back by a press".

Four disjoint seed sets, as for the rule:
* noise (no hidden changes): the spread of each signal's drift, used to standardize;
* train (scripted): the classifier;
* select (scripted): the alert threshold, the best right-cause rate within a false-alarm budget;
* test (scripted, the rule's own test seeds): the comparison with the rule.

Per team, per minute from the 30th: p(none), p(high_press), ... An alert fires when one
cause stays most likely above the threshold for two checks running, at most once per team
per 10 minutes, like the rule.
"""

from __future__ import annotations

import itertools
import json
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from ..league import build_league
from ..sim.engine import MatchEngine
from ..sim.tactics import LABELS
from ..stats.engine import BASELINE, MINUTE, RECENT, StatsEngine
from ..stats.learned import drifts as _drifts
from ..stats.learned import features as _features
from ..stats.learned import probs

SIGNALS = list(StatsEngine.SIGNALS)
CLASSES = ["none", *LABELS]
POS_FROM, POS_TO = 4 * MINUTE, 14 * MINUTE   # a change is "recent" from 4 to 14 minutes after it
SKIP_TO = 30 * MINUTE                        # after that, the windows still lean: not trained on
MATCH_WINDOW = 12 * MINUTE                   # an alert counts for a change within 12 minutes, as for the rule
FALSE_ALARM_BUDGET = 1.5                     # per match, on the selection seeds


def _fixtures(seeds):
    pairs = itertools.cycle(itertools.permutations(build_league(), 2))
    return [(*next(pairs), s) for s in seeds]


def drifts(base: dict, cur: dict, tid: str, opp: str) -> dict[str, float]:
    return _drifts(base, cur, tid, opp, SIGNALS)


def features(d: dict[str, float], sd: dict[str, float]):
    return _features(d, sd, SIGNALS)


def _play(home: str, away: str, seed: int, scripted: bool) -> dict:
    """One match: every team-minute's drifts from the 30th minute on, and when each hidden change took effect."""
    league = build_league()
    engine = MatchEngine(league[home], league[away], seed=seed, match_id=f"ld{seed}", scripted=scripted)
    stats = StatsEngine(engine.meta(), detector={"thresholds": {}})  # the detector itself stays quiet here
    rows, changes, seen, next_min = [], [], 0, RECENT + BASELINE
    for events, frame in engine.run():
        t = stats.t
        while seen < len(engine.truth):
            rec = engine.truth[seen]
            seen += 1
            changes.append({"t": t, "team": rec.team_id, "label": rec.label})
        stats.feed_frame(frame)
        for ev in events:
            stats.feed_event(ev)
        if stats.t >= next_min:
            next_min += MINUTE
            now = stats.t
            cur = stats.window_metrics(now - RECENT, now)
            base = stats.window_metrics(now - RECENT - BASELINE, now - RECENT)
            h, a = stats.team_ids
            for tid, opp in ((h, a), (a, h)):
                rows.append({"t": now, "team": tid, "d": drifts(base, cur, tid, opp)})
    return {"rows": rows, "changes": changes}


def _run(fixtures, scripted, workers):
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_play, *zip(*fixtures), itertools.repeat(scripted)))


def label(row: dict, changes: list[dict]) -> str | None:
    """The row's class: a cause if its team changed 4 to 14 minutes ago, none if not in 30, else unlabelled."""
    mine = [c for c in changes if c["team"] == row["team"] and c["t"] <= row["t"]]
    if not mine:
        return "none"
    age = row["t"] - mine[-1]["t"]
    if POS_FROM <= age <= POS_TO:
        return mine[-1]["label"]
    return None if age < SKIP_TO else "none"


def fit_softmax(X: np.ndarray, y: np.ndarray, k: int, l2: float = 1e-2, steps: int = 3000, lr: float = 0.3) -> np.ndarray:
    """Multinomial logistic regression by gradient descent with Adam; the bias is not penalized."""
    n, d = X.shape
    W = np.zeros((k, d))
    m, v = np.zeros_like(W), np.zeros_like(W)
    Y = np.eye(k)[y]
    mask = np.ones_like(W)
    mask[:, 0] = 0.0
    for step in range(1, steps + 1):
        Z = X @ W.T
        Z -= Z.max(axis=1, keepdims=True)
        P = np.exp(Z)
        P /= P.sum(axis=1, keepdims=True)
        g = (P - Y).T @ X / n + l2 * W * mask
        m = 0.9 * m + 0.1 * g
        v = 0.999 * v + 0.001 * g * g
        W -= lr * (m / (1 - 0.9 ** step)) / (np.sqrt(v / (1 - 0.999 ** step)) + 1e-8)
    return W


def alerts(run: dict, W: np.ndarray, sd: dict, tau: float) -> list[dict]:
    """Replays the alert rule over one match's team-minutes."""
    out, pending, last = [], {}, {}
    for row in run["rows"]:
        p = probs(W, features(row["d"], sd))
        c = int(np.argmax(p[1:])) + 1
        team = row["team"]
        if p[c] < tau:
            pending.pop(team, None)
            continue
        if pending.get(team) != c:
            pending[team] = c
            continue
        if row["t"] - last.get(team, -10**9) < 10 * MINUTE:
            continue
        last[team] = row["t"]
        pending.pop(team, None)
        out.append({"t": row["t"], "team": team, "cause": CLASSES[c], "p": float(p[c])})
    return out


def score(runs: list[dict], W: np.ndarray, sd: dict, tau: float) -> dict:
    found = right_first = total = n_alerts = matched = 0
    delays = []
    for r in runs:
        al = alerts(r, W, sd, tau)
        for ch in r["changes"]:
            total += 1
            near = [a for a in al if a["team"] == ch["team"] and 0 <= a["t"] - ch["t"] <= MATCH_WINDOW]
            hit = next((a for a in near if a["cause"] == ch["label"]), None)
            if hit:
                found += 1
                delays.append((hit["t"] - ch["t"]) / MINUTE)
            right_first += bool(near) and near[0]["cause"] == ch["label"]
        n_alerts += len(al)
        matched += sum(any(a["team"] == ch["team"] and 0 <= a["t"] - ch["t"] <= MATCH_WINDOW for ch in r["changes"])
                       for a in al)
    n = len(runs)
    return {"matches": n, "changes": total, "recall_right_cause": round(found / total, 3) if total else None,
            "first_alert_right_cause": round(right_first / total, 3) if total else None,
            "median_delay_min": round(float(np.median(delays)), 1) if delays else None,
            "false_alarms_per_match": round((n_alerts - matched) / n, 2), "alerts": n_alerts}


def learn(noise_seeds, train_seeds, select_seeds, test_seeds, workers: int = 4) -> dict:
    noise = _run(_fixtures(noise_seeds), False, workers)
    sd = {}
    for s in SIGNALS:
        v = np.array([row["d"][f"own.{s}"] for r in noise for row in r["rows"] if f"own.{s}" in row["d"]])
        if len(v) > 10:
            sd[s] = round(float(v.std()), 4)
    train = _run(_fixtures(train_seeds), True, workers) + noise
    X, y = [], []
    for r in train:
        for row in r["rows"]:
            lab = label(row, r["changes"])
            if lab is not None:
                X.append(features(row["d"], sd))
                y.append(CLASSES.index(lab))
    X, y = np.array(X), np.array(y)
    W = fit_softmax(X, y, len(CLASSES))
    P = np.array([probs(W, x) for x in X])
    train_loss = float(-np.mean(np.log(P[np.arange(len(y)), y] + 1e-12)))
    select = _run(_fixtures(select_seeds), True, workers)
    curve = [{"tau": tau, **score(select, W, sd, tau)} for tau in np.round(np.arange(0.30, 0.96, 0.05), 2)]
    within = [c for c in curve if c["false_alarms_per_match"] <= FALSE_ALARM_BUDGET] or curve
    best = max(within, key=lambda c: (c["recall_right_cause"] or 0, -c["false_alarms_per_match"]))
    tau = float(best["tau"])
    test = _run(_fixtures(test_seeds), True, workers)
    model = {"mode": "learned", "classes": CLASSES, "signals": SIGNALS, "sd": sd, "tau": tau,
             "W": np.round(W, 5).tolist(), "hold_checks": 2, "gap_min": 10,
             "seeds": {"noise": list(noise_seeds), "train": list(train_seeds), "select": list(select_seeds),
                       "test": list(test_seeds)}}
    return {"model": model, "train": {"samples": int(len(y)), "by_class": {c: int((y == i).sum()) for i, c in enumerate(CLASSES)},
                                      "log_loss": round(train_loss, 4)},
            "select_curve": curve, "chosen_tau": tau, "test": score(test, W, sd, tau)}


def write(model: dict, path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(model, f, indent=1)

