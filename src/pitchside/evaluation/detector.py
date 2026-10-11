"""Choose and score the tactical-shift detector against the engine's truth log.

Three disjoint sets of simulated matches:

* noise (no hidden changes): how far each candidate signal drifts on its own
  between the last 10 minutes and the 20 before;
* selection (scripted): how far each signal moves after each kind of change.
  For every cause, the signals that move most relative to their noise are
  kept. A cause's score adds up its signals' standardized moves, and the
  score's threshold is its 99th percentile on the noise matches;
* test (scripted, never used for choices): recall, whether the first alert
  names the true cause, false alarms per match and delay.
"""

from __future__ import annotations

import itertools
import math
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from ..league import build_league
from ..sim.engine import MatchEngine
from ..sim.tactics import LABELS
from ..stats.engine import BASELINE, MINUTE, RECENT, StatsEngine

SIGNALS = list(StatsEngine.SIGNALS)
MATCH_WINDOW = 12 * MINUTE  # an alert counts if it lands within 12 minutes of the change
MIN_SNR = 0.8               # a signal must move by at least 0.8 noise standard deviations to be kept
KEEP_PER_CAUSE = 3
SCORE_PCT = 99              # a cause's threshold: this percentile of its score on unscripted matches


def _fixtures(seeds: list[int]) -> list[tuple[str, str, int]]:
    pairs = itertools.cycle(itertools.permutations(build_league(), 2))
    return [(*next(pairs), s) for s in seeds]


def _play(home: str, away: str, seed: int, scripted: bool, detector: dict | None = None) -> dict:
    """Play one match live; record per-minute drifts and each truth change's effect."""
    league = build_league()
    engine = MatchEngine(league[home], league[away], seed=seed, match_id=f"ev{seed}", scripted=scripted)
    stats = StatsEngine(engine.meta(), detector=detector)
    vectors: list[dict[str, float]] = []  # one per team per minute: every signal's drift together
    changes: list[dict] = []
    seen = 0
    next_min = RECENT + BASELINE
    probe = RECENT + MINUTE  # read the effect once the recent window lies wholly after the change
    for events, frame in engine.run():
        stats.feed_frame(frame)
        for ev in events:
            stats.feed_event(ev)
        t = stats.t
        while seen < len(engine.truth):
            rec = engine.truth[seen]
            seen += 1
            changes.append({"label": rec.label, "team": rec.team_id, "t": t,
                            "base": stats.window_metrics(t - BASELINE, t), "after": None})
        for ch in changes:
            if ch["after"] is None and t >= ch["t"] + probe:
                ch["after"] = stats.window_metrics(t - RECENT, t)
        if not scripted and t >= next_min:
            next_min += MINUTE
            cur = stats.window_metrics(t - RECENT, t)
            base = stats.window_metrics(t - RECENT - BASELINE, t - RECENT)
            for tid in stats.team_ids:
                vectors.append({s: cur[tid][s] - base[tid][s] for s in SIGNALS
                                if cur[tid].get(s) is not None and base[tid].get(s) is not None})
    shifts = [{"t": m.t, "team": m.team_id, "cause": m.metrics.get("rule_cause"), "score": m.metrics.get("score")}
              for m in stats.moments if m.kind == "tactical_shift"]
    return {"vectors": vectors, "changes": changes, "shifts": shifts}


def _run(fixtures, scripted, workers, detector=None):
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_play, *zip(*fixtures), itertools.repeat(scripted), itertools.repeat(detector)))


def noise_profile(seeds: list[int], workers: int = 4) -> tuple[dict, list[dict]]:
    vectors = [v for r in _run(_fixtures(seeds), False, workers) for v in r["vectors"]]
    out = {}
    for s in SIGNALS:
        v = np.array([x[s] for x in vectors if s in x])
        if len(v):
            out[s] = {"n": int(len(v)), "sd": round(float(v.std()), 4),
                      "p99_abs": round(float(np.percentile(np.abs(v), 99)), 4)}
    return out, vectors


