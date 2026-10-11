"""Choose and score the tactical-shift detector against the engine's truth log.

Three disjoint sets of simulated matches:

* noise (no hidden changes): how far each candidate signal drifts on its own
  between the last 8 minutes and the 20 before;
* selection (scripted): how far each signal moves after each kind of change.
  For every change, the signals with the best effect-to-noise ratio are kept,
  and each kept signal gets a threshold from the noise;
* test (scripted, never used for choices): recall, false alarms and delay of
  the live detector, and how often the plain rule (signal moved this way ->
  this cause) names the true cause.
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from ..league import build_league
from ..sim.engine import MatchEngine
from ..sim.tactics import LABELS
from ..stats.engine import BASELINE, MINUTE, RECENT, StatsEngine

SIGNALS = list(StatsEngine.SIGNALS)
MATCH_WINDOW = 12 * MINUTE  # a detection counts if it lands within 12 minutes of the change
MIN_SNR = 0.8               # a signal must move by at least 0.8 noise standard deviations to be kept
KEEP_PER_CAUSE = 2
THRESHOLD_PCT = 97          # threshold = this percentile of |drift| on unscripted matches


def _fixtures(seeds: list[int]) -> list[tuple[str, str, int]]:
    pairs = itertools.cycle(itertools.permutations(build_league(), 2))
    return [(*next(pairs), s) for s in seeds]


def _play(home: str, away: str, seed: int, scripted: bool, thresholds: dict | None = None) -> dict:
    """Play one match live; record per-minute drifts and each truth change's effect."""
    league = build_league()
    engine = MatchEngine(league[home], league[away], seed=seed, match_id=f"ev{seed}", scripted=scripted)
    stats = StatsEngine(engine.meta(), thresholds=thresholds)
    drifts: dict[str, list[float]] = defaultdict(list)
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
                for s in SIGNALS:
                    a, b = base[tid].get(s), cur[tid].get(s)
                    if a is not None and b is not None:
                        drifts[s].append(b - a)
    shifts = [{"t": m.t, "team": m.team_id, "signal": m.metrics["signal"], "direction": m.metrics["direction"]}
              for m in stats.moments if m.kind == "tactical_shift"]
    return {"drifts": dict(drifts), "changes": changes, "shifts": shifts}


def _run(fixtures, scripted, workers, thresholds=None):
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_play, *zip(*fixtures), itertools.repeat(scripted), itertools.repeat(thresholds)))


def noise_profile(seeds: list[int], workers: int = 4) -> dict:
    pooled: dict[str, list[float]] = defaultdict(list)
    for r in _run(_fixtures(seeds), False, workers):
        for s, ds in r["drifts"].items():
            pooled[s].extend(ds)
    out = {}
    for s, v in sorted(pooled.items()):
        a = np.abs(np.array(v))
        out[s] = {"n": len(v), "sd": round(float(np.std(v)), 4),
                  **{f"p{p}_abs": round(float(np.percentile(a, p)), 4) for p in (90, 95, 97, 99)}}
    return out


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


def choose(noise: dict, effects: dict) -> dict:
    """Keep, for each cause, the signals that move most relative to their noise."""
    expected: dict[str, list[tuple[str, str, float]]] = {}
    for label in LABELS:
        ranked = []
        for s, e in effects.get(label, {}).items():
            sd = noise.get(s, {}).get("sd")
            if sd:
                snr = e["mean"] / sd
                if abs(snr) >= MIN_SNR:
                    ranked.append((s, "up" if snr > 0 else "down", round(abs(snr), 2)))
        ranked.sort(key=lambda r: -r[2])
        expected[label] = ranked[:KEEP_PER_CAUSE]
    cause_map: dict[str, tuple[str, float]] = {}
    for label, sigs in expected.items():
        for s, d, snr in sigs:
            key = f"{s}|{d}"
            if key not in cause_map or snr > cause_map[key][1]:
                cause_map[key] = (label, snr)
    used = sorted({s for sigs in expected.values() for s, _, _ in sigs})
    thresholds = {s: noise[s][f"p{THRESHOLD_PCT}_abs"] for s in used}
    return {"thresholds": thresholds, "cause_map": {k: v[0] for k, v in sorted(cause_map.items())},
            "expected": {lab: [[s, d, snr] for s, d, snr in sigs] for lab, sigs in expected.items()}}


def detection(seeds: list[int], config: dict, workers: int = 4) -> dict:
    expected = {lab: {(s, d) for s, d, _ in sigs} for lab, sigs in config["expected"].items()}
    runs = _run(_fixtures(seeds), True, workers, config["thresholds"])
    found, right, delays, total = 0, 0, [], 0
    shifts_total, shifts_matched = 0, 0
    per_change = []
    for r in runs:
        for ch in r["changes"]:
            total += 1
            near = [sh for sh in r["shifts"] if sh["team"] == ch["team"] and 0 <= sh["t"] - ch["t"] <= MATCH_WINDOW]
            hits = [sh for sh in near if (sh["signal"], sh["direction"]) in expected.get(ch["label"], set())]
            first = min(near, key=lambda sh: sh["t"]) if near else None
            if hits:
                found += 1
                delays.append((min(sh["t"] for sh in hits) - ch["t"]) / MINUTE)
            if first:
                right += config["cause_map"].get(f"{first['signal']}|{first['direction']}") == ch["label"]
            per_change.append({"label": ch["label"], "team": ch["team"], "minute": round(ch["t"] / MINUTE, 1),
                               "detected": bool(hits),
                               "first_signal": first and f"{first['signal']} {first['direction']}"})
        for sh in r["shifts"]:
            shifts_total += 1
            shifts_matched += any(sh["team"] == ch["team"] and 0 <= sh["t"] - ch["t"] <= MATCH_WINDOW
                                  for ch in r["changes"])
    n_matches = len(runs)
    return {
        "matches": n_matches,
        "changes": total,
        "recall": round(found / total, 3) if total else None,
        "median_delay_min": round(float(np.median(delays)), 1) if delays else None,
        "rule_cause_accuracy_on_first_alert": round(right / total, 3) if total else None,
        "shift_moments": shifts_total,
        "false_alarms_per_match": round((shifts_total - shifts_matched) / n_matches, 2),
        "shift_precision": round(shifts_matched / shifts_total, 3) if shifts_total else None,
        "per_change": per_change,
    }
