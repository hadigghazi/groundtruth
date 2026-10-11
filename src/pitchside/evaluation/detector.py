"""Score the tactical-shift detector against the engine's truth log.

Three measurements, each over simulated matches:

* noise: on matches with no hidden changes, how far each signal drifts on its
  own between the last 5 minutes and the 15 before (this sets thresholds);
* effect: on scripted matches, how far each signal moves after each kind of
  change (this shows which signals carry which cause);
* detection: recall, false alarms and delay of the live detector, plus how
  often a plain rule (signal -> cause) names the right cause.
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from ..league import build_league
from ..sim.engine import MatchEngine
from ..stats.engine import BASELINE, MINUTE, RECENT, StatsEngine

# Which own-team signals each hidden change should move, and which way.
EXPECTED: dict[str, list[tuple[str, str]]] = {
    "high_press": [("ppda", "down"), ("block_height_m", "up")],
    "low_block": [("ppda", "up"), ("block_height_m", "down")],
    "go_direct": [("long_pass_share", "up"), ("passes_per_min_in_possession", "up")],
    "slow_down": [("passes_per_min_in_possession", "down"), ("long_pass_share", "down")],
    "push_wide": [("width_m", "up")],
}
RULE: dict[tuple[str, str], str] = {sig: label for label, sigs in EXPECTED.items() for sig in sigs}
SIGNALS = sorted({s for sigs in EXPECTED.values() for s, _ in sigs})
MATCH_WINDOW = 12 * MINUTE  # a detection counts if it lands within 12 minutes of the change


def _fixtures(seeds: list[int]) -> list[tuple[str, str, int]]:
    pairs = itertools.cycle(itertools.permutations(build_league(), 2))
    return [(*next(pairs), s) for s in seeds]


def _play(home: str, away: str, seed: int, scripted: bool, probe_after: int = 6 * MINUTE) -> dict:
    """Play one match live. Records every minute's signal deltas and each truth change's effect."""
    league = build_league()
    engine = MatchEngine(league[home], league[away], seed=seed, match_id=f"ev{seed}", scripted=scripted)
    stats = StatsEngine(engine.meta())
    deltas: dict[str, list[float]] = defaultdict(list)
    changes: list[dict] = []
    seen = 0
    next_min = RECENT + BASELINE
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
            if ch["after"] is None and t >= ch["t"] + probe_after:
                ch["after"] = stats.window_metrics(t - RECENT, t)
        if not scripted and t >= next_min:
            next_min += MINUTE
            cur = stats.window_metrics(t - RECENT, t)
            base = stats.window_metrics(t - RECENT - BASELINE, t - RECENT)
            for tid in stats.team_ids:
                for s in SIGNALS:
                    a, b = base[tid][s], cur[tid][s]
                    if a is not None and b is not None:
                        deltas[s].append(b - a)
    shifts = [{"t": m.t, "team": m.team_id, "signal": m.metrics["signal"], "direction": m.metrics["direction"],
               "before": m.metrics["before"], "after": m.metrics["after"]}
              for m in stats.moments if m.kind == "tactical_shift"]
    return {"deltas": dict(deltas), "changes": changes, "shifts": shifts}


def _run(fixtures, scripted, workers):
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_play, *zip(*fixtures), itertools.repeat(scripted)))


def noise_profile(seeds: list[int], workers: int = 4) -> dict:
    runs = _run(_fixtures(seeds), False, workers)
    pooled: dict[str, list[float]] = defaultdict(list)
    for r in runs:
        for s, ds in r["deltas"].items():
            pooled[s].extend(ds)
    return {s: {"n": len(v), "p90_abs": round(float(np.percentile(np.abs(v), 90)), 3),
                "p95_abs": round(float(np.percentile(np.abs(v), 95)), 3),
                "p99_abs": round(float(np.percentile(np.abs(v), 99)), 3)} for s, v in sorted(pooled.items())}


def effect_and_detection(seeds: list[int], workers: int = 4) -> dict:
    runs = _run(_fixtures(seeds), True, workers)
    effects: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    found, rule_right, delays, total = 0, 0, [], 0
    shifts_total, shifts_matched = 0, 0
    per_change = []
    for r in runs:
        for ch in r["changes"]:
            total += 1
            if ch["after"] is not None:
                for s in SIGNALS:
                    a, b = ch["base"][ch["team"]][s], ch["after"][ch["team"]][s]
                    if a is not None and b is not None:
                        effects[ch["label"]][s].append(b - a)
            hits = [sh for sh in r["shifts"] if sh["team"] == ch["team"] and 0 <= sh["t"] - ch["t"] <= MATCH_WINDOW
                    and (sh["signal"], sh["direction"]) in EXPECTED[ch["label"]]]
            first = min(hits, key=lambda sh: sh["t"]) if hits else None
            if first:
                found += 1
                delays.append((first["t"] - ch["t"]) / MINUTE)
                rule_right += RULE[(first["signal"], first["direction"])] == ch["label"]
            per_change.append({"label": ch["label"], "team": ch["team"], "minute": round(ch["t"] / MINUTE, 1),
                               "detected": bool(first),
                               "delay_min": round((first["t"] - ch["t"]) / MINUTE, 1) if first else None})
        for sh in r["shifts"]:
            shifts_total += 1
            shifts_matched += any(sh["team"] == ch["team"] and 0 <= sh["t"] - ch["t"] <= MATCH_WINDOW
                                  for ch in r["changes"])
    return {
        "changes": total,
        "recall": round(found / total, 3) if total else None,
        "median_delay_min": round(float(np.median(delays)), 1) if delays else None,
        "rule_cause_accuracy": round(rule_right / found, 3) if found else None,
        "shift_moments": shifts_total,
        "shift_precision": round(shifts_matched / shifts_total, 3) if shifts_total else None,
        "mean_effect": {lab: {s: round(float(np.mean(v)), 3) for s, v in sorted(sig.items())}
                        for lab, sig in sorted(effects.items())},
        "per_change": per_change,
    }