def effect_profile(seeds: list[int], workers: int = 4) -> dict:
    """Mean change of every signal for the changing team, by kind of change."""
    eff: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in _run(_fixtures(seeds), True, workers):
        for ch in r["changes"]:
            if ch["after"] is None:
                continue
            for s in SIGNALS:
                a, b = ch["base"][ch["team"]].get(s), ch["after"][ch["team"]].get(s)
                if a is not None and b is not None:
                    eff[ch["label"]][s].append(b - a)
    return {lab: {s: {"mean": round(float(np.mean(v)), 4), "n": len(v)} for s, v in sorted(sig.items())}
            for lab, sig in sorted(eff.items())}


def _cause_score(sigs: list[list], sd: dict[str, float], drift: dict[str, float]) -> float | None:
    parts = [(1.0 if d == "up" else -1.0) * drift[s] / sd[s] for s, d, *_ in sigs if s in drift and sd.get(s)]
    return sum(parts) / math.sqrt(len(parts)) if parts else None


def choose(noise: dict, vectors: list[dict], effects: dict) -> dict:
    """For each cause keep the signals that move most relative to their noise, then set score thresholds."""
    causes: dict[str, list[list]] = {}
    for label in LABELS:
        ranked = []
        for s, e in effects.get(label, {}).items():
            sd = noise.get(s, {}).get("sd")
            if sd:
                snr = e["mean"] / sd
                if abs(snr) >= MIN_SNR:
                    ranked.append([s, "up" if snr > 0 else "down", round(abs(snr), 2)])
        ranked.sort(key=lambda r: -r[2])
        if ranked:
            causes[label] = ranked[:KEEP_PER_CAUSE]
    sd = {s: noise[s]["sd"] for sigs in causes.values() for s, *_ in sigs}
    score_thresholds = {}
    for label, sigs in causes.items():
        scores = [x for x in (_cause_score(sigs, sd, v) for v in vectors) if x is not None]
        score_thresholds[label] = round(float(np.percentile(scores, SCORE_PCT)), 3) if scores else 3.0
    return {"mode": "cause", "causes": causes, "sd": sd, "score_thresholds": score_thresholds,
            "windows_min": {"recent": RECENT // MINUTE, "baseline": BASELINE // MINUTE}}


def detection(seeds: list[int], config: dict, workers: int = 4) -> dict:
    runs = _run(_fixtures(seeds), True, workers, config)
    found, right, delays, total = 0, 0, [], 0
    alerts, matched = 0, 0
    per_change = []
    for r in runs:
        for ch in r["changes"]:
            total += 1
            near = sorted((sh for sh in r["shifts"] if sh["team"] == ch["team"]
                           and 0 <= sh["t"] - ch["t"] <= MATCH_WINDOW), key=lambda sh: sh["t"])
            hit = next((sh for sh in near if sh["cause"] == ch["label"]), None)
            if hit:
                found += 1
                delays.append((hit["t"] - ch["t"]) / MINUTE)
            right += bool(near) and near[0]["cause"] == ch["label"]
            per_change.append({"label": ch["label"], "team": ch["team"], "minute": round(ch["t"] / MINUTE, 1),
                               "detected_right_cause": bool(hit), "first_alert": near[0]["cause"] if near else None})
        for sh in r["shifts"]:
            alerts += 1
            matched += any(sh["team"] == ch["team"] and 0 <= sh["t"] - ch["t"] <= MATCH_WINDOW
                           for ch in r["changes"])
    n = len(runs)
    return {
        "matches": n,
        "changes": total,
        "recall_right_cause": round(found / total, 3) if total else None,
        "first_alert_right_cause": round(right / total, 3) if total else None,
        "median_delay_min": round(float(np.median(delays)), 1) if delays else None,
        "alerts": alerts,
        "false_alarms_per_match": round((alerts - matched) / n, 2),
        "alert_precision": round(matched / alerts, 3) if alerts else None,
        "per_change": per_change,
    }
