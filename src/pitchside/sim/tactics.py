"""Hidden tactical settings and the changes that move them.

Every setting runs from 0 to 1. Downstream stages never see these numbers;
they only see their effects in the event and position streams, which is
what makes an explanation checkable against the truth log.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace

import numpy as np


@dataclass(frozen=True)
class Tactics:
    press_intensity: float  # how many players press, how hard, and how far up the pitch
    line_height: float      # how high the block sits
    directness: float       # appetite for forward and long passes, and for runs in behind
    tempo: float            # how quickly the ball carrier acts
    width: float            # how wide the team stretches in possession

    def as_dict(self) -> dict[str, float]:
        return {k: round(v, 3) for k, v in asdict(self).items()}


# Each label is a set of deltas; results are clamped to [0.05, 0.95].
LABELS: dict[str, dict[str, float]] = {
    "high_press": {"press_intensity": 0.40, "line_height": 0.30},
    "low_block": {"press_intensity": -0.35, "line_height": -0.35},
    "go_direct": {"directness": 0.40, "tempo": 0.30},
    "slow_down": {"tempo": -0.35, "directness": -0.25},
    "push_wide": {"width": 0.40},
}


def apply_label(t: Tactics, label: str) -> Tactics:
    deltas = LABELS[label]
    return replace(t, **{k: float(np.clip(getattr(t, k) + d, 0.05, 0.95)) for k, d in deltas.items()})


@dataclass(frozen=True)
class ScheduledChange:
    period: int
    clock_ms: int
    team: int  # 0 = home, 1 = away
    label: str


def _useful(t: Tactics, label: str) -> bool:
    """A change only counts if it moves at least one setting by 0.15 or more."""
    after = apply_label(t, label)
    return max(abs(getattr(after, k) - getattr(t, k)) for k in LABELS[label]) >= 0.15


def make_script(rng: np.random.Generator, tactics: list[Tactics]) -> list[ScheduledChange]:
    """Two or three hidden shifts per match: one in the first half, one or two in the second."""
    current = list(tactics)
    windows = [(1, 25, 38), (2, 52, 64)]
    if rng.random() < 0.6:
        windows.append((2, 68, 80))
    script = []
    for period, lo, hi in windows:
        team = int(rng.integers(0, 2))
        options = [lab for lab in LABELS if _useful(current[team], lab)]
        if not options:
            team = 1 - team
            options = [lab for lab in LABELS if _useful(current[team], lab)]
        label = str(rng.choice(options))
        minute = float(rng.uniform(lo, hi))
        script.append(ScheduledChange(period, int(minute * 60_000), team, label))
        current[team] = apply_label(current[team], label)
    return script
